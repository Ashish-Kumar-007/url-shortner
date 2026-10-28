from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/url_shortener"
    )
    base_url: str = "http://localhost:8000"
    code_generation_attempts: int = 5

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
