from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./local.db"
    app_name: str = "app"
    log_level: str = "INFO"


settings = Settings()
