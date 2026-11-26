import base64
import csv
import io
from contextlib import ExitStack
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import MetaData, Table, inspect, select
from starlette.background import BackgroundTask

from app.config import ConnectionConfig
from app.importer import engine_for

router = APIRouter(prefix='/api/browser')


class BrowseConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    config: ConnectionConfig
    schema_name: str | None = Field(default=None, max_length=128)


class TableQuery(BrowseConfig):
    table: str = Field(min_length=1, max_length=128)
    offset: int = Field(default=0, ge=0)
    page_size: int = Field(default=100, ge=1, le=1000)


def selected_schema(conn, requested):
    inspector = inspect(conn)
    schemas = [name for name in inspector.get_schema_names()
               if name not in ('information_schema', 'pg_catalog') and not name.startswith('pg_toast')]
    current = requested or inspector.default_schema_name
    if current not in schemas:
        raise HTTPException(404, 'Không tìm thấy schema hoặc không có quyền truy cập.')
    return current, schemas


def selected_table(conn, query):
    schema, _ = selected_schema(conn, query.schema_name)
    if query.table not in inspect(conn).get_table_names(schema=schema):
        raise HTTPException(404, 'Không tìm thấy bảng hoặc không có quyền truy cập.')
    return Table(query.table, MetaData(), schema=schema, autoload_with=conn, resolve_fks=False)


def display_value(value):
    if value is None:
        return None
    if isinstance(value, bytes):
        return 'base64:' + base64.b64encode(value).decode('ascii')
    return str(value)


@router.post('/tables')
def list_tables(query: BrowseConfig):
    engine = engine_for(query.config)
    try:
        with engine.connect() as conn:
            schema, schemas = selected_schema(conn, query.schema_name)
            tables = sorted(inspect(conn).get_table_names(schema=schema))
        return {'database': query.config.database, 'schema': schema, 'schemas': schemas, 'tables': tables}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, 'Không đọc được database. Kiểm tra kết nối và quyền xem schema/bảng.') from None
    finally:
        engine.dispose()


@router.post('/rows')
def table_rows(query: TableQuery):
    engine = engine_for(query.config)
    try:
        with engine.connect() as conn:
            table = selected_table(conn, query)
            statement = select(table)
            primary = list(table.primary_key.columns)
            if primary:
                statement = statement.order_by(*primary)
            rows = conn.execute(statement.offset(query.offset).limit(query.page_size + 1)).fetchall()
            return {'table': table.name, 'schema': table.schema,
                    'columns': [col.name for col in table.columns],
                    'rows': [[display_value(value) for value in row] for row in rows[:query.page_size]],
                    'offset': query.offset, 'page_size': query.page_size,
                    'has_next': len(rows) > query.page_size, 'ordered': bool(primary)}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, 'Không đọc được dữ liệu bảng. Kiểm tra kết nối và quyền SELECT.') from None
    finally:
        engine.dispose()


@router.post('/export')
def export_csv(query: str = Form(...)):
    try:
        settings = TableQuery.model_validate_json(query)
    except ValueError:
        raise HTTPException(422, 'Cấu hình export không hợp lệ.') from None
    resources = ExitStack()
    try:
        engine = engine_for(settings.config)
        resources.callback(engine.dispose)
        conn = resources.enter_context(engine.connect())
        table = selected_table(conn, settings)
        # Server-side cursor: fetch bounded batches, never buffer the entire table.
        result = conn.execution_options(yield_per=1000).execute(select(table))
        resources.callback(result.close)
        columns = list(result.keys())
        first = result.fetchmany(1000)
    except Exception as exc:
        resources.close()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400, 'Không export được bảng. Kiểm tra kết nối và quyền SELECT.') from None

    def generate():
        buffer = io.StringIO(newline='')
        writer = csv.writer(buffer)

        def encode(row):
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(row)
            return buffer.getvalue().encode('utf-8')

        try:
            yield b'\xef\xbb\xbf' + encode(columns)
            batch = first
            while batch:
                for row in batch:
                    yield encode([display_value(value) for value in row])
                batch = result.fetchmany(1000)
        finally:
            resources.close()

    return StreamingResponse(generate(), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': "attachment; filename=table.csv; filename*=UTF-8''" + quote(table.name + '.csv', safe=''),
                                      'Cache-Control': 'no-store'},
                             background=BackgroundTask(resources.close))
