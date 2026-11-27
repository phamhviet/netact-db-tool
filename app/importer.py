import csv
import shutil
import subprocess
import tempfile
from pathlib import Path
from xml.sax import SAXParseException
from xml.sax.handler import feature_external_ges
from xml.sax.saxutils import XMLGenerator

from defusedxml.common import DefusedXmlException
from defusedxml.expatreader import DefusedExpatParser
from sqlalchemy import Column, MetaData, String, Table, Text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateTable

from app.errors import failure_details

ROOT = Path(__file__).resolve().parents[1]
csv.field_size_limit(16 * 1024 * 1024)


class ImportFailure(Exception):
    pass


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
            "The XML references an unresolved entity. "
            "Export XML containing literal values without external entity/DTD dependencies."
        )


def normalize_xml(xml_path, destination):
    # SAX streams text/attributes without building a tree. XMLGenerator emits no
    # DOCTYPE; the JAR therefore never sees DTD URLs or entity declarations.
    parser = NoExternalDtdParser(forbid_dtd=False, forbid_entities=True, forbid_external=True)
    parser.setFeature(feature_external_ges, False)
    try:
        with destination.open("wb") as stream:
            parser.setContentHandler(CompleteXMLGenerator(stream, encoding="utf-8"))
            with xml_path.open("rb") as source:
                parser.parse(source)
    except DefusedXmlException:
        raise ImportFailure(
            "XML entity declarations or external references are unsupported. "
            "Export literal values instead. "
            "DOCTYPE declarations without entities are supported."
        ) from None
    except SAXParseException as exc:
        raise ImportFailure(
            f"Malformed XML at line {exc.getLineNumber()}, column {exc.getColumnNumber() + 1}. "
            "Check unescaped &, matching tags, encoding and whether the file was truncated."
        ) from None


def convert(xml_path, output):
    if not shutil.which("java"):
        raise ImportFailure("Java was not found. Install Java 11 or later on the server.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="normalized-", dir=output.parent) as temporary:
        normalized = Path(temporary) / xml_path.name
        normalize_xml(xml_path, normalized)
        output.mkdir(exist_ok=True)
        # Let the JVM choose heap size from available RAM; JAVA_TOOL_OPTIONS can override it.
        with (output.parent / "parser.log").open("wb") as log:
            result = subprocess.run(
                [
                    "java",
                    "-Dfile.encoding=UTF-8",
                    "-jar",
                    str(ROOT / "vendor/boda-nokiacmdataparser.jar"),
                    "-i",
                    str(normalized),
                    "-o",
                    str(output),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=output.parent,
                check=False,
            )
    files = sorted(output.rglob("*.csv"))
    if result.returncode or not files:
        raise ImportFailure(
            "The JAR produced no CSV files. Verify that the XML uses Nokia RAML20 and contains managedObject elements."
        )
    return files


def unique_names(raw_names, limit):
    seen = set()
    result = []
    for raw in raw_names:
        base = raw.strip().replace("\x00", "") or "unknown_column"
        candidate = base.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
        n = 1
        while candidate.casefold() in seen:
            suffix = f"_{n}"
            candidate = base.encode("utf-8")[: limit - len(suffix)].decode("utf-8", errors="ignore") + suffix
            n += 1
        seen.add(candidate.casefold())
        result.append(candidate)
    return result


def encoding_for(path):
    for encoding in ("utf-8-sig", "cp1252", "latin-1", "iso-8859-1"):
        try:
            with path.open(encoding=encoding) as stream:
                for _ in stream:
                    pass
            return encoding
        except UnicodeDecodeError:
            pass
    raise ImportFailure("Cannot detect the CSV encoding.")


def script_column_names(headers):
    # Match the supplied script: 64 characters and case-sensitive duplicate handling.
    columns = []
    seen = set()
    for raw in headers:
        original = raw.strip()[:64] or "unknown_column"
        name = original
        counter = 1
        while name in seen:
            suffix = f"_{counter}"
            name = original[: 64 - len(suffix)] + suffix
            counter += 1
        seen.add(name)
        columns.append(name)
    return columns


def import_values(row, column_count, source_filename):
    values = (row + [""] * column_count)[:column_count]
    if source_filename is not None:
        values[0] = source_filename
    return values


def import_csvs(engine, files, progress, *, source_filename=None):
    results = []
    postgres = engine.dialect.name == "postgresql"
    with engine.connect() as conn:
        for index, path in enumerate(files):
            table_name = (
                path.stem[:64]
                if not postgres
                else path.stem.encode("utf-8")[:63].decode("utf-8", errors="ignore")
            )
            report = {
                "table": table_name,
                "file": path.name,
                "rows": 0,
                "rows_failed": 0,
                "status": "imported",
                "columns": [],
                "errors": [],
            }

            def record_error(exc, line=None, report=report):
                # Keep the complete counters, but bound error details for huge imports.
                if len(report["errors"]) < 20:
                    details = failure_details(exc, "csv_to_database", "import", log_error=False)
                    report["errors"].append(
                        {
                            "line": line,
                            "code": details["error_code"],
                            "type": details["error_type"],
                            "message": details["message"],
                        }
                    )

            encoding = encoding_for(path)
            with path.open(encoding=encoding) as stream:
                reader = csv.reader(stream)
                headers = next(reader, None)
                if headers is None:
                    report["status"] = "skipped_empty"
                    results.append(report)
                    continue
                columns = unique_names(headers, 63) if postgres else script_column_names(headers)
                report["columns"] = [
                    {"source": a, "target": b} for a, b in zip(headers, columns, strict=True)
                ]
                filename = source_filename if headers and headers[0].strip().upper() == "FILENAME" else None
                max_lengths = [0] * len(columns)
                for row in reader:
                    for position, value in enumerate(import_values(row, len(columns), filename)):
                        max_lengths[position] = max(max_lengths[position], len(value))
                types = [Text() if length > 255 else String(max(1, length)) for length in max_lengths]
                # A separate MetaData per file permits truncated filenames to reuse the
                # same existing table, just like CREATE TABLE IF NOT EXISTS in the script.
                table = Table(
                    table_name,
                    MetaData(),
                    *(Column(name, kind) for name, kind in zip(columns, types, strict=True)),
                )
                try:
                    if postgres:
                        with conn.begin_nested():
                            conn.execute(CreateTable(table, if_not_exists=True))
                    else:
                        conn.execute(CreateTable(table, if_not_exists=True))
                except SQLAlchemyError as exc:
                    report["status"] = "failed_create"
                    record_error(exc)
                    results.append(report)
                    progress(
                        45 + int(50 * (index + 1) / max(1, len(files))),
                        f"Skipping table {table_name}: table creation failed",
                    )
                    continue
                stream.seek(0)
                next(reader)
                # Positional insert deliberately matches the original script when a
                # truncated table name reuses a table with different column names.
                quoted = engine.dialect.identifier_preparer.quote_identifier(table_name)
                marker = "?" if engine.dialect.paramstyle == "qmark" else "%s"
                placeholders = ", ".join(marker for _ in columns)
                # Use the native mariadb '?' placeholders, matching the source script.
                statement = f"INSERT INTO {quoted} VALUES ({placeholders})"
                for line, row in enumerate(reader, 2):
                    parameters = tuple(import_values(row, len(columns), filename))
                    try:
                        if postgres:
                            # PostgreSQL needs a savepoint to continue after a failed row.
                            with conn.begin_nested():
                                conn.exec_driver_sql(statement, parameters)
                        else:
                            conn.exec_driver_sql(statement, parameters)
                        report["rows"] += 1
                    except SQLAlchemyError as exc:
                        report["rows_failed"] += 1
                        report["status"] = "partial"
                        record_error(exc, line)
            results.append(report)
            progress(
                45 + int(50 * (index + 1) / max(1, len(files))),
                f"{table_name}: {report['rows']:,} rows imported, {report['rows_failed']:,} failed rows",
            )
        conn.commit()
    return results
