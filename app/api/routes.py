"""
HTTP routes.

Exposes the triage pipeline over HTTP:

  POST /triage              → direct alert submission
  POST /webhook/grafana     → Grafana webhook receiver
  POST /webhook/sentry      → Sentry webhook receiver
  GET  /health              → liveness + readiness probe

Routes are intentionally thin. They:
  1. Parse and validate the request (Pydantic does the work)
  2. Delegate to TriageService
  3. Return the decision

No routing logic, no decision logic, no Laya/Groq calls live here.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.models.alert import Alert, AlertSeverity, AlertSource
from app.models.decision import TriageDecision
from app.services.triage import TriageService

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────
# Dependency — retrieve the singleton TriageService from app.state
# ─────────────────────────────────────────────────────────────────
def get_triage_service(request: Request) -> TriageService:
    """
    FastAPI dependency that returns the app-wide TriageService.

    The service is created once during the lifespan startup event
    and stored on app.state. This dependency retrieves it per
    request — it does NOT construct a new one.
    """
    service = getattr(request.app.state, "triage_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Triage service not initialized.",
        )
    return service


# ─────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────
@router.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is alive."""
    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────
# Primary triage endpoint
# ─────────────────────────────────────────────────────────────────
@router.post(
    "/triage",
    response_model=TriageDecision,
    status_code=status.HTTP_200_OK,
    tags=["triage"],
    summary="Triage a single alert",
    description=(
        "Submit a normalized alert. Returns a typed TriageDecision "
        "with priority, team, page_now, risk_score, and latency."
    ),
)
async def triage_alert(
    alert: Alert,
    service: TriageService = Depends(get_triage_service),
) -> TriageDecision:
    """
    Run the full Laya → guardrail → Groq → executor pipeline.
    """
    decision = await service.triage(alert)
    return decision


# ─────────────────────────────────────────────────────────────────
# Grafana webhook receiver
# ─────────────────────────────────────────────────────────────────
@router.post(
    "/webhook/grafana",
    response_model=TriageDecision,
    status_code=status.HTTP_200_OK,
    tags=["webhooks"],
    summary="Receive a Grafana alert webhook",
)
async def webhook_grafana(
    payload: dict[str, Any],
    service: TriageService = Depends(get_triage_service),
) -> TriageDecision:
    """
    Normalize a Grafana alert payload into an Alert and triage it.

    Grafana sends a webhook with a slightly different shape. We
    extract the fields we care about and discard the rest.
    """
    try:
        alert = _alert_from_grafana(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid Grafana payload: {exc}",
        ) from exc

    return await service.triage(alert)


# ─────────────────────────────────────────────────────────────────
# Sentry webhook receiver
# ─────────────────────────────────────────────────────────────────
@router.post(
    "/webhook/sentry",
    response_model=TriageDecision,
    status_code=status.HTTP_200_OK,
    tags=["webhooks"],
    summary="Receive a Sentry alert webhook",
)
async def webhook_sentry(
    payload: dict[str, Any],
    service: TriageService = Depends(get_triage_service),
) -> TriageDecision:
    """
    Normalize a Sentry alert payload into an Alert and triage it.
    """
    try:
        alert = _alert_from_sentry(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid Sentry payload: {exc}",
        ) from exc

    return await service.triage(alert)


# ─────────────────────────────────────────────────────────────────
# Normalizers — source-specific payload → Alert
# ─────────────────────────────────────────────────────────────────
def _alert_from_grafana(payload: dict[str, Any]) -> Alert:
    """
    Convert a Grafana alert webhook into our Alert schema.

    Grafana's payload includes: status, alerts[], commonLabels,
    commonAnnotations, externalURL, ...
    """
    alerts = payload.get("alerts") or []
    first = alerts[0] if alerts else {}
    labels = first.get("labels", {}) or {}
    annotations = first.get("annotations", {}) or {}

    # Map Grafana's status → our severity enum
    status_str = (payload.get("status") or "firing").lower()
    severity = {
        "firing": AlertSeverity.CRITICAL,
        "resolved": AlertSeverity.INFO,
        "pending": AlertSeverity.WARNING,
    }.get(status_str, AlertSeverity.WARNING)

    # Try to pull a service name from common labels
    service = (
        labels.get("service")
        or labels.get("job")
        or labels.get("app")
        or "unknown"
    )

    message = (
        annotations.get("summary")
        or annotations.get("description")
        or first.get("status", "Grafana alert")
    )

    return Alert(
        source=AlertSource.GRAFANA,
        service=str(service),
        message=str(message)[:2000],
        severity=severity,
        error_rate=0.0,        # Grafana doesn't ship this; default
        recent_deploys=0,
        labels={**labels, **annotations},
    )


def _alert_from_sentry(payload: dict[str, Any]) -> Alert:
    """
    Convert a Sentry issue webhook into our Alert schema.

    Sentry's payload includes: data.issue.*, data.event.*, ...
    """
    data = payload.get("data", {}) or {}
    issue = data.get("issue", {}) or {}
    event = data.get("event", {}) or {}

    service = issue.get("project", {}).get("slug") or "unknown"
    message = issue.get("title") or event.get("title") or "Sentry alert"

    # Sentry levels: fatal, error, warning, info, debug
    level = (issue.get("level") or "warning").lower()
    severity = {
        "fatal": AlertSeverity.CRITICAL,
        "error": AlertSeverity.CRITICAL,
        "warning": AlertSeverity.WARNING,
        "info": AlertSeverity.INFO,
        "debug": AlertSeverity.INFO,
    }.get(level, AlertSeverity.WARNING)

    return Alert(
        source=AlertSource.SENTRY,
        service=str(service),
        message=str(message)[:2000],
        severity=severity,
        error_rate=0.0,
        recent_deploys=0,
        labels={
            "sentry_issue_id": str(issue.get("id", "")),
            "sentry_url": str(issue.get("permalink", "")),
            "culprit": str(issue.get("culprit", "")),
        },
    )