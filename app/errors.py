import csv
import errno
import logging
import re

from sqlalchemy.exc import DBAPIError

logger = logging.getLogger("netact")
STAGES = {
    "xml_to_csv": "XML to CSV conversion",
    "database_connect": "Database connection/setup",
    "csv_to_database": "Table creation/CSV import",
}
DATABASE_ERRORS = {
    "1045": "Invalid credentials or this account cannot connect from the web server.",
    "1044": "The server returned Access denied. Compare the actual account and server response.",
    "1049": "The database does not exist. Create it first or enable Create database.",
    "1007": "The database already exists or another process is creating it. Check the target and DROP/CREATE permissions.",
    "1142": "This account lacks the required table privileges (CREATE/INSERT/SELECT/DROP).",
    "1050": "The table already exists in the target database.",
    "1118": "The table exceeds the MariaDB/MySQL row-size limit.",
    "1117": "The table exceeds the database column-count limit.",
    "1406": "A value exceeds the column capacity; MariaDB/MySQL TEXT supports up to 65,535 bytes.",
    "1366": "A value or encoding is incompatible with its column. Check UTF-8/utf8mb4.",
    "2003": "Cannot reach the database server. Check host, port, firewall and network access.",
    "2005": "Cannot resolve the database hostname.",
    "2006": "The database closed the connection. Check DB logs, timeouts and max_allowed_packet.",
    "2013": "Connection lost during the query. Check DB logs, network and timeouts.",
    "1153": "The packet exceeds the database max_allowed_packet setting.",
    "28P01": "Invalid PostgreSQL credentials.",
    "28000": "PostgreSQL rejected authentication. Check the account and pg_hba.conf.",
    "3D000": "The database does not exist. Create it first or enable Create database.",
    "42501": "The PostgreSQL account lacks database/schema/table privileges.",
    "42P04": "The database already exists or another process is creating it. Check the target and DROP/CREATE permissions.",
    "42P07": "The table already exists in the target schema.",
    "54011": "The table exceeds the PostgreSQL column-count limit.",
    "54000": "The data exceeds a PostgreSQL limit.",
    "22021": "The data contains a character or encoding PostgreSQL cannot accept.",
    "22001": "A value exceeds the target column capacity.",
    "53100": "The PostgreSQL server is out of disk space.",
    "53200": "The PostgreSQL server is out of memory.",
    "53300": "The PostgreSQL server has no available connections.",
    "08001": "Cannot establish a PostgreSQL connection.",
    "08006": "The PostgreSQL connection was lost.",
}


class DatabaseOperationError(Exception):
    def __init__(self, operation, original, database, state="unchanged"):
        super().__init__("Database operation failed")
        self.operation = operation
        self.original = original
        self.database = database
        self.state = state


def database_error_code(original):
    number = getattr(original, "errno", None)
    if isinstance(number, int):
        return str(number)
    if original.args and isinstance(original.args[0], int):
        return str(original.args[0])
    sqlstate = getattr(original, "sqlstate", None)
    if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isascii() and sqlstate.isalnum():
        return sqlstate
    return None


def access_denied_reply(exc, password):
    if not isinstance(exc, DBAPIError) or database_error_code(exc.orig) not in ("1044", "1045"):
        return None
    reply = getattr(exc.orig, "errmsg", None)
    if not isinstance(reply, str):
        reply = next((value for value in exc.orig.args if isinstance(value, str)), None)
    if not reply:
        return None
    if password:
        reply = reply.replace(password, "[redacted]")
    # Allow only the standard account/database error shape, not arbitrary driver
    # output which could contain SQL, parameter values or authentication data.
    if re.fullmatch(
        r"Access denied for user '[^'\r\n]*'@'[^'\r\n]*'(?: to database '[^'\r\n]*'| \(using password: (?:YES|NO)\))",
        reply,
    ):
        return reply[:500]
    return None


def failure_details(exc, stage, job_id, log_error=True, password=None):
    operation = exc.operation if isinstance(exc, DatabaseOperationError) else None
    database = exc.database if isinstance(exc, DatabaseOperationError) else None
    state = exc.state if isinstance(exc, DatabaseOperationError) else None
    if isinstance(exc, DatabaseOperationError):
        exc = exc.original
    code = None
    reason = "Unclassified error. Use the stage and error type below for diagnosis."
    if isinstance(exc, DBAPIError):
        original = exc.orig
        code = database_error_code(original)
        reason = DATABASE_ERRORS.get(
            code,
            "The database rejected the operation or the connection failed. Check DB logs and account privileges.",
        )
    elif isinstance(exc, OSError):
        code = f"OS-{exc.errno}" if isinstance(exc.errno, int) else None
        reason = {
            errno.ENOSPC: "The web server is out of temporary disk space.",
            errno.EACCES: "The web server cannot access temporary files or execute Java.",
            errno.ENOMEM: "The web server is out of memory.",
        }.get(exc.errno, "Cannot read/write a file or start Java.")
    elif isinstance(exc, csv.Error):
        reason = "Cannot read the CSV. A field may exceed the CSV reader limit or the CSV may be malformed."
    elif isinstance(exc, UnicodeError):
        reason = "Cannot decode the data. Check the XML/CSV encoding."
    label = STAGES[stage]
    if operation:
        label = {
            "server_connect": "Connect to database server (without selecting the target)",
            "database_exists": f"Check database [{database}] existence",
            "drop_database": f"DROP DATABASE [{database}]",
            "create_database": f"CREATE DATABASE [{database}]",
            "target_connect": f"Open database [{database}]",
        }[operation]
    if code in ("1044", "42501") and operation:
        reason = {
            "database_exists": "The server rejected the database existence check.",
            "drop_database": "This account cannot DROP this database.",
            "create_database": "This account cannot CREATE this database.",
            "target_connect": "This account cannot access the target database. Check the database name and account.",
            "server_connect": "The server rejected access. Check the account selected for this client host.",
        }[operation]
    if code == "1044" and operation:
        reason = "The server rejected the operation. Compare the original response and the actual account/server using DB diagnostics."
    server_reply = access_denied_reply(exc, password)
    if server_reply:
        reason += f" DB response: {server_reply}."
    suffix = "A newly created database may remain; check it before retrying."
    if state == "dropped":
        suffix = "DROP succeeded, but CREATE was not confirmed successful."
    elif state == "created":
        suffix = "The database was recreated, but the application could not open it."
    elif operation in ("server_connect", "database_exists") or (
        state == "unchanged"
        and operation in ("drop_database", "target_connect")
        and code in ("1044", "1045", "42501", "28P01")
    ):
        suffix = "The application has not dropped or recreated the database."
    kind = type(exc).__name__
    # Never log str(exc), SQL, parameter values, credentials or XML contents.
    if log_error:
        logger.error(
            "job=%s stage=%s operation=%s exception=%s code=%s",
            job_id,
            stage,
            operation or "none",
            kind,
            code or "none",
        )
    identifier = f"{kind}, code {code}" if code else kind
    return {
        "stage": stage,
        "operation": operation,
        "server_reply": server_reply,
        "error_code": code,
        "error_type": kind,
        "message": f"{label} failed ({identifier}). {reason} {suffix}",
    }
