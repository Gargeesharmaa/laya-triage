"""
FastAPI application factory.

Responsibilities:
  1. Configure logging once
  2. Load TriageService at startup (NOT per request)
  3. Store the service on app.state
  4. Mount the API routes
  5. Run with: uvicorn app.main:app --reload

The lifespan pattern:
  ┌─────────────────────────────────────────────────────┐
  │  uvicorn starts                                     │
  │      │                                              │
  │      ▼                                              │
  │  ┌──────────────────────────────────────────┐      │
  │  │  LIFESPAN STARTUP                        │      │
  │  │  • Load Laya into memory (5–15s)         │      │
  │  │  • Create Groq client                    │      │
  │  │  • Store TriageService on app.state      │      │
  │  └──────────────────────────────────────────┘      │
  │      │                                              │
  │      ▼                                              │
  │  Serve requests (fast — service already loaded)    │
  │      │                                              │
  │      ▼                                              │
  │  ┌──────────────────────────────────────────┐      │
  │  │  LIFESPAN SHUTDOWN                       │      │
  │  │  • Close Groq HTTP client                │      │
  │  └──────────────────────────────────────────┘      │
  └─────────────────────────────────────────────────────┘
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.services.triage import TriageService


# ─────────────────────────────────────────────────────────────────
# Logging configuration — run once at import time
# ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Lifespan — startup and shutdown hooks
# ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: load the model once and attach it to app.state.
    Shutdown: clean up.
    """
    logger.info("Starting up: loading TriageService (this may take a while on first run)...")

    # ── Startup ──
    try:
        service = TriageService()
        app.state.triage_service = service
        logger.info("TriageService ready.")
    except Exception:
        logger.exception("Failed to initialize TriageService — aborting startup.")
        raise

    yield

    # ── Shutdown ──
    logger.info("Shutting down: closing TriageService...")
    # If GroqFallback had a close() method, call it here:
    # await service._groq.close()
    logger.info("Shutdown complete.")


# ─────────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    """
    Build and configure the FastAPI app.

    Using a factory function makes it easy to create isolated
    apps for testing (each test gets a fresh app).
    """
    app = FastAPI(
        title="Laya Triage API",
        description=(
            "Real-time alert triage using a local Laya decision layer "
            "with Groq LLM fallback for low-confidence cases."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(router)

    return app


# ─────────────────────────────────────────────────────────────────
# Module-level app instance (what uvicorn imports)
# ─────────────────────────────────────────────────────────────────
app = create_app()