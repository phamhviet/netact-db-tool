import os
from pathlib import Path

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
PREFIXES = {"mariadb": "MARIADB", "postgres": "POSTGRES", "mysql": "MYSQL"}


def environment_value(name, default=""):
    values = dotenv_values(ENV_FILE, interpolate=False) if ENV_FILE.is_file() else {}
    return os.environ.get(name, values.get(name, default))


def database_profile(db_type):
    values = dict(dotenv_values(ENV_FILE, interpolate=False)) if ENV_FILE.is_file() else {}
    prefix = PREFIXES[db_type]

    def read(suffix, default=""):
        key = f"{prefix}_{suffix}"
        return os.environ.get(key, values.get(key, default)) or ""

    password_key = f"{prefix}_PASSWORD"
    try:
        port = int(read("PORT", "5432" if db_type == "postgres" else "3306"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError(f"{prefix}_PORT is invalid in the environment configuration.") from None
    return {
        "host": read("HOST"),
        "port": port,
        "username": read("USER"),
        "database": read("DATABASE"),
        "ssl_mode": read("SSL_MODE", "disable"),
        "password": read("PASSWORD"),
        "password_configured": password_key in os.environ or password_key in values,
    }


def public_database_profiles():
    return {
        kind: {key: value for key, value in database_profile(kind).items() if key != "password"}
        for kind in PREFIXES
    }
