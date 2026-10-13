from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Groq (free LLM fallback) ──────────────────────────────
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # ── Laya (local decision layer — no API key needed) ──────
    laya_checkpoint: str = "convaiinnovations/laya"
    laya_device: str = "cpu"

    # ── Confidence guardrail ─────────────────────────────────
    confidence_threshold: float = 0.75

    # ── Load from .env file ──────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


# Singleton — created once, imported everywhere.
settings = Settings()