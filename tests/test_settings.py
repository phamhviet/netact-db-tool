import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import settings
from app.config import ConnectionConfig
from app.main import app

client = TestClient(app)


@pytest.fixture
def configured_env(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text('\n'.join(
        f"{prefix}_HOST=db-{kind}.internal\n{prefix}_PORT={port}\n{prefix}_USER=test_user\n"
        f"{prefix}_PASSWORD='test#secret$literal'\n{prefix}_DATABASE=netact_test\n{prefix}_SSL_MODE=disable"
        for kind, prefix, port in [('mariadb', 'MARIADB', 3307), ('postgres', 'POSTGRES', 5433), ('mysql', 'MYSQL', 3308)]
    ))
    monkeypatch.setattr(settings, 'ENV_FILE', path)
    # Isolate these tests from actual injected environment bindings, by name only.
    for prefix in settings.PREFIXES.values():
        for suffix in ['HOST', 'PORT', 'USER', 'PASSWORD', 'DATABASE', 'SSL_MODE']:
            monkeypatch.delenv(f'{prefix}_{suffix}', raising=False)
    return path


@pytest.mark.parametrize('kind,port', [('mariadb', 3307), ('postgres', 5433), ('mysql', 3308)])
def test_saved_profiles_without_password_in_response(configured_env, kind, port):
    response = client.get('/api/db-presets')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert 'test#secret$literal' not in response.text
    profile = response.json()[kind]
    assert 'password' not in profile
    assert profile['password_configured'] is True
    assert profile['port'] == port
    config = ConnectionConfig(db_type=kind, host=profile['host'], port=port, username=profile['username'],
                              database='netact_target', ssl_mode=profile['ssl_mode'], use_saved_credentials=True)
    assert config.password.get_secret_value() == 'test#secret$literal'
    assert 'test#secret$literal' not in config.model_dump_json()


@pytest.mark.parametrize('changed', [{'host': 'other.internal'}, {'port': 9999}, {'username': 'another'}, {'ssl_mode': 'verify-full'}])
def test_server_password_bound_to_original_connection(configured_env, changed):
    body = dict(db_type='mariadb', host='db-mariadb.internal', port=3307, username='test_user',
                database='test', use_saved_credentials=True)
    body.update(changed)
    with pytest.raises(ValidationError):
        ConnectionConfig(**body)
    response = client.post('/api/connection', json=body)
    assert response.status_code == 422
    assert 'test#secret$literal' not in response.text


def test_manual_password_and_environment_override(configured_env, monkeypatch):
    monkeypatch.setenv('MARIADB_HOST', 'override.internal')
    monkeypatch.setenv('MARIADB_PASSWORD', 'override-secret')
    profile = settings.database_profile('mariadb')
    assert profile['host'] == 'override.internal'
    assert profile['password'] == 'override-secret'
    config = ConnectionConfig(db_type='mariadb', host='custom.internal', port=3306, username='custom',
                              database='test', password='manual-secret')
    assert config.password.get_secret_value() == 'manual-secret'
    assert 'override-secret' not in client.get('/api/db-presets').text


def test_missing_password_not_marked_configured(configured_env):
    configured_env.write_text('MARIADB_HOST=localhost\nMARIADB_USER=root\n')
    assert settings.database_profile('mariadb')['password_configured'] is False
    with pytest.raises(ValidationError):
        ConnectionConfig(db_type='mariadb', host='localhost', port=3306, username='root',
                         database='test', use_saved_credentials=True)
