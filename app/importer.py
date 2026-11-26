import csv
import shutil
import subprocess
import tempfile
from xml.sax import SAXParseException
from xml.sax.handler import feature_external_ges
from xml.sax.saxutils import XMLGenerator
from pathlib import Path

from defusedxml.common import DefusedXmlException
from defusedxml.expatreader import DefusedExpatParser
from sqlalchemy import Column, MetaData, Table, Text, String, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.schema import CreateTable
from sqlalchemy.exc import SQLAlchemyError

from app.errors import failure_details, DatabaseOperationError

ROOT = Path(__file__).resolve().parents[1]
csv.field_size_limit(16 * 1024 * 1024)
_DEFAULT_DATABASE = object()


class ImportFailure(Exception):
    pass


def engine_for(config, database=_DEFAULT_DATABASE):
    driver = {'mariadb': 'mysql+pymysql', 'mysql': 'mysql+pymysql', 'postgres': 'postgresql+psycopg'}[config.db_type]
    options = {'connect_timeout': 10}
    if config.db_type == 'postgres':
        options['sslmode'] = config.ssl_mode
    elif config.ssl_mode == 'verify-full':
        options['ssl'] = {'check_hostname': True}
    return create_engine(URL.create(driver, username=config.username, password=config.password.get_secret_value(),
                         host=config.host, port=config.port, database=config.database if database is _DEFAULT_DATABASE else database),
                         connect_args=options, pool_pre_ping=True)


def administration_engine(config):
    # Match mariadb.connect(user=..., password=..., host=..., port=...) in the
    # supplied script: no database is selected before DROP/CREATE on MySQL/MariaDB.
    return engine_for(config, 'postgres' if config.db_type == 'postgres' else None)


def database_exists(connection, config):
    if config.db_type == 'postgres':
        return connection.execute(text('SELECT 1 FROM pg_catalog.pg_database WHERE datname = :name'),
                                  {'name': config.database}).scalar() is not None
    # SHOW DATABASES lists the schemas visible to this account; no default
    # information_schema connection or global SHOW DATABASES privilege is needed.
    names = connection.exec_driver_sql('SHOW DATABASES').scalars().all()
    case_insensitive = connection.exec_driver_sql('SELECT @@lower_case_table_names').scalar_one() != 0
    if case_insensitive:
        return config.database.casefold() in {name.casefold() for name in names}
    return config.database in names


def prepare_database(config):
    if config.create_database:
        admin = administration_engine(config)
        operation = 'server_connect'
        state = 'unchanged'
        try:
            with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
                name = admin.dialect.identifier_preparer.quote_identifier(config.database)
                suffix = ' CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci' if config.db_type != 'postgres' else ''
                operation = 'database_exists'
                if database_exists(conn, config):
                    operation = 'drop_database'
                    conn.exec_driver_sql(f'DROP DATABASE IF EXISTS {name}')
                    state = 'dropped'
                operation = 'create_database'
                conn.exec_driver_sql(f'CREATE DATABASE {name}{suffix}')
        except SQLAlchemyError as exc:
            raise DatabaseOperationError(operation, exc, config.database, state) from None
        finally:
            admin.dispose()
    return engine_for(config)


class NoExternalDtdParser(DefusedExpatParser):
    def defused_external_entity_ref_handler(self, context, base, sysid, pubid):
        if context is None:
            # An external DTD subset: acknowledge without opening its URL/file.
            return 1
        return super().defused_external_entity_ref_handler(context, base, sysid, pubid)


class CompleteXMLGenerator(XMLGenerator):
    def skippedEntity(self, name):
        # Skipping a DTD must never silently remove an unresolved parameter value.
        raise ImportFailure(
            'XML tham chiếu entity chưa có giá trị trực tiếp. '
            'Hãy xuất lại XML không phụ thuộc entity/DTD bên ngoài.'
        )


def normalize_xml(xml_path, destination):
    # SAX streams text/attributes without building a tree. XMLGenerator emits no
    # DOCTYPE; the JAR therefore never sees DTD URLs or entity declarations.
    parser = NoExternalDtdParser(forbid_dtd=False, forbid_entities=True, forbid_external=True)
    parser.setFeature(feature_external_ges, False)
    try:
        with destination.open('wb') as stream:
            parser.setContentHandler(CompleteXMLGenerator(stream, encoding='utf-8'))
            with xml_path.open('rb') as source:
                parser.parse(source)
    except DefusedXmlException:
        raise ImportFailure(
            'XML có khai báo entity hoặc tham chiếu bên ngoài không được hỗ trợ. '
            'Hãy xuất lại XML với giá trị trực tiếp thay cho entity. '
            'DOCTYPE không chứa entity vẫn được hỗ trợ.'
        ) from None
    except SAXParseException as exc:
        raise ImportFailure(
            f'XML không đúng cú pháp tại dòng {exc.getLineNumber()}, cột {exc.getColumnNumber() + 1}. '
            'Kiểm tra ký tự &, thẻ đóng/mở, encoding và file XML có bị cắt thiếu hay không.'
        ) from None


def convert(xml_path, output):
    if not shutil.which('java'):
        raise ImportFailure('Không tìm thấy Java. Cần Java 11 trở lên trên máy chủ.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='normalized-', dir=output.parent) as temporary:
        normalized = Path(temporary) / xml_path.name
        normalize_xml(xml_path, normalized)
        output.mkdir(exist_ok=True)
        # Let the JVM choose heap size from available RAM; JAVA_TOOL_OPTIONS can override it.
        with (output.parent / 'parser.log').open('wb') as log:
            result = subprocess.run(['java', '-Dfile.encoding=UTF-8', '-jar',
                                     str(ROOT / 'vendor/boda-nokiacmdataparser.jar'),
                                     '-i', str(normalized), '-o', str(output)],
                                    stdout=log, stderr=subprocess.STDOUT, cwd=output.parent)
    files = sorted(output.rglob('*.csv'))
    if result.returncode or not files:
        raise ImportFailure('JAR không tạo được CSV. Kiểm tra XML đúng định dạng Nokia RAML20 và có managedObject.')
    return files


def unique_names(raw_names, limit):
    seen = set()
    result = []
    for raw in raw_names:
        base = raw.strip().replace('\x00', '') or 'unknown_column'
        candidate = base.encode('utf-8')[:limit].decode('utf-8', errors='ignore')
        n = 1
        while candidate.casefold() in seen:
            suffix = f'_{n}'
            candidate = base.encode('utf-8')[:limit-len(suffix)].decode('utf-8', errors='ignore') + suffix
            n += 1
        seen.add(candidate.casefold())
        result.append(candidate)
    return result


def encoding_for(path):
    for encoding in ('utf-8-sig', 'cp1252', 'latin-1', 'iso-8859-1'):
        try:
            with path.open(encoding=encoding) as stream:
                for _ in stream:
                    pass
            return encoding
        except UnicodeDecodeError:
            pass
    raise ImportFailure('Không đọc được bảng mã CSV.')


def script_column_names(headers):
    # Match the supplied script: 64 characters and case-sensitive duplicate handling.
    columns = []
    seen = set()
    for raw in headers:
        original = raw.strip()[:64] or 'unknown_column'
        name = original
        counter = 1
        while name in seen:
            suffix = f'_{counter}'
            name = original[:64-len(suffix)] + suffix
            counter += 1
        seen.add(name)
        columns.append(name)
    return columns


def import_csvs(engine, files, progress):
    results = []
    postgres = engine.dialect.name == 'postgresql'
    with engine.connect() as conn:
        for index, path in enumerate(files):
            table_name = path.stem[:64] if not postgres else path.stem.encode('utf-8')[:63].decode('utf-8', errors='ignore')
            report = {'table': table_name, 'file': path.name, 'rows': 0, 'rows_failed': 0,
                      'status': 'imported', 'columns': [], 'errors': []}

            def record_error(exc, line=None):
                # Keep the complete counters, but bound error details for huge imports.
                if len(report['errors']) < 20:
                    details = failure_details(exc, 'csv_to_database', 'import', log_error=False)
                    report['errors'].append({'line': line, 'code': details['error_code'],
                                             'type': details['error_type'], 'message': details['message']})

            encoding = encoding_for(path)
            with path.open(encoding=encoding) as stream:
                reader = csv.reader(stream)
                headers = next(reader, None)
                if headers is None:
                    report['status'] = 'skipped_empty'
                    results.append(report)
                    continue
                columns = unique_names(headers, 63) if postgres else script_column_names(headers)
                report['columns'] = [{'source': a, 'target': b} for a, b in zip(headers, columns)]
                max_lengths = [0] * len(columns)
                for row in reader:
                    for position, value in enumerate(row[:len(columns)]):
                        max_lengths[position] = max(max_lengths[position], len(value))
                types = [Text() if length > 255 else String(max(1, length)) for length in max_lengths]
                # A separate MetaData per file permits truncated filenames to reuse the
                # same existing table, just like CREATE TABLE IF NOT EXISTS in the script.
                table = Table(table_name, MetaData(), *(Column(name, kind) for name, kind in zip(columns, types)))
                try:
                    if postgres:
                        with conn.begin_nested():
                            conn.execute(CreateTable(table, if_not_exists=True))
                    else:
                        conn.execute(CreateTable(table, if_not_exists=True))
                except SQLAlchemyError as exc:
                    report['status'] = 'failed_create'
                    record_error(exc)
                    results.append(report)
                    progress(45 + int(50 * (index + 1) / max(1, len(files))), f'Bỏ qua bảng {table_name}: lỗi tạo bảng')
                    continue
                stream.seek(0)
                next(reader)
                # Positional insert deliberately matches the original script when a
                # truncated table name reuses a table with different column names.
                quoted = engine.dialect.identifier_preparer.quote_identifier(table_name)
                placeholders = ', '.join('%s' for _ in columns)
                # Both drivers use %s; the dialect preparer already escapes literal %.
                statement = f'INSERT INTO {quoted} VALUES ({placeholders})'
                for line, row in enumerate(reader, 2):
                    values = (row + [''] * len(columns))[:len(columns)]
                    parameters = tuple(values)
                    try:
                        if postgres:
                            # PostgreSQL needs a savepoint to continue after a failed row.
                            with conn.begin_nested():
                                conn.exec_driver_sql(statement, parameters)
                        else:
                            conn.exec_driver_sql(statement, parameters)
                        report['rows'] += 1
                    except SQLAlchemyError as exc:
                        report['rows_failed'] += 1
                        report['status'] = 'partial'
                        record_error(exc, line)
            results.append(report)
            progress(45 + int(50 * (index + 1) / max(1, len(files))),
                     f'{table_name}: {report["rows"]:,} dòng đã nhập, {report["rows_failed"]:,} dòng lỗi')
        conn.commit()
    return results
