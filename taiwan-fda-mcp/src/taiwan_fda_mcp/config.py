# path: src/taiwan_fda_mcp/config.py
# brief: Application settings loaded from .env via pydantic-settings.

from pathlib import Path

from platformdirs import user_cache_dir
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings — populated from .env / environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    FDA_INSERT_BASE_URL: str = "https://mcp.fda.gov.tw"
    FDA_OPENDATA_BASE_URL: str = "https://data.fda.gov.tw"
    FDA_RATE_LIMIT_INTERVAL_SECONDS: float = 0.5

    # Per-user OS cache dir (uvx-safe — never the package tree, which is
    # ephemeral/read-only under `uvx`). Override with DATASET37_CACHE_DIR.
    DATASET37_CACHE_DIR: Path = Field(
        default_factory=lambda: Path(user_cache_dir("taiwan-fda-mcp")) / "dataset37"
    )
    DATASET37_TTL_HOURS: int = 24

    LOG_LEVEL: str = "INFO"


def get_settings() -> Settings:
    """Return a freshly-loaded Settings instance.

    Not cached — tests may mutate env between calls. Cache at call site if needed.
    """
    return Settings()  # type: ignore[call-arg]
