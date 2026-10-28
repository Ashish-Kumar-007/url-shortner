from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5433/url_shortener"
    )
    base_url: str = "http://localhost:8000"
    code_generation_attempts: int = Field(default=5, ge=1, le=10)
    db_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    db_statement_timeout_ms: int = Field(default=5000, ge=1, le=120000)
    db_pool_timeout_seconds: int = Field(default=5, ge=1, le=60)
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    analytics_batch_size: int = Field(default=500, ge=1, le=10000)
    analytics_poll_interval_seconds: float = Field(default=0.25, gt=0, le=10)
    create_rate_limit_count: int = Field(default=60, ge=1, le=10000)
    create_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    trust_proxy_headers: bool = False
    block_private_destinations: bool = True
    seo_indexable: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
