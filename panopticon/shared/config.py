"""Configuration globale PANOPTICON."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    panopticon_host: str = "0.0.0.0"
    panopticon_port: int = 8000
    panopticon_profile: str = "standard"  # light | standard | full
    database_url: str = "sqlite+aiosqlite:///./data/panopticon.db"
    storage_path: Path = Path("./data/storage")
    redis_url: str = ""
    jwt_secret: str = "change-me-in-production"
    yolo_model: str = "yolov8n.pt"
    log_level: str = "INFO"

    # SPECTRA — amélioration d'image appliquée par ARGUS avant détection (opt-in, off par défaut)
    spectra_enhance_frames: bool = False
    spectra_gamma: float | None = None
    spectra_denoise: bool = False

    @property
    def use_redis(self) -> bool:
        return bool(self.redis_url.strip())


settings = Settings()
