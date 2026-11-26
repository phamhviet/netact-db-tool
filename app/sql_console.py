"""Execute scripts once and export captured result sets without re-execution."""

import re
import shutil
import threading
import time
from contextlib import ExitStack

import sqlparse
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from app.browser import BrowseConfig, selected_schema
from app.csv_output import CleanupFileResponse, CSVEncoder, display_value
from app.database import engine_for
from app.errors import database_error_code
from app.settings import environment_value
from app.sql_results import SQLResultStore

router = APIRouter(prefix="/api/sql")
active_sql = threading.BoundedSemaphore(1)


def result_ttl():
    try:
        seconds = int(environment_value("SQL_RESULT_TTL_SECONDS", "3600"))
        if seconds <= 0:
            raise ValueError
        return seconds
    except (ValueError, TypeError):
        raise ValueError("SQL_RESULT_TTL_SECONDS must be a positive integer.") from None


store = SQLResultStore(ttl_seconds=result_ttl())


class SQLRequest(BrowseConfig):
    sql: str = Field(min_length=1)
    preview_limit: int = Field(default=100, ge=1, le=1000)


def split_script(source):
    statements = []
    for part in sqlparse.split(source):
        clean = sqlparse.format(part, strip_comments=True).strip().strip(";").strip()
        if re.match(r"^(?:DELIMITER\b|SOURCE\b|\\\w)", clean, re.IGNORECASE):
            raise HTTPException(
                400,
                "Client commands such as DELIMITER, SOURCE and psql backslash commands are not SQL. Submit server SQL directly.",
            )
        if clean:
            statements.append(part)
    if not statements:
        raise HTTPException(400, "Enter at least one SQL statement.")
    return statements


def streamable_select(statement):
    parsed = sqlparse.parse(statement)[0]
    if parsed.get_type() != "SELECT":
        return False
    words = {
        token.normalized
        for token in parsed.flatten()
        if token.ttype in sqlparse.tokens.Keyword or token.ttype in sqlparse.tokens.DML
    }
    return not words.intersection({"INTO", "INSERT", "UPDATE", "DELETE", "MERGE"})


def open_cursor(raw, engine, statement, index):
    if engine.dialect.name == "postgresql" and streamable_select(statement):
        return raw.cursor(name=f"netact_result_{index}"), True
    if engine.dialect.driver == "mariadbconnector":
        return raw.cursor(buffered=False), False
    if engine.dialect.driver == "pymysql":
        from pymysql.cursors import SSCursor

        return raw.cursor(SSCursor), False
    return raw.cursor(), False


def execute_script(query):
    statements = split_script(query.sql)
    token, folder = store.new()
    engine = None
    started = time.monotonic()
    statement_index = 0
    results = []
    try:
        engine = engine_for(query.config)
        with engine.connect() as conn:
            with conn.begin():
                schema, _ = selected_schema(conn, query.schema_name)
                quoted = engine.dialect.identifier_preparer.quote_identifier(schema)
                if engine.dialect.name == "postgresql":
                    conn.exec_driver_sql(f"SET LOCAL search_path TO {quoted}")
                else:
                    conn.exec_driver_sql(f"USE {quoted}")
                raw = conn.connection.driver_connection
                for statement_index, statement in enumerate(statements, 1):
                    cursor, named = open_cursor(raw, engine, statement, statement_index)
                    try:
                        cursor.execute(statement)
                        while True:
                            result = {
                                "index": len(results),
                                "statement": statement_index,
                                "type": "rows" if cursor.description else "command",
                                "columns": [],
                                "rows": [],
                                "total_rows": 0,
                                "affected_rows": cursor.rowcount if cursor.rowcount >= 0 else None,
                            }
                            if cursor.description:
                                result["columns"] = [column[0] for column in cursor.description]
                                encoder = CSVEncoder()
                                with (folder / f"result-{result['index']}.csv").open("wb") as output:
                                    output.write(b"\xef\xbb\xbf" + encoder.row(result["columns"]))
                                    while batch := cursor.fetchmany(1000):
                                        for row in batch:
                                            values = [display_value(value) for value in row]
                                            output.write(encoder.row(values))
                                            result["total_rows"] += 1
                                            if len(result["rows"]) < query.preview_limit:
                                                result["rows"].append(values)
                            results.append(result)
                            if named or not cursor.nextset():
                                break
                    finally:
                        cursor.close()
        metadata = {
            "id": token,
            "statement_count": len(statements),
            "results": results,
            "preview_limit": query.preview_limit,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "expires_in_seconds": store.ttl_seconds,
            "committed": True,
        }
        store.publish(token, folder, metadata)
        return metadata
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        original = getattr(exc, "orig", exc)
        code = database_error_code(original)
        reasons = {
            "1064": "SQL syntax error.",
            "42601": "SQL syntax error.",
            "1146": "Table does not exist.",
            "42P01": "Table does not exist.",
            "1054": "Column does not exist.",
            "42703": "Column does not exist.",
            "1062": "Duplicate key.",
            "23505": "Duplicate key.",
            "1044": "The server denied database access.",
            "42501": "The server denied this operation.",
        }
        reason = reasons.get(code, "The database rejected the operation or the connection failed.")
        detail = (
            f"Statement {statement_index or 'setup'} failed ({type(original).__name__}"
            + (f", code {code}" if code else "")
            + f"). {reason} "
        )
        detail += "The script stopped and rollback was attempted. DDL, nontransactional tables or explicit COMMIT may already have committed changes."
        # Do not return driver messages, SQL or parameters: scripts may contain credentials.
        raise HTTPException(400, detail) from None
    finally:
        if engine is not None:
            engine.dispose()


@router.post("/execute")
def run_sql(query: SQLRequest):
    if not active_sql.acquire(blocking=False):
        raise HTTPException(409, "Another SQL script is running. Wait for it to finish.")
    try:
        return JSONResponse(execute_script(query), headers={"Cache-Control": "no-store"})
    finally:
        active_sql.release()


@router.get("/results/{token}/{index}/csv")
def export_result(token: str, index: int):
    resources = ExitStack()
    try:
        run = resources.enter_context(store.acquire(token))
        results = run["metadata"]["results"]
        if not 0 <= index < len(results) or results[index]["type"] != "rows":
            raise HTTPException(404, "This result set has no CSV export.")
        path = run["folder"] / f"result-{index}.csv"
        return CleanupFileResponse(
            path,
            media_type="text/csv; charset=utf-8",
            filename=f"sql-result-{index + 1}.csv",
            headers={"Cache-Control": "no-store"},
            cleanup=resources.close,
        )
    except Exception:
        resources.close()
        raise


@router.delete("/results/{token}")
def discard_result(token: str):
    store.discard(token)
    return {"ok": True}
