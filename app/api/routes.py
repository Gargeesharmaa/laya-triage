"""
HTTP routes.

Exposes the triage pipeline over HTTP:

  POST /triage              → direct alert submission
  POST /webhook/grafana     → Grafana alert webhook receiver
  POST /webhook/sentry      → Sentry alert webhook receiver
  GET  /health              → liveness probe
  GET  /ready               → readiness probe (checks service loaded)

Routes are intentionally thin. They:
  1. Parse and validate the request (Pydantic does the work)
  2. Delegate to TriageService
  3. Return the decision

No routing logic, no decision logic, no Laya/Groq calls live here.
"""

import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.config import settings
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
# Health checks
# ─────────────────────────────────────────────────────────────────
@router.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is alive."""
    return {"status": "ok"}


@router.get("/ready", tags=["ops"])
async def ready(request: Request) -> dict[str, str]:
    """
    Readiness probe. Returns 200 only if TriageService is loaded
    and ready to accept traffic.
    """
    service = getattr(request.app.state, "triage_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Triage service not initialized.",
        )
    return {"status": "ready"}


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
    description=(
        "Grafana posts here when an alert fires or resolves. "
        "We normalize the payload, run the triage pipeline, and return the decision."
    ),
)
async def webhook_grafana(
    payload: dict[str, Any],
    request: Request,
    service: TriageService = Depends(get_triage_service),
) -> TriageDecision:
    """
    Normalize a Grafana alert payload into an Alert and triage it.

    Handles Grafana's real webhook format:
      - Alerts array with per-alert status, labels, annotations
      - commonLabels / commonAnnotations (shared across alerts)
      - valueString (e.g. "value: 95.5")
      - fingerprint (for deduplication)
      - generatorURL (deep-link to the Grafana alert)
      - Resolved alerts (status="resolved")

    Also handles Grafana's "Test contact point" button, which sends
    a payload with empty alerts[] — we detect this and return a
    synthetic test decision.
    """
    logger.info("Grafana webhook received (status=%s, alerts=%d)",
                payload.get("status"), len(payload.get("alerts") or []))

    # ── HMAC signature verification (only if secret is configured) ──
    if settings.grafana_webhook_secret:
        raw_body = await request.body()
        signature = request.headers.get("X-Grafana-Alerting-Signature", "")

        expected = hmac.new(
            settings.grafana_webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            logger.warning(
                "Grafana webhook signature mismatch — rejecting. "
                "(got=%s... expected=%s...)",
                signature[:12] or "<empty>",
                expected[:12],
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )

    try:
        alert = _alert_from_grafana(payload)
    except (KeyError, ValueError) as exc:
        logger.warning("Invalid Grafana payload: %s", exc)
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
    logger.info("Sentry webhook received")

    try:
        alert = _alert_from_sentry(payload)
    except (KeyError, ValueError) as exc:
        logger.warning("Invalid Sentry payload: %s", exc)
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
    Convert a real Grafana alert webhook into our Alert schema.

    Grafana's webhook payload looks like:

    {
      "receiver": "Laya Triage",
      "status": "firing",              # firing | resolved
      "alerts": [
        {
          "status": "firing",
          "labels": {
            "alertname": "HighErrorRate",
            "severity": "critical",
            "service": "payments-api"
          },
          "annotations": {
            "summary": "HTTP 500 rate spiked to 42%",
            "description": "Error rate exceeded threshold after deploy"
          },
          "startsAt": "2026-10-16T10:15:30Z",
          "endsAt": "0001-01-01T00:00:00Z",
          "generatorURL": "https://grafana.example.com/alerting/...",
          "fingerprint": "a1b2c3d4e5f6",
          "valueString": "[ metric='error_rate' value=0.42 ]"
        }
      ],
      "groupLabels": {...},
      "commonLabels": {
        "alertname": "HighErrorRate",
        "severity": "critical",
        "service": "payments-api"
      },
      "commonAnnotations": {
        "summary": "HTTP 500 rate spiked to 42%"
      },
      "externalURL": "https://grafana.example.com/..."
    }

    Our strategy:
      1. Prefer the first alert's own labels/annotations
      2. Fall back to commonLabels / commonAnnotations
      3. Fall back to reasonable defaults
    """
    alerts = payload.get("alerts") or []
    common_labels = payload.get("commonLabels") or {}
    common_annotations = payload.get("commonAnnotations") or {}

    # ── Handle Grafana's "Test contact point" button ──
    # Test payloads have empty alerts[] and status="firing".
    # We synthesize a test alert so the user sees a green check.
    if not alerts:
        logger.info("Grafana test contact point detected (no alerts)")
        return Alert(
            source=AlertSource.GRAFANA,
            service="grafana-test",
            message="Test notification from Grafana contact point — Laya Triage is reachable.",
            severity=AlertSeverity.INFO,
            error_rate=0.0,
            recent_deploys=0,
            labels={
                "test": "true",
                "receiver": str(payload.get("receiver", "")),
            },
        )

    first = alerts[0]

    # ── Labels: per-alert first, then common ──
    labels = {**common_labels, **(first.get("labels") or {})}

    # ── Annotations: per-alert first, then common ──
    annotations = {**common_annotations, **(first.get("annotations") or {})}

    # ── Status → severity ──
    # Grafana uses "firing" or "resolved" at the top level,
    # and per-alert status too. Prefer the per-alert status.
    alert_status = (first.get("status") or payload.get("status") or "firing").lower()
    severity = {
        "firing": AlertSeverity.CRITICAL,
        "resolved": AlertSeverity.INFO,
        "pending": AlertSeverity.WARNING,
        "no_data": AlertSeverity.WARNING,
    }.get(alert_status, AlertSeverity.WARNING)

    # ── If the alert's own severity label is set, honor it ──
    # Grafana users often set labels.severity = "critical" | "warning" | "info"
    label_severity = (labels.get("severity") or "").lower()
    if label_severity in ("critical", "warning", "info"):
        severity = AlertSeverity(label_severity)

    # ── Service name: try multiple label keys in priority order ──
    service = (
        labels.get("service")
        or labels.get("job")
        or labels.get("app")
        or labels.get("service_name")
        or labels.get("alertname")   # last resort
        or "unknown"
    )

    # ── Message: prefer summary, then description, then valueString ──
    message = (
        annotations.get("summary")
        or annotations.get("description")
        or first.get("valueString")
        or f"Grafana alert: {labels.get('alertname', 'unknown')}"
    )

    # ── Try to extract error rate from valueString if present ──
    error_rate = _parse_error_rate(labels, annotations, first.get("valueString"))

    # ── Build enriched labels for downstream use ──
    enriched_labels = {
        **labels,
        **annotations,
        "grafana_status": alert_status,
        "grafana_fingerprint": str(first.get("fingerprint", "")),
        "grafana_url": str(first.get("generatorURL", "")),
        "grafana_receiver": str(payload.get("receiver", "")),
        "grafana_external_url": str(payload.get("externalURL", "")),
    }

    return Alert(
        source=AlertSource.GRAFANA,
        service=str(service),
        message=str(message)[:2000],
        severity=severity,
        error_rate=error_rate,
        recent_deploys=0,
        labels=enriched_labels,
    )


def _parse_error_rate(
    labels: dict[str, Any],
    annotations: dict[str, Any],
    value_string: Any,
) -> float:
    """
    Best-effort extraction of an error rate (0.0–1.0) from Grafana fields.

    Tries:
      1. labels.error_rate — explicit label set by the user
      2. valueString — parses "value=0.42" or "value: 42"
      3. Returns 0.0 if nothing usable is found
    """
    # 1. Explicit label
    try:
        v = float(labels.get("error_rate", 0.0))
        if 0.0 <= v <= 1.0:
            return v
        if 0.0 <= v <= 100.0:
            return v / 100.0
    except (TypeError, ValueError):
        pass

    # 2. valueString — Grafana sends things like:
    #    "[ metric='error_rate' value=0.42 ]"
    #    "value: 42%"
    if isinstance(value_string, str) and value_string:
        import re
        m = re.search(r"value[=:\s]+([0-9.]+)(%?)", value_string)
        if m:
            try:
                v = float(m.group(1))
                has_percent = m.group(2) == "%"
                if has_percent or v > 1.0:
                    v = v / 100.0
                if 0.0 <= v <= 1.0:
                    return v
            except ValueError:
                pass

    return 0.0


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