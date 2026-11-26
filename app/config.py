from typing import Literal

from app.settings import database_profile

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    db_type: Literal['mariadb', 'mysql', 'postgres'] = 'mariadb'
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    database: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = SecretStr('')
    use_saved_credentials: bool = False
    create_database: bool = False
    ssl_mode: Literal['disable', 'require', 'verify-full'] = 'disable'

    @model_validator(mode='after')
    def saved_credentials(self):
        if self.use_saved_credentials:
            profile = database_profile(self.db_type)
            # Never send a saved server secret to an edited destination/account/TLS.
            if not profile['password_configured'] or any([
                self.host.casefold() != profile['host'].strip().casefold(),
                self.port != profile['port'], self.username != profile['username'].strip(),
                self.ssl_mode != profile['ssl_mode'],
            ]):
                raise ValueError('Cấu hình đã đổi; nhập mật khẩu riêng hoặc dùng đúng cấu hình .env.')
            if self.password.get_secret_value():
                raise ValueError('Chỉ chọn mật khẩu riêng hoặc mật khẩu .env.')
            self.password = SecretStr(profile['password'])
        return self

    @field_validator('ssl_mode')
    @classmethod
    def supported_tls(cls, value, info):
        if value == 'require' and info.data.get('db_type') != 'postgres':
            raise ValueError('MariaDB/MySQL: chọn disable hoặc verify-full.')
        return value

    @field_validator('host', 'username')
    @classmethod
    def clean(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError('Ký tự điều khiển không hợp lệ.')
        return value.strip()

