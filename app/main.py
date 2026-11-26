import secrets
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.errors import failure_details, DatabaseOperationError
from app.settings import public_database_profiles
from app.config import ConnectionConfig
from app.browser import router as browser_router
from app.importer import ImportFailure, convert, engine_for, import_csvs, prepare_database, administration_engine

app = FastAPI(title='NetAct XML → Database', docs_url=None, redoc_url=None)
app.include_router(browser_router)
STATIC = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=STATIC), name='static')
executor = ThreadPoolExecutor(max_workers=1)
active = threading.BoundedSemaphore(1)
jobs = {}
lock = threading.Lock()



def update(job_id, **fields):
    with lock:
        jobs[job_id].update(fields)


def run_job(job_id, config, folder):
    engine = None
    stage = 'xml_to_csv'
    try:
        update(job_id, status='running', progress=10, message='Kiểm tra XML và chuyển đổi bằng JAR…')
        files = convert(folder / 'input.xml', folder / 'csv')
        update(job_id, progress=40, message=f'Đã tạo {len(files)} CSV. Đang kết nối database…')
        stage = 'database_connect'
        engine = prepare_database(config)
        try:
            with engine.connect() as connection:
                connection.execute(text('SELECT 1'))
        except SQLAlchemyError as exc:
            raise DatabaseOperationError('target_connect', exc, config.database,
                                         'created' if config.create_database else 'unchanged') from None
        stage = 'csv_to_database'
        result = import_csvs(engine, files, lambda percent, message: update(job_id, progress=percent, message=message))
        failed_rows = sum(t['rows_failed'] for t in result)
        failed_tables = sum(t['status'] == 'failed_create' for t in result)
        has_errors = failed_rows > 0 or failed_tables > 0
        update(job_id, status='completed_with_errors' if has_errors else 'completed', progress=100,
               message=f'Hoàn tất: {failed_rows:,} dòng lỗi, {failed_tables:,} bảng không tạo được. Xem chi tiết bên dưới.' if has_errors else 'Hoàn tất chuyển đổi và nhập dữ liệu.',
               tables=result, total_rows=sum(t['rows'] for t in result),
               failed_rows=failed_rows, failed_tables=failed_tables)
    except ImportFailure as exc:
        update(job_id, status='failed', stage=stage, message=str(exc))
    except Exception as exc:
        update(job_id, status='failed', **failure_details(exc, stage, job_id))
    finally:
        if engine is not None:
            engine.dispose()
        shutil.rmtree(folder, ignore_errors=True)
        update(job_id, finished_at=time.time())
        active.release()


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Never echo rejected request bodies: they can contain a database password.
    return JSONResponse({'detail': 'Cấu hình không hợp lệ. Kiểm tra loại database, host, port, tên database và TLS.'}, status_code=422)


@app.middleware('http')
async def same_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if request.method == 'POST' and origin and origin != expected:
        return JSONResponse({'detail': 'Yêu cầu khác nguồn không được phép.'}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith('/api/browser/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'java': shutil.which('java') is not None, 'max_upload_mb': None}


@app.get('/api/db-presets')
def db_presets():
    try:
        return JSONResponse(public_database_profiles(), headers={'Cache-Control': 'no-store'})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post('/api/connection')
def test_connection(config: ConnectionConfig):
    # Read-only: use the same login path as import without running DROP/CREATE.
    engine = administration_engine(config) if config.create_database else engine_for(config)
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1')).scalar_one()
            identity = conn.execute(text('SELECT CURRENT_USER()' if config.db_type != 'postgres' else 'SELECT CURRENT_USER')).scalar_one()
        return {'ok': True, 'authenticated_as': identity,
                'message': f'Kết nối thành công. Tài khoản DB thực tế: {identity}. Chưa thực hiện DROP/CREATE.'}
    except Exception as exc:
        operation = 'server_connect' if config.create_database else 'target_connect'
        details = failure_details(DatabaseOperationError(operation, exc, config.database), 'database_connect', 'connection-test')
        raise HTTPException(400, details['message']) from None
    finally:
        engine.dispose()


@app.post('/api/jobs', status_code=202)
async def create_job(config: str = Form(...), file: UploadFile = File(...)):
    try:
        settings = ConnectionConfig.model_validate_json(config)
    except ValueError:
        raise HTTPException(422, 'Cấu hình database không hợp lệ.') from None
    if not file.filename or not file.filename.lower().endswith('.xml'):
        raise HTTPException(400, 'Vui lòng chọn file .xml.')
    if not active.acquire(blocking=False):
        raise HTTPException(409, 'Đang có tác vụ chạy. Vui lòng chờ hoàn tất.')
    folder = None
    submitted = False
    try:
        folder = Path(tempfile.mkdtemp(prefix='netact-'))
        size = 0
        with (folder / 'input.xml').open('wb') as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                stream.write(chunk)
        if not size:
            raise HTTPException(400, 'File XML rỗng.')
        job_id = secrets.token_urlsafe(24)
        with lock:
            # Keep bounded, short-lived result metadata; never store credentials in jobs.
            for old in list(jobs):
                if jobs[old].get('finished_at', time.time()) < time.time() - 86400:
                    del jobs[old]
            if len(jobs) >= 100:
                del jobs[next(iter(jobs))]
            jobs[job_id] = {'id': job_id, 'status': 'queued', 'progress': 0, 'message': 'Đã nhận XML.', 'tables': []}
        executor.submit(run_job, job_id, settings, folder)
        submitted = True
        return {'id': job_id}
    finally:
        await file.close()
        if not submitted:
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
            active.release()


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    with lock:
        if job_id not in jobs:
            raise HTTPException(404, 'Không tìm thấy tác vụ (máy chủ có thể đã khởi động lại).')
        return dict(jobs[job_id])
