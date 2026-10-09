from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_prefix="BIDWRIGHT_", extra="ignore")

    # Passwords come from .env (`make env` generates them); nothing here has a default.
    db_host: str = "localhost"
    db_port: int = Field(5434, validation_alias="POSTGRES_PORT")
    db_name: str = "bidwright"
    db_password: str = ""
    app_password: str = ""
    dataset_version: str = "v0-synthetic"
    storage_dir_setting: Path = Field(Path("var/blobs"), validation_alias="BIDWRIGHT_STORAGE_DIR")
    anthropic_api_key: str = Field("", validation_alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = "https://api.anthropic.com"
    model_extract: str = "claude-haiku-4-5"
    model_classify: str = "claude-haiku-4-5"
    libreoffice: str = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    session_days: int = 14
    cookie_secure: bool = False

    def _url(self, user: str, password: str) -> str:
        if not password:
            raise RuntimeError(f"no password for the {user} login: run `make env`, or set it in .env")
        return URL.create("postgresql+psycopg", user, password, self.db_host, self.db_port, self.db_name) \
            .render_as_string(hide_password=False)

    @property
    def database_url(self) -> str:
        """Owner login: migrations and the seed."""
        return self._url("bidwright", self.db_password)

    @property
    def app_database_url(self) -> str:
        """API login, which row-level security binds."""
        return self._url("bidwright_app", self.app_password)

    @property
    def storage_dir(self) -> Path:
        d = self.storage_dir_setting
        return d if d.is_absolute() else REPO_ROOT / d

    @property
    def dataset_dir(self) -> Path:
        return REPO_ROOT / "data" / "archive" / self.dataset_version


settings = Settings()
