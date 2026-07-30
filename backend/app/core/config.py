import secrets
import warnings
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    EmailStr,
    HttpUrl,
    PostgresDsn,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = secrets.token_urlsafe(32)
    INTERNAL_RUNTIME_TOKEN: str | None = None
    RUNTIME_WORKER_RELEASE_DIGEST: str | None = None
    MODEL_GATEWAY_URL: str = "http://model-gateway:8090"
    MODEL_GATEWAY_PUBLIC_URL: str | None = None
    GATEWAY_JWT_LEEWAY_SECONDS: int = 30
    ARTIFACT_STORAGE_BACKEND: str = "local"
    ARTIFACT_LOCAL_ROOT: str = "/data/runtime-artifacts"
    ARTIFACT_S3_BUCKET: str | None = None
    ARTIFACT_S3_ENDPOINT: str | None = None
    ARTIFACT_S3_ACCESS_KEY: str | None = None
    ARTIFACT_S3_SECRET_KEY: str | None = None
    ARTIFACT_S3_REGION: str | None = None
    ARTIFACT_MAX_ARCHIVE_BYTES: int = 1024 * 1024 * 1024
    ARTIFACT_MAX_CONCURRENT_UPLOADS: int = 2
    ARTIFACT_TEMP_MIN_FREE_BYTES: int = 2 * 1024 * 1024 * 1024
    ARTIFACT_TEMP_DIR: str | None = None
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 8
    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    ENABLE_PRIVATE_TEST_API: bool = False

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    POSTGRES_SERVER: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn:
        return PostgresDsn.build(
            scheme="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        weak = (
            not value
            or value.lower().startswith("changethis")
            or value.lower()
            in {
                "secret",
                "password",
                "template",
                "example",
            }
        )
        if weak:
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD)
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )
        self._check_default_secret(
            "INTERNAL_RUNTIME_TOKEN", self.INTERNAL_RUNTIME_TOKEN
        )
        if self.ENVIRONMENT != "local" and (
            self.RUNTIME_WORKER_RELEASE_DIGEST is None
            or len(self.RUNTIME_WORKER_RELEASE_DIGEST) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.RUNTIME_WORKER_RELEASE_DIGEST.lower()
            )
        ):
            raise ValueError(
                "RUNTIME_WORKER_RELEASE_DIGEST must be a 64-character hex digest"
            )
        if (
            self.ENVIRONMENT != "local"
            and self.INTERNAL_RUNTIME_TOKEN
            and self.INTERNAL_RUNTIME_TOKEN
            in {self.SECRET_KEY, self.POSTGRES_PASSWORD, self.FIRST_SUPERUSER_PASSWORD}
        ):
            raise ValueError(
                "INTERNAL_RUNTIME_TOKEN must be distinct from other deployment secrets"
            )
        if self.ENVIRONMENT != "local" and self.ENABLE_PRIVATE_TEST_API:
            raise ValueError("Private test API cannot be enabled outside local")

        return self


settings = Settings()  # type: ignore
