"""
Normalized alert model.

Every alert source (Grafana, Sentry, Prometheus, custom webhooks)
gets transformed into THIS shape before it reaches the decision layer.

Why normalize? Because Laya should never care whether the alert came
from Grafana or Sentry — it only cares about the semantic content:
service, severity, error rate, deploys, and message.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AlertSeverity(str, Enum):
    """Raw severity as reported by the source system."""
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertSource(str, Enum):
    """Which system produced this alert."""
    GRAFANA = "grafana"
    SENTRY = "sentry"
    PROMETHEUS = "prometheus"
    PAGERDUTY = "pagerduty"
    MANUAL = "manual"


class Alert(BaseModel):
    """
    A normalized alert ready for triage.

    Every field is typed and validated. If a webhook sends garbage,
    Pydantic rejects it with a 422 BEFORE it hits the decision layer.
    """

    source: AlertSource = Field(
        ...,
        description="Which system produced this alert",
    )
    service: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Service name, e.g. 'payments-api'",
    )
    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Human-readable alert message",
    )
    severity: AlertSeverity = Field(
        default=AlertSeverity.WARNING,
        description="Raw severity from the source system",
    )
    error_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of requests failing (0.0–1.0)",
    )
    recent_deploys: int = Field(
        default=0,
        ge=0,
        description="Number of deploys in the last hour",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the alert fired (UTC)",
    )
    labels: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form metadata (region, pod, runbook URL, etc.)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "source": "grafana",
                "service": "payments-api",
                "message": "HTTP 500 rate spiked to 42% after deploy 3f8a1c",
                "severity": "critical",
                "error_rate": 0.42,
                "recent_deploys": 1,
                "labels": {"region": "us-east-1", "pod": "payments-7f9c"},
            }
        }
    }