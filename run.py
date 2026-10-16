"""
Single entry point.

Runs the FastAPI app (with all routes and webhooks) AND the
Gradio UI on one server, one port.

    FastAPI routes:
      GET  /health              → liveness
      POST /triage              → triage an alert
      POST /webhook/grafana     → receive Grafana alerts
      POST /webhook/sentry      → receive Sentry alerts

    Gradio UI:
      GET  /ui                  → the dashboard

Run:
    python run.py
    # → http://127.0.0.1:8000/ui
"""

import logging
import os

import gradio as gr
import uvicorn
from fastapi import FastAPI

from app.api.routes import router
from app.main import lifespan  # reuse the lifespan (loads Laya once)
from app.ui import CUSTOM_CSS, build_ui

logger = logging.getLogger(__name__)


def create_unified_app() -> FastAPI:
    """
    Create the FastAPI app, mount the routes, and attach the
    Gradio UI at /ui.
    """
    app = FastAPI(
        title="Laya Triage (Unified)",
        description="API + Webhooks + Dashboard in one app.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── API routes ──
    app.include_router(router)

    # ── Gradio UI mounted at /ui ──
    demo = build_ui()
    app = gr.mount_gradio_app(app, demo, path="/ui", css=CUSTOM_CSS)

    return app


app = create_unified_app()


if __name__ == "__main__":
    uvicorn.run(
        "run:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )