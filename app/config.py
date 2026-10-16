from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Groq (free LLM fallback) ──────────────────────────────
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"

    # ── Laya (local decision layer — no API key needed) ──────
    laya_checkpoint: str = "convaiinnovations/laya"
    laya_device: str = "cpu"

    # ── Confidence guardrail ─────────────────────────────────
    confidence_threshold: float = 0.75

     # ── Slack (NEW) ──
    slack_webhook_url: str = ""

    # ── Grafana (NEW) ──
    grafana_webhook_secret: str = ""  # optional HMAC secret for verification

    # ── Load from .env file ──────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


# Singleton — created once, imported everywhere.
settings = Settings()