import json
import csv
import io
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.main import app, ConnectionConfig
from app.importer import ImportFailure, convert, engine_for, import_csvs, unique_names, normalize_xml, script_column_names, prepare_database

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


def test_entity_rejected(tmp_path):
    xml = tmp_path / 'bad.xml'
    xml.write_text('<!DOCTYPE x [<!ENTITY x "secret">]><x>&x;</x>')
    with pytest.raises(ImportFailure, match='khai báo entity'):
        convert(xml, tmp_path / 'csv')
    assert not (tmp_path / 'csv').exists()


@pytest.mark.parametrize('doctype', [
    '<!DOCTYPE raml>',
    '<!DOCTYPE raml SYSTEM "https://example.invalid/private.dtd">',
    '<!DOCTYPE raml PUBLIC "raml" "file:///nonexistent/private.dtd">',
])
def test_doctype_without_entities_converts(tmp_path, doctype):
    original = FIXTURE.read_text()
    content = original.replace('<raml ', doctype + '\n<raml ', 1)
    xml = tmp_path / 'with-dtd.xml'
    xml.write_text(content, encoding='utf-8')
    files = convert(xml, tmp_path / 'csv')
    assert len(files) == 1
    assert 'Trạm Hà Nội' in files[0].read_text()
    assert ',001' in files[0].read_text()
    assert xml.read_text() == content  # Keep the uploaded original unchanged.
    assert not list(tmp_path.glob('normalized-*'))


def test_actual_exporter_relative_dtd_matches_direct_jar(tmp_path):
    # Match the supplied export's structure using synthetic site names/IDs.
    xml = tmp_path / 'actual.xml'
    objects = ['<managedObject class="PLMN" version="1.0" distName="PLMN-PLMN" id="1"/>']
    for number in [1, 10, 11, 12, 2, 3, 4, 5, 6, 7, 8, 9]:
        objects.append(f'<managedObject class="MRSITE" version="1.0" '
                       f'distName="PLMN-PLMN/MRSITE-{number}" id="{100 + number}">'
                       f'<p name="name">Trạm thử nghiệm {number}</p></managedObject>')
    for kind in ['EXCENBF', 'EXCCG', 'EXCCU']:
        objects.append(f'<managedObject class="{kind}" version="1.0" '
                       f'distName="PLMN-PLMN/{kind}-1" id="200">'
                       '<defaults name="System"/></managedObject>')
    objects.extend([
        '<managedObject class="NETACT" version="8" distName="PLMN-PLMN/NETACT-1" id="300"/>',
        '<managedObject class="HPHW" version="11isdk" distName="PLMN-PLMN/HPHW-1" id="400"/>',
    ])
    xml.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                   "<!DOCTYPE raml SYSTEM 'raml20.dtd'>\n"
                   '<raml version="2.0" xmlns="raml20.xsd"><cmData type="actual">'
                   '<header><log dateTime="2026-01-01T10:25:39.000+07:00" '
                   'action="created" appInfo="ActualExporter">UIValues are used</log></header>'
                   + ''.join(objects) + '</cmData></raml>', encoding='utf-8')
    direct = tmp_path / 'direct'
    direct.mkdir()
    jar = Path(__file__).resolve().parents[1] / 'vendor/boda-nokiacmdataparser.jar'
    raw_result = subprocess.run(['java', '-Dfile.encoding=UTF-8', '-jar', str(jar),
                                 '-i', str(xml), '-o', str(direct)],
                                cwd=tmp_path, capture_output=True, timeout=30)
    assert raw_result.returncode == 0
    normalized_files = convert(xml, tmp_path / 'normalized-csv')
    raw_files = sorted(direct.rglob('*.csv'))
    assert raw_files
    raw = {p.name: p.read_bytes() for p in raw_files}
    normalized = {p.name: p.read_bytes() for p in normalized_files}
    assert normalized == raw
    assert any('Trạm thử nghiệm 1' in p.read_text() for p in normalized_files)


def test_normalization_preserves_utf16_namespaces_and_values(tmp_path):
    xml = tmp_path / 'utf16.xml'
    xml.write_text('<?xml version="1.0" encoding="UTF-16"?>'
                   '<n:raml xmlns:n="raml20.xsd" a="A &amp; B"><n:p>Hà Nội &lt; 2</n:p></n:raml>',
                   encoding='utf-16')
    normalized = tmp_path / 'normalized.xml'
    normalize_xml(xml, normalized)
    from xml.etree.ElementTree import parse
    root = parse(normalized).getroot()
    assert root.tag == '{raml20.xsd}raml'
    assert root.attrib['a'] == 'A & B'
    assert root[0].text == 'Hà Nội < 2'


def test_invalid_xml_has_position_without_contents(tmp_path):
    xml = tmp_path / 'broken.xml'
    xml.write_text('<raml>\n<private>DO_NOT_ECHO & broken</private></raml>')
    with pytest.raises(ImportFailure, match='dòng 2, cột') as error:
        convert(xml, tmp_path / 'csv')
    assert 'DO_NOT_ECHO' not in str(error.value)
    assert not (tmp_path / 'csv').exists()
    assert not list(tmp_path.glob('normalized-*'))


def test_external_entity_rejected(tmp_path):
    xml = tmp_path / 'external.xml'
    xml.write_text('<!DOCTYPE raml [<!ENTITY private SYSTEM "file:///etc/passwd">]>'
                   '<raml>&private;</raml>')
    with pytest.raises(ImportFailure, match='khai báo entity'):
        convert(xml, tmp_path / 'csv')
    assert not (tmp_path / 'csv').exists()


def test_entity_from_skipped_dtd_never_dropped_silently(tmp_path):
    xml = tmp_path / 'missing-entity.xml'
    xml.write_text('<!DOCTYPE raml SYSTEM "https://example.invalid/private.dtd">'
                   '<raml>&private;</raml>')
    with pytest.raises(ImportFailure, match='entity'):
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


def test_job_reports_xml_position_without_connecting_to_database(monkeypatch):
    import importlib
    main = importlib.import_module('app.main')

    def unexpected_database(*args):
        pytest.fail('Invalid XML must fail before database access')

    monkeypatch.setattr(main, 'prepare_database', unexpected_database)
    config = dict(db_type='postgres', host='127.0.0.1', port=5432, database='unused',
                  username='unused', password='DO_NOT_ECHO_PASSWORD')
    response = client.post('/api/jobs', data={'config': json.dumps(config)},
                           files={'file': ('bad.xml', b'<raml>\n<p>PRIVATE_XML & broken</p></raml>', 'application/xml')})
    assert response.status_code == 202
    job_id = response.json()['id']
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] in ('completed', 'failed'):
            break
        time.sleep(.01)
    assert job['status'] == 'failed'
    assert 'dòng 2, cột' in job['message']
    assert 'PRIVATE_XML' not in json.dumps(job)
    assert 'DO_NOT_ECHO_PASSWORD' not in json.dumps(job)


def test_database_error_is_specific_without_leaking_credentials(caplog):
    from sqlalchemy.exc import OperationalError
    from pymysql.err import OperationalError as MySQLError
    from app.errors import failure_details
    error = OperationalError('PRIVATE_SQL', {'password': 'PRIVATE_PASSWORD'},
                             MySQLError(1045, 'PRIVATE_DRIVER_MESSAGE'))
    details = failure_details(error, 'database_connect', 'test-id')
    assert details['error_code'] == '1045'
    assert 'Kết nối/tạo database' in details['message']
    assert 'Sai tài khoản/mật khẩu' in details['message']
    assert 'PRIVATE_' not in json.dumps(details)
    assert 'PRIVATE_' not in caplog.text


def test_postgres_privilege_error_is_identified():
    from sqlalchemy.exc import ProgrammingError
    from psycopg.errors import InsufficientPrivilege
    from app.errors import failure_details
    details = failure_details(ProgrammingError('PRIVATE_SQL', {}, InsufficientPrivilege('PRIVATE_MESSAGE')),
                              'csv_to_database', 'test-id')
    assert details['error_code'] == '42501'
    assert 'thiếu quyền' in details['message']
    assert 'PRIVATE_' not in json.dumps(details)


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
        browse = {'config': config}
        listing = client.post('/api/browser/tables', json=browse)
        assert listing.status_code == 200, listing.text
        assert table in listing.json()['tables']
        schema = listing.json()['schema']
        query = {'config': config, 'schema_name': schema, 'table': table, 'page_size': 1}
        preview = client.post('/api/browser/rows', json=query)
        assert preview.status_code == 200, preview.text
        assert len(preview.json()['rows']) == 1
        assert preview.json()['has_next'] is True
        assert preview.json()['ordered'] is False
        assert client.post('/api/browser/rows', json={**query, 'offset': 1}).json()['has_next'] is False
        assert client.post('/api/browser/rows', json={**query, 'table': 'missing; DROP DATABASE x'}).status_code == 404
        assert client.post('/api/browser/rows', json={**query, 'schema_name': 'missing'}).status_code == 404
        assert client.post('/api/browser/rows', json={**query, 'offset': -1}).status_code == 422
        assert client.post('/api/browser/rows', json={**query, 'page_size': 1001}).status_code == 422
        # A quote in a real table name must be handled as an identifier, never SQL.
        from sqlalchemy import Column, Integer, MetaData, Table, Text
        special = Table('quoted " table', MetaData(), Column('id', Integer, primary_key=True),
                        Column('value', Text), Column('nullable', Text))
        value = 'Tiếng Việt, "quoted"\n' + 'x' * 600
        with target.begin() as conn:
            special.create(conn)
            conn.execute(special.insert(), [{'id': number, 'value': value, 'nullable': None} for number in range(1, 1206)])
        query['table'] = special.name
        query['page_size'] = 100
        page = client.post('/api/browser/rows', json={**query, 'offset': 100}).json()
        assert page['ordered'] is True
        assert len(page['rows']) == 100
        assert page['rows'][0] == ['101', value, None]
        exported = client.post('/api/browser/export', data={'query': json.dumps(query)})
        assert exported.status_code == 200, exported.text
        assert exported.headers['content-disposition'].startswith('attachment;')
        assert exported.content.startswith(b'\xef\xbb\xbf')
        csv_rows = list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig'))))
        assert csv_rows[0] == ['id', 'value', 'nullable']
        assert len(csv_rows) == 1206  # Export all rows, not only the selected page.
        assert all(row[1:] == [value, ''] for row in csv_rows[1:])
        assert config['password'] not in listing.text
        assert config['password'] not in preview.text
        assert config['password'] not in exported.text
        # Reuse existing tables and insert again, as CREATE TABLE IF NOT EXISTS does.
        uploaded_name = tmp_path / 'input.xml'
        uploaded_name.write_bytes(FIXTURE.read_bytes())
        files = convert(uploaded_name, tmp_path / 'csv')
        repeated = import_csvs(target, files, lambda *_: None)
        assert repeated[0]['rows'] == 2
        with target.connect() as conn:
            assert conn.exec_driver_sql(f'SELECT COUNT(*) FROM {q(table)}').scalar() == 4
        verify_original_script_cases(target, tmp_path, db_type)
        # Creating a database now means DROP IF EXISTS + CREATE with the same name.
        # Leaving the option off, or testing a connection, must preserve existing data.
        assert client.post('/api/connection', json=config).json()['ok']
        assert inspect(target).get_table_names()
        reused = prepare_database(ConnectionConfig(**{**config, 'create_database': False}))
        assert inspect(reused).get_table_names()
        reused.dispose()
        target.dispose()
        recreated = prepare_database(ConnectionConfig(**config))
        with recreated.connect() as conn:
            assert inspect(conn).get_table_names() == []
        recreated.dispose()
    finally:
        if target is not None:
            target.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            quoted = admin.dialect.identifier_preparer.quote_identifier(config['database'])
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS {quoted}')
        admin.dispose()


def verify_original_script_cases(engine, folder, db_type):
    def write_csv(name, headers, rows, encoding='utf-8-sig'):
        path = folder / name
        with path.open('w', encoding=encoding, newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)
        return path

    headers = [' A ', 'A', 'A_1', '', 'x' * 80, 'x' * 80, 'FUNCTION', 'RANK', 'VERSION', '漢' * 40]
    row = ['001', 'a', 'b', '', 'z' * 300, 'tail', 'f', 'r', 'v', 'unicode']
    edge = write_csv('edge.csv', headers, [row, ['002'], row + ['ignored extra'], []])
    report = import_csvs(engine, [edge], lambda *_: None)[0]
    assert report['rows'] == 4 and report['rows_failed'] == 0
    expected = script_column_names(headers) if db_type != 'postgres' else unique_names(headers, 63)
    assert [c['target'] for c in report['columns']] == expected
    with engine.connect() as conn:
        from sqlalchemy import MetaData, Table, select, Text
        table = Table('edge', MetaData(), autoload_with=conn)
        rows = conn.execute(select(table)).all()
        assert tuple(row) in [tuple(r) for r in rows]
        assert tuple([''] * 10) in [tuple(r) for r in rows]
        assert tuple(['002'] + [''] * 9) in [tuple(r) for r in rows]
        assert table.c[expected[0]].type.length == 3
        assert table.c[expected[3]].type.length == 1
        assert isinstance(table.c[expected[4]].type, Text)
    punctuated = write_csv('quoted%:table.csv', ['function'], [['001']])
    assert import_csvs(engine, [punctuated], lambda *_: None)[0]['rows'] == 1
    # A blank file is skipped; a header-only file still creates VARCHAR(1).
    blank = folder / 'blank.csv'
    blank.write_bytes(b'')
    header_only = write_csv('header_only.csv', ['empty'], [])
    reports = import_csvs(engine, [blank, header_only], lambda *_: None)
    assert reports[0]['status'] == 'skipped_empty'
    assert reports[1]['rows'] == 0
    assert inspect(engine).get_columns('header_only')[0]['type'].length == 1
    # These byte sequences require the same fallback order as the supplied script.
    for name, value, encoding in [('cp', 'café €', 'cp1252'), ('latin', 'café \x81', 'latin-1')]:
        path = write_csv(f'{name}.csv', ['value'], [[value]], encoding)
        assert import_csvs(engine, [path], lambda *_: None)[0]['rows'] == 1
        with engine.connect() as conn:
            table = Table(name, MetaData(), autoload_with=conn)
            assert conn.execute(select(table)).scalar_one() == value
    # The reference script uses universal-newline text mode, including inside cells.
    crlf = write_csv('crlf_value.csv', ['value'], [['a\r\nb']])
    assert import_csvs(engine, [crlf], lambda *_: None)[0]['rows'] == 1
    with engine.connect() as conn:
        table = Table('crlf_value', MetaData(), autoload_with=conn)
        assert conn.execute(select(table)).scalar_one() == 'a\nb'
    # Wide short-value tables use VARCHAR, not TEXT for every column.
    wide = write_csv('wide.csv', [f'c{n}' for n in range(400)], [['x'] * 400])
    assert import_csvs(engine, [wide], lambda *_: None)[0]['rows'] == 1
    assert all(c['type'].length == 1 for c in inspect(engine).get_columns('wide'))
    too_wide = write_csv('too_wide.csv', [f'c{n}' for n in range(1700)], [['x'] * 1700])
    good = write_csv('after_bad_create.csv', ['value'], [['ok']])
    reports = import_csvs(engine, [too_wide, good], lambda *_: None)
    assert reports[0]['status'] == 'failed_create'
    assert reports[0]['errors']
    assert reports[1]['rows'] == 1
    # TEXT limit on MariaDB / NUL on PostgreSQL produces one rejected row,
    # while subsequent valid rows are still committed.
    bad_value = 'x' * 70000 if db_type != 'postgres' else 'bad\x00value'
    failure = write_csv('row_failure.csv', ['value'], [['ok'], [bad_value], ['after']])
    report = import_csvs(engine, [failure], lambda *_: None)[0]
    assert report['status'] == 'partial'
    assert report['rows'] == 2 and report['rows_failed'] == 1
    assert report['errors'][0]['line'] == 3
    with engine.connect() as conn:
        table = Table('row_failure', MetaData(), autoload_with=conn)
        assert conn.execute(select(table)).scalars().all() == ['ok', 'after']
    # Original filename truncation may reuse one table; insert is positional.
    first = write_csv('t' * 64 + '_first.csv', ['first'], [['a']])
    second = write_csv('t' * 64 + '_second.csv', ['different'], [['b']])
    reports = import_csvs(engine, [first, second], lambda *_: None)
    assert [r['rows'] for r in reports] == [1, 1]
    assert reports[0]['table'] == reports[1]['table']
    with engine.connect() as conn:
        table = Table(reports[0]['table'], MetaData(), autoload_with=conn)
        assert conn.execute(select(table)).scalars().all() == ['a', 'b']


def test_script_column_name_contract():
    assert script_column_names([' A ', 'A', 'A_1', '', 'A', 'a']) == ['A', 'A_1', 'A_1_1', 'unknown_column', 'A_2', 'a']
    assert script_column_names(['漢' * 70])[0] == '漢' * 64


def test_mariadb_schema_scoped_permissions(tmp_path):
    if os.getenv('NETACT_TEST_DATABASES') != '1':
        pytest.skip('Requires the disposable MariaDB test service')
    from app.errors import DatabaseOperationError, failure_details
    from app.importer import administration_engine
    suffix = uuid.uuid4().hex[:12]
    database = 'scopetest' + suffix
    owner = 'owner' + suffix
    limited = 'limited' + suffix
    password = 'local-scoped-test-only'
    root_config = ConnectionConfig(db_type='mariadb', host='127.0.0.1', port=53306,
                                   username='root', password='local-test-only', database=database)
    root = engine_for(root_config, None)
    quoted = root.dialect.identifier_preparer.quote_identifier(database)
    owned = denied = None
    try:
        with root.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            for account in (owner, limited):
                conn.exec_driver_sql("CREATE USER %s@'%%' IDENTIFIED BY %s", (account, password))
            conn.exec_driver_sql(f"GRANT ALL PRIVILEGES ON {quoted}.* TO %s@'%%'", (owner,))
        cfg = ConnectionConfig(**{**root_config.model_dump(), 'username': owner, 'password': password, 'create_database': True})
        admin = administration_engine(cfg)
        assert admin.url.database is None  # No information_schema or target DB selected.
        admin.dispose()
        response = client.post('/api/connection', json={**cfg.model_dump(mode='json'), 'password': password})
        assert response.status_code == 200, response.text
        assert response.json()['authenticated_as'] == owner + '@%'
        owned = prepare_database(cfg)
        with owned.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE sentinel (value VARCHAR(10))')
            conn.exec_driver_sql("INSERT INTO sentinel VALUES ('preserved')")
        with root.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.exec_driver_sql(f"GRANT SELECT, INSERT, CREATE ON {quoted}.* TO %s@'%%'", (limited,))
        denied_config = {**cfg.model_dump(mode='json'), 'username': limited, 'password': password}
        limited_cfg = ConnectionConfig(**denied_config)
        # SELECT 1 / CURRENT_USER does not require database recreation privileges.
        assert client.post('/api/connection', json=denied_config).status_code == 200
        with pytest.raises(DatabaseOperationError) as caught:
            prepare_database(limited_cfg)
        details = failure_details(caught.value, 'database_connect', 'test', log_error=False)
        assert details['operation'] == 'drop_database'
        assert details['error_code'] == '1044'
        assert 'quyền DROP' in details['message']
        assert password not in json.dumps(details)
        with owned.connect() as conn:
            assert conn.exec_driver_sql('SELECT value FROM sentinel').scalar_one() == 'preserved'
        with FIXTURE.open('rb') as source:
            response = client.post('/api/jobs', data={'config': json.dumps(denied_config)},
                                   files={'file': ('sample.xml', source, 'application/xml')})
        assert response.status_code == 202
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = client.get('/api/jobs/' + response.json()['id']).json()
            if job['status'] == 'failed':
                break
            time.sleep(.05)
        assert job['operation'] == 'drop_database'
        assert job['error_code'] == '1044'
        assert password not in json.dumps(job)
        # The owner has privileges only on this schema, and can recreate it.
        owned.dispose()
        owned = prepare_database(cfg)
        assert inspect(owned).get_table_names() == []
        owned.dispose()
        # Distinguish successful DROP followed by denied CREATE.
        with root.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.exec_driver_sql(f"REVOKE CREATE ON {quoted}.* FROM %s@'%%'", (limited,))
            conn.exec_driver_sql(f"GRANT DROP ON {quoted}.* TO %s@'%%'", (limited,))
        with pytest.raises(DatabaseOperationError) as caught:
            prepare_database(limited_cfg)
        details = failure_details(caught.value, 'database_connect', 'test', log_error=False)
        assert details['operation'] == 'create_database'
        assert details['error_code'] == '1044'
        assert 'DROP đã thành công' in details['message']
    finally:
        if owned is not None:
            owned.dispose()
        with root.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS {quoted}')
            for account in (owner, limited):
                conn.exec_driver_sql("DROP USER IF EXISTS %s@'%%'", (account,))
        root.dispose()


def test_missing_database_creates_without_attempting_drop(monkeypatch):
    if os.getenv('NETACT_TEST_DATABASES') != '1':
        pytest.skip('Requires disposable MariaDB')
    import importlib
    from sqlalchemy import event
    from app.errors import DatabaseOperationError
    importer = importlib.import_module('app.importer')
    suffix = uuid.uuid4().hex[:12]
    database, account = 'createtest' + suffix, 'creator' + suffix
    password = 'local-create-only-test'
    root_cfg = ConnectionConfig(db_type='mariadb', host='127.0.0.1', port=53306,
                                username='root', password='local-test-only', database=database)
    root = engine_for(root_cfg, None)
    name = root.dialect.identifier_preparer.quote_identifier(database)
    creator_cfg = ConnectionConfig(**{**root_cfg.model_dump(), 'username': account, 'password': password,
                                      'create_database': True})
    creator = engine_for(creator_cfg, None)
    ddl = []

    @event.listens_for(creator, 'before_cursor_execute')
    def track_ddl(conn, cursor, statement, parameters, context, executemany):
        for operation in ('DROP DATABASE', 'CREATE DATABASE'):
            if statement.startswith(operation):
                ddl.append(operation)

    target = None
    try:
        with root.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.exec_driver_sql("CREATE USER %s@'%%' IDENTIFIED BY %s", (account, password))
            # CREATE on this schema only; deliberately no DROP privilege.
            conn.exec_driver_sql(f"GRANT CREATE ON {name}.* TO %s@'%%'", (account,))
        monkeypatch.setattr(importer, 'administration_engine', lambda config: creator)
        target = prepare_database(creator_cfg)
        with target.connect() as conn:
            assert conn.exec_driver_sql('SELECT DATABASE()').scalar_one() == database
        assert ddl == ['CREATE DATABASE']  # No DROP sent for a missing DB.
        target.dispose()
        # Now it exists, so DROP is attempted and a real permission denial surfaces.
        with pytest.raises(DatabaseOperationError) as error:
            prepare_database(creator_cfg)
        assert error.value.operation == 'drop_database'
        assert ddl == ['CREATE DATABASE', 'DROP DATABASE']
    finally:
        if target is not None:
            target.dispose()
        creator.dispose()
        with root.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS {name}')
            conn.exec_driver_sql("DROP USER IF EXISTS %s@'%%'", (account,))
        root.dispose()
