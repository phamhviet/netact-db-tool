"""Read-only comparison of the original MariaDB script and this app's .env."""
import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ConnectionConfig
from app.diagnostics import diagnose_connection, grant_scopes
from app.errors import DatabaseOperationError, failure_details
from app.settings import database_profile


def read_reference(path):
    # Never import/execute a reference script: it may contain DROP DATABASE.
    keys = {'username', 'password', 'host', 'port', 'database_name'}
    values = {}
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in keys:
                    try:
                        values[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    if not keys.issubset(values):
        raise ValueError('Reference script must use literal username/password/host/port/database_name settings.')
    return ConnectionConfig(db_type='mariadb', host=values['host'], port=values['port'],
                            username=values['username'], password=values['password'],
                            database=values['database_name'], create_database=True)


def diagnose_original(config):
    import mariadb
    # Exactly the connect arguments in the reference script; no SQLAlchemy setup.
    conn = mariadb.connect(user=config.username, password=config.password.get_secret_value(),
                           host=config.host, port=config.port)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT CURRENT_USER(), USER(), @@hostname, @@port, VERSION(), '
                           '@@character_set_server, @@collation_server')
            account, login, host, port, version, charset, collation = cursor.fetchone()
            cursor.execute('SHOW GRANTS')
            scopes = grant_scopes([row[0] for row in cursor.fetchall()])
            cursor.execute('SHOW DATABASES')
            names = [row[0] for row in cursor.fetchall()]
            cursor.execute('SELECT @@lower_case_table_names')
            folded = cursor.fetchone()[0] != 0
            exists = config.database.casefold() in {name.casefold() for name in names} if folded else config.database in names
            return {'connection': {'driver': 'mariadb.connect (direct)', 'host': config.host, 'port': config.port,
                                   'username': config.username, 'database': config.database},
                    'server': {'authenticated_as': account, 'login_identity': login, 'server_hostname': host,
                               'server_port': port, 'version': version, 'server_charset': charset,
                               'server_collation': collation, 'grant_scopes': scopes},
                    'database_visible': exists, 'read_only': True}
        finally:
            cursor.close()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Compare DB settings without executing the original script or changing any database.')
    parser.add_argument('reference', help='Path to the original netact_mariadb_import.py')
    args = parser.parse_args()
    try:
        reference = read_reference(args.reference)
        profile = database_profile('mariadb')
        current = ConnectionConfig(db_type='mariadb', host=profile['host'], port=profile['port'],
                                   username=profile['username'], password=profile['password'],
                                   database=profile['database'] or reference.database,
                                   ssl_mode=profile['ssl_mode'], create_database=True)
    except Exception:
        print('Không đọc được cấu hình. Kiểm tra đường dẫn script, các hằng cấu hình và .env. Không có database nào bị thay đổi.')
        return 1
    output = {'read_only': True, 'settings_match': {
        'host': reference.host == current.host, 'port': reference.port == current.port,
        'username': reference.username == current.username,
        'password': reference.password.get_secret_value() == current.password.get_secret_value(),
    }}
    for label, config in [('original_script', reference), ('web_env', current)]:
        try:
            output[label] = diagnose_original(config) if label == 'original_script' else diagnose_connection(config)
        except Exception as exc:
            if isinstance(getattr(exc, 'errno', None), int):
                from sqlalchemy.exc import DBAPIError
                exc = DBAPIError(None, None, exc)
            output[label] = {'error': failure_details(DatabaseOperationError('server_connect', exc, config.database),
                                                     'database_connect', 'compare-config', log_error=False,
                                                     password=config.password.get_secret_value())['message']}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
