import secrets
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import ConnectionConfig
from app.database import prepare_database
from app.errors import DatabaseOperationError, failure_details
from app.importer import ImportFailure, convert, import_csvs

router = APIRouter(prefix="/api/jobs")

executor = ThreadPoolExecutor(max_workers=1)
active = threading.BoundedSemaphore(1)
jobs = {}
lock = threading.Lock()


def update(job_id, **fields):
    with lock:
        jobs[job_id].update(fields)


def run_job(job_id, config, folder):
    engine = None
    stage = "xml_to_csv"
    try:
        update(job_id, status="running", progress=10, message="Validating XML and converting with the JAR…")
        files = convert(folder / "input.xml", folder / "csv")
        update(job_id, progress=40, message=f"Generated {len(files)} CSV files. Connecting to the database…")
        stage = "database_connect"
        engine = prepare_database(config)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise DatabaseOperationError(
                "target_connect", exc, config.database, "created" if config.create_database else "unchanged"
            ) from None
        stage = "csv_to_database"
        result = import_csvs(
            engine, files, lambda percent, message: update(job_id, progress=percent, message=message)
        )
        failed_rows = sum(t["rows_failed"] for t in result)
        failed_tables = sum(t["status"] == "failed_create" for t in result)
        has_errors = failed_rows > 0 or failed_tables > 0
        update(
            job_id,
            status="completed_with_errors" if has_errors else "completed",
            progress=100,
            message=f"Completed: {failed_rows:,} failed rows, {failed_tables:,} failed tables. Review details below."
            if has_errors
            else "Conversion and import completed.",
            tables=result,
            total_rows=sum(t["rows"] for t in result),
            failed_rows=failed_rows,
            failed_tables=failed_tables,
        )
    except ImportFailure as exc:
        update(job_id, status="failed", stage=stage, message=str(exc))
    except Exception as exc:
        update(
            job_id,
            status="failed",
            **failure_details(exc, stage, job_id, password=config.password.get_secret_value()),
        )
    finally:
        if engine is not None:
            engine.dispose()
        shutil.rmtree(folder, ignore_errors=True)
        update(job_id, finished_at=time.time())
        active.release()


@router.post("", status_code=202)
async def create_job(config: Annotated[str, Form()], file: Annotated[UploadFile, File()]):
    try:
        settings = ConnectionConfig.model_validate_json(config)
    except ValueError:
        raise HTTPException(422, "Invalid database configuration.") from None
    if not file.filename or not file.filename.lower().endswith(".xml"):
        raise HTTPException(400, "Choose an .xml file.")
    if not active.acquire(blocking=False):
        raise HTTPException(409, "An import is already running. Wait for it to finish.")
    folder = None
    submitted = False
    try:
        folder = Path(tempfile.mkdtemp(prefix="netact-"))
        size = 0
        with (folder / "input.xml").open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                stream.write(chunk)
        if not size:
            raise HTTPException(400, "The XML file is empty.")
        job_id = secrets.token_urlsafe(24)
        with lock:
            # Keep bounded, short-lived result metadata; never store credentials in jobs.
            for old in list(jobs):
                if jobs[old].get("finished_at", time.time()) < time.time() - 86400:
                    del jobs[old]
            if len(jobs) >= 100:
                del jobs[next(iter(jobs))]
            jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "progress": 0,
                "message": "XML received.",
                "create_database": settings.create_database,
                "tables": [],
            }
        executor.submit(run_job, job_id, settings, folder)
        submitted = True
        return {"id": job_id}
    finally:
        await file.close()
        if not submitted:
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
            active.release()


@router.get("/{job_id}")
def get_job(job_id: str):
    with lock:
        if job_id not in jobs:
            raise HTTPException(404, "Job not found. The server may have restarted.")
        return dict(jobs[job_id])
