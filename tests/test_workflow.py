import json
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.main import app, ConnectionConfig
from app.importer import ImportFailure, convert, engine_for, import_csvs, unique_names

client = TestClient(app)
FIXTURE = Path(__file__).parent / 'fixtures/sample.xml'


def test_converter(tmp_path):
    files = convert(FIXTURE, tmp_path / 'csv')
    assert len(files) == 1
    content = files[0].read_text()
    assert 'Trạm Hà Nội' in content
    assert ',001' in content


def test_names_utf8_and_duplicates():
    names = unique_names(['A', 'a', 'a_1', '', '漢' * 60, '漢' * 60], 63)
    assert len({n.casefold() for n in names}) == 6
    assert all(len(n.encode()) <= 63 for n in names)


def test_dtd_rejected(tmp_path):
    xml = tmp_path / 'bad.xml'
    xml.write_text('<!DOCTYPE x [<!ENTITY x "secret">]><x>&x;</x>')
    with pytest.raises(Exception):
        convert(xml, tmp_path / 'csv')
    assert not (tmp_path / 'csv').exists()


def test_api_validation():
    assert client.get('/').status_code == 200
    health = client.get('/api/health').json()
    assert health['java'] is True
    assert health['max_upload_mb'] is None
    response = client.post('/api/connection', json={'password': 'DO_NOT_ECHO'})
    assert response.status_code == 422
    assert 'DO_NOT_ECHO' not in response.text
    assert client.get('/api/jobs/missing').status_code == 404
    assert client.post('/api/jobs', headers={'Origin': 'https://other.example'}).status_code == 403


@pytest.mark.parametrize('db_type,port,user', [('postgres',55432,'postgres'), ('mariadb',53306,'root')])
def test_real_database_workflow(db_type, port, user, tmp_path):
    if os.getenv('NETACT_TEST_DATABASES') != '1':
        pytest.skip('Set NETACT_TEST_DATABASES=1 with disposable database services')
    config = dict(db_type=db_type, host='127.0.0.1', port=port, username=user,
                  password='local-test-only', database='netact_test_' + uuid.uuid4().hex[:12],
                  create_database=True, ssl_mode='disable')
    parsed = ConnectionConfig(**config)
    admin = engine_for(parsed, 'postgres' if db_type == 'postgres' else 'information_schema')
    target = None
    try:
        assert client.post('/api/connection', json=config).json()['ok']
        with FIXTURE.open('rb') as upload:
            response = client.post('/api/jobs', data={'config': json.dumps(config)}, files={'file': ('sample.xml', upload, 'application/xml')})
        assert response.status_code == 202, response.text
        job_id = response.json()['id']
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = client.get(f'/api/jobs/{job_id}').json()
            if job['status'] in ('completed', 'failed'):
                break
            time.sleep(.1)
        assert job['status'] == 'completed', job
        assert job['total_rows'] == 2
        assert 'password' not in json.dumps(job)
        target = engine_for(parsed)
        table = job['tables'][0]['table']
        q = target.dialect.identifier_preparer.quote_identifier
        with target.connect() as conn:
            rows = conn.exec_driver_sql(f'SELECT {q("name")}, {q("id")} FROM {q(table)} ORDER BY {q("id")}').all()
        assert rows == [('Trạm Hà Nội', '001'), ('Station B', '002')]
        # All target tables are checked before any CREATE/INSERT.
        files = convert(FIXTURE, tmp_path / 'csv')
        with pytest.raises(ImportFailure, match='đã tồn tại'):
            import_csvs(target, files, lambda *_: None)
        with target.connect() as conn:
            assert conn.exec_driver_sql(f'SELECT COUNT(*) FROM {q(table)}').scalar() == 2
        # A malformed row removes newly created tables (including MariaDB DDL).
        malformed = tmp_path / 'malformed.csv'
        malformed.write_text('a,b\n1,2,3\n')
        with pytest.raises(ImportFailure, match='thừa cột'):
            import_csvs(target, [malformed], lambda *_: None)
        assert 'malformed' not in inspect(target).get_table_names()
    finally:
        if target is not None:
            target.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            quoted = admin.dialect.identifier_preparer.quote_identifier(config['database'])
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS {quoted}')
        admin.dispose()
