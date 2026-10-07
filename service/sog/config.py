from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    sog_database_url: str = "postgresql+psycopg://sog:sog@localhost:5434/sog"
    sog_dataset_version: str = "v0-synthetic"
    sog_storage_dir: Path = Path("var/blobs")
    anthropic_api_key: str = ""
    sog_anthropic_base_url: str = "https://api.anthropic.com"
    sog_model_extract: str = "claude-haiku-4-5"
    sog_model_classify: str = "claude-haiku-4-5"
    sog_libreoffice: str = "/Applications/LibreOffice.app/Contents/MacOS/soffice"

    @property
    def storage_dir(self) -> Path:
        d = self.sog_storage_dir
        return d if d.is_absolute() else REPO_ROOT / d

    @property
    def dataset_dir(self) -> Path:
        return REPO_ROOT / "data" / "archive" / self.sog_dataset_version


settings = Settings()
