"""
Laya decision layer.

Wraps the local Laya model. Takes an Alert, builds typed questions,
runs ONE forward pass, and returns a TriageDecision.

Laya is a non-autoregressive encoder model:
  - It does NOT generate text.
  - It scores each option at its own [MASK] token and softmaxes.
  - Probabilities are trained with strictly proper scoring rules,
    so confidence numbers are statistically meaningful.
"""

import time
from typing import Any

import laya

from app.config import settings
from app.models.alert import Alert
from app.models.decision import (
    DecidedBy,
    Priority,
    Team,
    TriageDecision,
)


class LayaClient:
    """
    Local, free decision layer.

    Loads Laya once at construction. Reuses the same agent for every
    request — model loading is expensive, inference is cheap.
    """

    def __init__(self) -> None:
        # Load the model. On first run this downloads weights (~2.4 GB)
        # and caches them. Subsequent loads read from disk.
        self._agent = laya.load(settings.laya_checkpoint)

        # Build the typed question schema ONCE and reuse it.
        # Rebuilding it per request is wasted work.
        self._questions = self._build_questions()

    def _build_questions(self) -> dict[str, Any]:
        """
        Define Laya's typed questions.

        Laya supports three primitives:
          - choice: pick one label from a set → probability per option
          - score:  place on an ordinal rubric → expected value
          - noul:   calibrated P(true) → float in [0.0, 1.0]
        """
        return {
            "priority": {
                "type": "choice",
                "instructions": "What is the operational priority of this alert?",
                "criteria": {
                    "P0": "service down, customers impacted, page immediately",
                    "P1": "severe degradation, page during business hours",
                    "P2": "elevated errors but service is up, create a ticket",
                    "P3": "informational, batch for later review",
                },
            },
            "team": {
                "type": "choice",
                "instructions": "Which team owns this alert?",
                "criteria": {
                    "backend": "API, database, business logic",
                    "frontend": "UI, client-side, browser errors",
                    "infra": "networking, k8s, cloud, deployment",
                    "security": "auth, credentials, suspicious activity",
                    "unknown": "cannot determine",
                },
            },
            "page_now": {
                "type": "noul",
                "instructions": "Should an on-call engineer be paged immediately?",
            },
            "risk_score": {
                "type": "score",
                "instructions": "How risky is this incident?",
                "criteria": [
                    "negligible",
                    "minor",
                    "moderate",
                    "serious",
                    "severe",
                    "catastrophic",
                ],
            },
        }

    def _build_state(self, alert: Alert) -> dict[str, Any]:
        """
        Convert the Alert into Laya's state format.

        Laya accepts strings OR dicts. A dict is cleaner: Laya's
        encoder can attend to structured fields directly.
        """
        return {
            "source": alert.source.value,
            "service": alert.service,
            "severity": alert.severity.value,
            "error_rate": alert.error_rate,
            "recent_deploys": alert.recent_deploys,
            "message": alert.message[:500],
            "labels": alert.labels,
        }

    def decide(self, alert: Alert) -> TriageDecision:
        """
        Run Laya over the alert. Returns a typed decision.

        This is synchronous (no async) because Laya runs in-process.
        """
        started = time.perf_counter()

        state = self._build_state(alert)
        result = self._agent.predict(state, self._questions)
        answers = result["answers"]

        decision = TriageDecision(
            priority=Priority(answers["priority"]["choice"]),
            team=Team(answers["team"]["choice"]),
            page_now=answers["page_now"]["noul"] > 0.5,
            risk_score=round(answers["risk_score"]["score"]),
            confidence=float(answers["priority"]["confidence"]),
            reasoning=self._build_reasoning(answers),
            decided_by=DecidedBy.LAYA,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return decision

    def _build_reasoning(self, answers: dict[str, Any]) -> str:
        """Build a short human-readable explanation from Laya's answers."""
        p = answers["priority"]
        t = answers["team"]
        page = answers["page_now"]["noul"]
        risk = answers["risk_score"]["score"]
        return (
            f"Laya: priority={p['choice']} ({p['confidence']:.2f}), "
            f"team={t['choice']} ({t['confidence']:.2f}), "
            f"page_now P={page:.2f}, "
            f"risk={risk:.1f}/5.0"
        )