import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import administration_engine, database_exists


def grant_scopes(rows):
    # SHOW GRANTS may include password hashes / authentication clauses. Return
    # only the privilege+scope prefix, discarding everything from TO onward.
    result = []
    for row in rows:
        match = re.match(r"^(GRANT\s+.+?\s+ON\s+.+?)\s+TO\s+", row, re.IGNORECASE)
        if match:
            result.append(match.group(1))
    return result


def diagnose_connection(config):
    engine = administration_engine(config)
    try:
        with engine.connect() as conn:
            if config.db_type == "postgres":
                account, login, server, port, version = conn.execute(
                    text(
                        "SELECT current_user, session_user, inet_server_addr()::text, inet_server_port(), version()"
                    )
                ).one()
                role = conn.execute(
                    text("SELECT rolcreatedb, rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user")
                ).one()
                server_info = {
                    "authenticated_as": account,
                    "login_identity": login,
                    "server_hostname": server,
                    "server_port": port,
                    "version": version,
                    "can_create_database": role[0],
                    "superuser": role[1],
                }
            else:
                account, login, server, port, version, charset, collation = conn.exec_driver_sql(
                    "SELECT CURRENT_USER(), USER(), @@hostname, @@port, VERSION(), "
                    "@@character_set_server, @@collation_server"
                ).one()
                server_info = {
                    "authenticated_as": account,
                    "login_identity": login,
                    "server_hostname": server,
                    "server_port": port,
                    "version": version,
                    "server_charset": charset,
                    "server_collation": collation,
                    "grant_scopes": grant_scopes(conn.exec_driver_sql("SHOW GRANTS").scalars().all()),
                }
            if config.db_type != "postgres":
                try:
                    server_info["current_role"] = conn.exec_driver_sql("SELECT CURRENT_ROLE()").scalar_one()
                except SQLAlchemyError:
                    server_info["current_role"] = "CURRENT_ROLE() is not supported"
            return {
                "connection": {
                    "db_type": config.db_type,
                    "driver": engine.dialect.driver,
                    "host": config.host,
                    "port": config.port,
                    "username": config.username,
                    "database": config.database,
                    "create_database": config.create_database,
                    "password_source": "env" if config.use_saved_credentials else "form",
                },
                "server": server_info,
                "database_visible": database_exists(conn, config),
                "read_only": True,
            }
    finally:
        engine.dispose()
