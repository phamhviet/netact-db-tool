import asyncio
import shutil
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.browser import router as browser_router
from app.config import ConnectionConfig
from app.database import administration_engine, engine_for
from app.diagnostics import diagnose_connection
from app.errors import DatabaseOperationError, failure_details
from app.jobs import router as jobs_router
from app.settings import public_database_profiles
from app.sql_console import router as sql_router
from app.sql_console import store as sql_store


@asynccontextmanager
async def lifespan(app):
    async def clean_sql_results():
        while True:
            await asyncio.sleep(60)
            await asyncio.to_thread(sql_store.cleanup)

    cleanup = asyncio.create_task(clean_sql_results())
    try:
        yield
    finally:
        cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup
        sql_store.close()


app = FastAPI(title="NetAct XML → Database", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(jobs_router)
app.include_router(browser_router)
app.include_router(sql_router)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Never echo rejected request bodies: they can contain a database password.
    return JSONResponse(
        {"detail": "Invalid configuration. Check database type, host, port, database name and TLS."},
        status_code=422,
    )


@app.middleware("http")
async def same_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != expected:
        return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "java": shutil.which("java") is not None, "max_upload_mb": None}


@app.get("/api/db-presets")
def db_presets():
    try:
        return JSONResponse(public_database_profiles(), headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/connection")
def test_connection(config: ConnectionConfig):
    # Read-only: use the same login path as import without running DROP/CREATE.
    engine = administration_engine(config) if config.create_database else engine_for(config)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1")).scalar_one()
            identity = conn.execute(
                text("SELECT CURRENT_USER()" if config.db_type != "postgres" else "SELECT CURRENT_USER")
            ).scalar_one()
        return {
            "ok": True,
            "authenticated_as": identity,
            "message": f"Connected successfully. Authenticated DB account: {identity}. No DROP/CREATE was executed.",
        }
    except Exception as exc:
        operation = "server_connect" if config.create_database else "target_connect"
        details = failure_details(
            DatabaseOperationError(operation, exc, config.database),
            "database_connect",
            "connection-test",
            password=config.password.get_secret_value(),
        )
        raise HTTPException(400, details["message"]) from None
    finally:
        engine.dispose()


@app.post("/api/diagnostics")
def database_diagnostics(config: ConnectionConfig):
    try:
        return JSONResponse(diagnose_connection(config), headers={"Cache-Control": "no-store"})
    except Exception as exc:
        details = failure_details(
            DatabaseOperationError("server_connect", exc, config.database),
            "database_connect",
            "diagnostics",
            password=config.password.get_secret_value(),
        )
        raise HTTPException(400, details["message"]) from None
