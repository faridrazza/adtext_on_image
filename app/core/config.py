"""Application settings, loaded once from the environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Ad Text & CTA Image Generator"
    log_level: str = "INFO"
    cors_allow_origins: str = "*"

    openai_api_key: str = ""
    # Exact image model id. Kept in config rather than hardcoded so the model
    # can be swapped without touching the service layer. gpt-image-2 is the
    # only family that accepts arbitrary output sizes -- see .env.example.
    openai_image_model: str = "gpt-image-2"
    openai_image_quality: str = "high"
    openai_timeout_seconds: float = 180.0
    openai_max_retries: int = 2

    # Ceiling applied before any per-platform limit, so we reject oversized
    # uploads without decoding them.
    max_upload_bytes: int = 30 * 1024 * 1024

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
