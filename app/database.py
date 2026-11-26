from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

from app.errors import DatabaseOperationError

_DEFAULT_DATABASE = object()


def engine_for(config, database=_DEFAULT_DATABASE):
    driver = {
        "mariadb": "mariadb+mariadbconnector",
        "mysql": "mysql+pymysql",
        "postgres": "postgresql+psycopg",
    }[config.db_type]
    options = {"connect_timeout": 10}
    if config.db_type == "postgres":
        options["sslmode"] = config.ssl_mode
    elif config.ssl_mode == "verify-full":
        if config.db_type == "mariadb":
            options.update(ssl=True, ssl_verify_cert=True)
        else:
            options["ssl"] = {"check_hostname": True}
    return create_engine(
        URL.create(
            driver,
            username=config.username,
            password=config.password.get_secret_value(),
            host=config.host,
            port=config.port,
            database=config.database if database is _DEFAULT_DATABASE else database,
        ),
        connect_args=options,
        pool_pre_ping=True,
    )


def administration_engine(config):
    # Match mariadb.connect(user=..., password=..., host=..., port=...) in the
    # supplied script: no database is selected before DROP/CREATE on MySQL/MariaDB.
    return engine_for(config, "postgres" if config.db_type == "postgres" else None)


def database_exists(connection, config):
    if config.db_type == "postgres":
        return (
            connection.execute(
                text("SELECT 1 FROM pg_catalog.pg_database WHERE datname = :name"), {"name": config.database}
            ).scalar()
            is not None
        )
    # SHOW DATABASES lists the schemas visible to this account; no default
    # information_schema connection or global SHOW DATABASES privilege is needed.
    names = connection.exec_driver_sql("SHOW DATABASES").scalars().all()
    case_insensitive = connection.exec_driver_sql("SELECT @@lower_case_table_names").scalar_one() != 0
    if case_insensitive:
        return config.database.casefold() in {name.casefold() for name in names}
    return config.database in names


def prepare_database(config):
    if config.create_database:
        admin = administration_engine(config)
        operation = "server_connect"
        state = "unchanged"
        try:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                name = admin.dialect.identifier_preparer.quote_identifier(config.database)
                operation = "database_exists"
                if database_exists(conn, config):
                    operation = "drop_database"
                    conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {name}")
                    state = "dropped"
                operation = "create_database"
                conn.exec_driver_sql(f"CREATE DATABASE {name}")
        except SQLAlchemyError as exc:
            raise DatabaseOperationError(operation, exc, config.database, state) from None
        finally:
            admin.dispose()
    return engine_for(config)
