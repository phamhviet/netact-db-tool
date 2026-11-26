import csv
import io
import json
import os
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.config import ConnectionConfig
from app.database import administration_engine, prepare_database
from app.main import app
from app.sql_console import split_script, store, streamable_select
from app.sql_results import SQLResultStore

client = TestClient(app)


def test_script_splitter_preserves_literals_comments_and_dollar_quotes():
    source = "SELECT 'a;b'; -- ; comment\nSELECT 'it''s; fine'; DO $$ BEGIN PERFORM 1; END $$;"
    statements = split_script(source)
    assert len(statements) == 3
    assert "a;b" in statements[0]
    assert "PERFORM 1;" in statements[2]
    assert len(split_script("SELECT 'line1\nDELIMITER literal\nline3';")) == 1
    assert streamable_select("WITH x AS (SELECT 1) SELECT * FROM x;")
    assert not streamable_select("WITH x AS (UPDATE t SET a=1 RETURNING *) SELECT * FROM x;")
    assert not streamable_select("SELECT * INTO new_table FROM old_table;")
    for command in ("DELIMITER $$\nSELECT 1$$", "SOURCE local.sql;", "\\copy t TO file"):
        with pytest.raises(HTTPException):
            split_script(command)
    with pytest.raises(HTTPException):
        split_script("-- comment only\n ;")


def test_snapshot_expiry_respects_download_lease():
    snapshots = SQLResultStore(ttl_seconds=60)
    try:
        token, folder = snapshots.new()
        (folder / "result-0.csv").write_text("value\n1\n")
        snapshots.publish(token, folder, {"results": []})
        with snapshots.acquire(token):
            snapshots.runs[token]["expires_at"] = 0
            snapshots.cleanup()
            assert folder.exists()
        assert not folder.exists()
        with pytest.raises(HTTPException) as error:
            with snapshots.acquire(token):
                pass
        assert error.value.status_code == 404
    finally:
        snapshots.close()


@pytest.fixture(
    params=[("mariadb", 53306, "root"), ("postgres", 55432, "postgres"), ("mysql", 53307, "root")]
)
def sql_database(request):
    if os.getenv("NETACT_TEST_DATABASES") != "1":
        pytest.skip("Requires disposable database services")
    kind, port, username = request.param
    config = dict(
        db_type=kind,
        host="127.0.0.1",
        port=port,
        username=username,
        password="local-test-only",
        database="sqltest" + uuid.uuid4().hex[:12],
        create_database=True,
    )
    parsed = ConnectionConfig(**config)
    admin = administration_engine(parsed)
    engine = prepare_database(parsed)
    try:
        yield config, engine
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            quoted = admin.dialect.identifier_preparer.quote_identifier(config["database"])
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {quoted}")
        admin.dispose()


def execute(config, sql, **kwargs):
    return client.post("/api/sql/execute", json={"config": config, "sql": sql, **kwargs})


def test_execute_multiple_results_export_does_not_reexecute_writes(sql_database):
    config, engine = sql_database
    script = """
    CREATE TABLE execution_count (id INT PRIMARY KEY, value VARCHAR(255));
    INSERT INTO execution_count VALUES (1, 'a;b'), (2, 'Tiếng Việt, "quoted"');
    UPDATE execution_count SET value='updated' WHERE id=1;
    SELECT id, value, NULL AS nullable FROM execution_count ORDER BY id;
    SELECT COUNT(*) AS total FROM execution_count;
    """
    response = execute(config, script, preview_limit=1)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    run = response.json()
    assert run["statement_count"] == 5
    assert run["committed"] is True
    sets = [result for result in run["results"] if result["type"] == "rows"]
    assert len(sets) == 2
    assert sets[0]["total_rows"] == 2
    assert sets[0]["rows"] == [["1", "updated", None]]
    index = sets[0]["index"]
    for _ in range(2):
        exported = client.get(f"/api/sql/results/{run['id']}/{index}/csv")
        assert exported.status_code == 200
        assert exported.headers["content-disposition"].startswith("attachment;")
        rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
        assert rows == [["id", "value", "nullable"], ["1", "updated", ""], ["2", 'Tiếng Việt, "quoted"', ""]]
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM execution_count").scalar_one() == 2
    # Empty SELECT remains exportable and duplicate labels stay positional.
    empty = execute(config, "SELECT id, id AS id FROM execution_count WHERE id=999;").json()
    result = empty["results"][0]
    assert result["columns"] == ["id", "id"] and result["rows"] == []
    exported = client.get(f"/api/sql/results/{empty['id']}/0/csv")
    assert list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig")))) == [["id", "id"]]
    assert client.get(f"/api/sql/results/{run['id']}/0/csv").status_code == 404  # CREATE has no result set.
    assert client.get(f"/api/sql/results/{run['id']}/-1/csv").status_code == 404
    assert client.delete(f"/api/sql/results/{run['id']}").status_code == 200
    assert client.get(f"/api/sql/results/{run['id']}/{index}/csv").status_code == 404


def test_result_snapshot_preserves_rows_after_database_changes(sql_database):
    config, engine = sql_database
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE large_result (id INT PRIMARY KEY)")
        marker = "?" if engine.dialect.paramstyle == "qmark" else "%s"
        conn.exec_driver_sql(f"INSERT INTO large_result VALUES ({marker})", [(n,) for n in range(1205)])
    run = execute(config, "SELECT id FROM large_result ORDER BY id;", preview_limit=50).json()
    assert len(run["results"][0]["rows"]) == 50
    assert run["results"][0]["total_rows"] == 1205
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM large_result")
    exported = client.get(f"/api/sql/results/{run['id']}/0/csv")
    rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert len(rows) == 1206
    assert rows[1] == ["0"] and rows[-1] == ["1204"]
    assert "local-test-only" not in json.dumps(run)
    assert client.get("/api/sql/results/not-a-run/0/csv").status_code == 404
    store.discard(run["id"])


def test_statement_error_rolls_back_dml_and_does_not_expose_sql(sql_database):
    config, engine = sql_database
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE rollback_test (id INT PRIMARY KEY)")
    before = set(store.runs)
    response = execute(
        config, "INSERT INTO rollback_test VALUES (1); SELECT PRIVATE_SQL_PASSWORD FROM missing_table;"
    )
    assert response.status_code == 400
    assert "Statement 2 failed" in response.text
    assert "PRIVATE_SQL_PASSWORD" not in response.text
    assert "local-test-only" not in response.text
    assert set(store.runs) == before
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM rollback_test").scalar_one() == 0
    # A new request uses a fresh connection; the previous failure cannot poison it.
    assert execute(config, "SELECT 1 AS ok;").status_code == 200
    assert "rollback_test" in inspect(engine).get_table_names()


def test_dialect_specific_blocks_and_multi_result_calls(sql_database):
    config, _ = sql_database
    if config["db_type"] == "postgres":
        response = execute(config, "DO $$ BEGIN PERFORM 1; PERFORM 2; END $$; SELECT 3 AS result;")
    else:
        response = execute(
            config,
            "CREATE PROCEDURE test_results() BEGIN SELECT 1 AS result_one; SELECT 2 AS result_two; END; CALL test_results();",
        )
    assert response.status_code == 200, response.text
    data = response.json()
    sets = [result for result in data["results"] if result["type"] == "rows"]
    assert [result["rows"] for result in sets] == (
        [[["3"]]] if config["db_type"] == "postgres" else [[["1"]], [["2"]]]
    )


def test_validation_origin_guard_and_english_messages():
    response = client.post(
        "/api/sql/execute", json={"config": {"password": "PRIVATE_PASSWORD"}, "sql": "PRIVATE_SQL"}
    )
    assert response.status_code == 422
    assert "PRIVATE_" not in response.text
    assert client.post("/api/sql/execute", headers={"Origin": "https://other.example"}).status_code == 403
    assert (
        client.delete("/api/sql/results/anything", headers={"Origin": "https://other.example"}).status_code
        == 403
    )
    assert 'lang="en"' in client.get("/").text


def test_sql_execution_is_serialized_without_opening_a_connection():
    from app.sql_console import active_sql

    config = dict(
        db_type="postgres", host="localhost", port=5432, username="unused", password="", database="unused"
    )
    assert active_sql.acquire(blocking=False)
    try:
        response = execute(config, "SELECT 1;")
        assert response.status_code == 409
    finally:
        active_sql.release()


def test_application_shutdown_removes_sql_snapshots():
    with TestClient(app):
        token, folder = store.new()
        (folder / "result-0.csv").write_text("value\n1\n")
        store.publish(token, folder, {"results": []})
        assert folder.exists()
    assert not folder.exists()
    assert not store.runs


@pytest.mark.parametrize("kind", ["file", "stream"])
def test_response_cleanup_runs_even_when_download_send_fails(tmp_path, kind):
    import asyncio

    from app.csv_output import CleanupFileResponse, CleanupStreamingResponse

    released = []
    if kind == "file":
        path = tmp_path / "result.csv"
        path.write_text("value\n1\n")
        response = CleanupFileResponse(path, cleanup=lambda: released.append(True))
    else:
        response = CleanupStreamingResponse(iter([b"value\n1\n"]), cleanup=lambda: released.append(True))

    async def send(message):
        if message["type"] == "http.response.body":
            raise RuntimeError("disconnected")

    async def receive():
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "asgi": {"spec_version": "2.4"}}
    with pytest.raises(RuntimeError):
        asyncio.run(response(scope, receive, send))
    assert released == [True]


def test_shared_result_value_serialization_preserves_json_and_binary():
    from decimal import Decimal

    from app.csv_output import display_value

    value = {"station": "Hà Nội", "ids": [1, 2]}
    assert json.loads(display_value(value)) == value
    assert display_value(memoryview(b"\x00\xff")) == "base64:AP8="
    assert display_value(Decimal("1.20")) == "1.20"
    assert display_value(None) is None
