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
from sqlalchemy import Column, MetaData, Table, Text, create_engine, inspect
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
csv.field_size_limit(16 * 1024 * 1024)


class ImportFailure(Exception):
    pass


def engine_for(config, database=None):
    driver = {'mariadb': 'mysql+pymysql', 'mysql': 'mysql+pymysql', 'postgres': 'postgresql+psycopg'}[config.db_type]
    options = {'connect_timeout': 10}
    if config.db_type == 'postgres':
        options['sslmode'] = config.ssl_mode
    elif config.ssl_mode == 'verify-full':
        options['ssl'] = {'check_hostname': True}
    return create_engine(URL.create(driver, username=config.username, password=config.password.get_secret_value(),
                         host=config.host, port=config.port, database=database or config.database),
                         connect_args=options, pool_pre_ping=True)


def prepare_database(config):
    if config.create_database:
        admin = engine_for(config, 'postgres' if config.db_type == 'postgres' else 'information_schema')
        try:
            with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
                name = admin.dialect.identifier_preparer.quote_identifier(config.database)
                suffix = ' CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci' if config.db_type != 'postgres' else ''
                # Deliberately no IF NOT EXISTS: creation must never silently reuse a database.
                conn.exec_driver_sql(f'CREATE DATABASE {name}{suffix}')
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
    for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            with path.open(encoding=encoding) as stream:
                for _ in stream:
                    pass
            return encoding
        except UnicodeDecodeError:
            pass
    raise ImportFailure('Không đọc được bảng mã CSV.')


def import_csvs(engine, files, progress):
    metadata = MetaData()
    limit = 63 if engine.dialect.name == 'postgresql' else 64
    table_names = unique_names([p.stem for p in files], limit)
    plans = []
    for path, name in zip(files, table_names):
        encoding = encoding_for(path)
        with path.open(encoding=encoding, newline='') as stream:
            headers = next(csv.reader(stream), [])
        if not headers:
            continue
        columns = unique_names(headers, limit)
        # Preserve IDs, leading zeroes and long NetAct parameter values as text.
        table = Table(name, metadata, *(Column(c, Text) for c in columns))
        plans.append((path, encoding, table, headers))
    if not plans:
        raise ImportFailure('Không có bảng dữ liệu trong CSV.')
    results = []
    created = []
    try:
        with engine.begin() as conn:
            existing = {name.casefold() for name in inspect(conn).get_table_names()}
            if any(table.name.casefold() in existing for _, _, table, _ in plans):
                raise ImportFailure('Bảng đích đã tồn tại. Hãy chọn database mới hoặc database chưa có các bảng này.')
            for index, (path, encoding, table, headers) in enumerate(plans):
                table.create(conn, checkfirst=False)
                created.append(table)
                count = 0
                batch = []
                with path.open(encoding=encoding, newline='') as stream:
                    reader = csv.reader(stream)
                    next(reader)
                    for line, row in enumerate(reader, 2):
                        if not row:
                            continue
                        if len(row) > len(table.columns):
                            raise ImportFailure(f'CSV {path.name}, dòng {line}: thừa cột, đã dừng nhập.')
                        values = row + [''] * (len(table.columns) - len(row))
                        if any('\x00' in value for value in values):
                            raise ImportFailure(f'CSV {path.name}, dòng {line}: chứa ký tự NUL không hợp lệ.')
                        batch.append(dict(zip(table.columns.keys(), values)))
                        if len(batch) >= 500:
                            conn.execute(table.insert(), batch)
                            count += len(batch)
                            batch.clear()
                    if batch:
                        conn.execute(table.insert(), batch)
                        count += len(batch)
                results.append({'table': table.name, 'rows': count,
                                'columns': [{'source': a, 'target': b} for a, b in zip(headers, table.columns.keys())]})
                progress(45 + int(50 * (index + 1) / len(plans)), f'Đã nhập {table.name}: {count:,} dòng')
    except Exception as error:
        # MariaDB/MySQL DDL commits implicitly; remove only tables created by this job.
        if engine.dialect.name == 'mysql' and created:
            try:
                with engine.begin() as conn:
                    for table in reversed(created):
                        table.drop(conn, checkfirst=True)
            except Exception:
                raise ImportFailure('Nhập thất bại, không thể dọn bảng vừa tạo. Kiểm tra các bảng của tác vụ trước khi thử lại.') from error
        raise
    return results
