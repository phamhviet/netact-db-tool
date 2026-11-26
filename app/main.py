import secrets
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import text

from app.importer import ImportFailure, convert, engine_for, import_csvs, prepare_database

app = FastAPI(title='NetAct XML → Database', docs_url=None, redoc_url=None)
STATIC = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=STATIC), name='static')
executor = ThreadPoolExecutor(max_workers=1)
active = threading.BoundedSemaphore(1)
jobs = {}
lock = threading.Lock()


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    db_type: Literal['mariadb', 'mysql', 'postgres'] = 'mariadb'
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    database: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr
    create_database: bool = False
    ssl_mode: Literal['disable', 'require', 'verify-full'] = 'disable'

    @field_validator('ssl_mode')
    @classmethod
    def supported_tls(cls, value, info):
        if value == 'require' and info.data.get('db_type') != 'postgres':
            raise ValueError('MariaDB/MySQL: chọn disable hoặc verify-full.')
        return value

    @field_validator('host', 'username')
    @classmethod
    def clean(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError('Ký tự điều khiển không hợp lệ.')
        return value.strip()


def update(job_id, **fields):
    with lock:
        jobs[job_id].update(fields)


def run_job(job_id, config, folder):
    engine = None
    try:
        update(job_id, status='running', progress=10, message='Kiểm tra XML và chuyển đổi bằng JAR…')
        files = convert(folder / 'input.xml', folder / 'csv')
        update(job_id, progress=40, message=f'Đã tạo {len(files)} CSV. Đang kết nối database…')
        engine = prepare_database(config)
        result = import_csvs(engine, files, lambda percent, message: update(job_id, progress=percent, message=message))
        update(job_id, status='completed', progress=100, message='Hoàn tất chuyển đổi và nhập dữ liệu.',
               tables=result, total_rows=sum(t['rows'] for t in result))
    except ImportFailure as exc:
        update(job_id, status='failed', message=str(exc))
    except Exception:
        # Driver/parser exception messages may contain credentials, queries or XML data.
        update(job_id, status='failed', message='Xử lý thất bại. Kiểm tra XML, kết nối, quyền tạo database/bảng và dung lượng máy chủ. Database mới tạo có thể còn tồn tại; xem README trước khi thử lại.')
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
    return await call_next(request)


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'java': shutil.which('java') is not None, 'max_upload_mb': None}


@app.post('/api/connection')
def test_connection(config: ConnectionConfig):
    # Read-only: never create a database from the connection-test button.
    engine = engine_for(config, ('postgres' if config.db_type == 'postgres' else 'information_schema') if config.create_database else None)
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1')).scalar_one()
        return {'ok': True, 'message': 'Kết nối thành công. Quyền tạo database/bảng sẽ được kiểm tra khi nhập.'}
    except Exception:
        raise HTTPException(400, 'Không kết nối được. Kiểm tra host, port, tài khoản, TLS và database.') from None
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
