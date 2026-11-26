from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    db_type: Literal['mariadb', 'mysql', 'postgres'] = 'mariadb'
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    database: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr
    create_database: bool = False
    reset_database: bool = False
    confirm_database: str = Field(default="", max_length=63)
    ssl_mode: Literal['disable', 'require', 'verify-full'] = 'disable'

    @model_validator(mode='after')
    def confirmed_reset(self):
        if self.reset_database and self.confirm_database != self.database:
            raise ValueError('Phải nhập đúng tên database để xác nhận xóa và tạo lại.')
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

