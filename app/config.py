from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "BotBoard"
    SECRET_KEY: str
    BASE_URL: str = "http://localhost:8080"
    ENV: str = "development"

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "botboard"
    POSTGRES_USER: str = "botboard"
    POSTGRES_PASSWORD: str = "botboard_pass"
    DATABASE_URL: str | None = None

    REDIS_URL: str | None = None

    SMTP_HOST: str | None = None
    SMTP_PORT: int | None = None
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str | None = None
    SMTP_TLS: bool = True

    MAGIC_LINK_EXP_MIN: int = 15
    ACCESS_TOKEN_EXP_MIN: int = 120

    RATE_LIMIT_BURST: int = 20
    RATE_LIMIT_WINDOW_S: int = 60

    AUTO_PROMOTE_FIRST_ADMIN: bool = True
    ADMIN_ALLOWLIST: str = ""

    @property
    def db_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

settings = Settings()  # type: ignore
