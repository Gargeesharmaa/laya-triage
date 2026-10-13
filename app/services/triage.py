"""
Triage orchestrator.

Wires Laya, Groq, and the Executor into one pipeline:

    Alert
      │
      ▼
    ┌─────────────────────────────────────────────────┐
    │ 1. Laya (fast path, local, free)                │
    │    • single forward pass, ~40ms                 │
    │    • calibrated confidence                      │
    └────────────────────┬────────────────────────────┘
                         │
                         ▼
    ┌─────────────────────────────────────────────────┐
    │ 2. Guardrail: confidence >= threshold?          │
    │    yes → use Laya's decision                    │
    │    no  → escalate to Groq (slow path)           │
    └────────────────────┬────────────────────────────┘
                         │
                         ▼
    ┌─────────────────────────────────────────────────┐
    │ 3. Executor                                     │
    │    • page / notify / queue                      │
    └─────────────────────────────────────────────────┘

Failure handling:
  • Laya raises      → skip straight to Groq
  • Groq raises      → fall back to a safe default decision
  • Executor raises  → swallowed inside the executor
  • Nothing crashes the pipeline. Every alert gets a decision.
"""

import logging
import time

from app.config import settings
from app.models.alert import Alert
from app.models.decision import (
    DecidedBy,
    Priority,
    Team,
    TriageDecision,
)
from app.services.executor import Executor
from app.services.groq_client import GroqFallback
from app.services.laya_client import LayaClient

logger = logging.getLogger(__name__)


class TriageService:
    """
    The orchestrator.

    Constructed once at app startup. Loads Laya into memory and
    creates a Groq HTTP client — both are reused across requests.
    """

    def __init__(self) -> None:
        self._laya = LayaClient()
        self._groq = GroqFallback()
        self._executor = Executor()

    async def triage(self, alert: Alert) -> TriageDecision:
        """
        Run the full pipeline for a single alert.

        Always returns a TriageDecision. Never raises.
        """
        started = time.perf_counter()
        decision = await self._decide(alert)
        await self._executor.execute(alert, decision)

        # Record true end-to-end latency, including executor.
        decision.latency_ms = (time.perf_counter() - started) * 1000
        return decision

    async def _decide(self, alert: Alert) -> TriageDecision:
        """
        Run the decision layers with escalation.

        Returns the first decision whose confidence clears the
        threshold. Falls back to a safe default if both layers fail.
        """
        # ── 1. Fast path: Laya ────────────────────────────────
        try:
            decision = self._laya.decide(alert)
            logger.debug(
                "Laya decided %s/%s confidence=%.2f latency=%.0fms",
                decision.priority.value,
                decision.team.value,
                decision.confidence,
                decision.latency_ms,
            )
        except Exception as exc:
            logger.warning(
                "Laya failed for %s/%s: %s — escalating to Groq",
                alert.source.value,
                alert.service,
                exc,
            )
            return await self._try_groq_or_default(alert)

        # ── 2. Guardrail: is Laya confident enough? ───────────
        if decision.confidence >= settings.confidence_threshold:
            return decision

        logger.info(
            "Laya confidence %.2f < %.2f — escalating to Groq",
            decision.confidence,
            settings.confidence_threshold,
        )
        return await self._try_groq_or_default(alert, laya_attempt=decision)

    async def _try_groq_or_default(
        self,
        alert: Alert,
        laya_attempt: TriageDecision | None = None,
    ) -> TriageDecision:
        """
        Try Groq; on failure, return a safe default decision.

        `laya_attempt` is passed through so the default fallback can
        preserve Laya's best-effort answer if it exists.
        """
        try:
            return await self._groq.decide(alert)
        except Exception as exc:
            logger.error(
                "Groq failed for %s/%s: %s — using safe default",
                alert.source.value,
                alert.service,
                exc,
            )
            return self._safe_default(alert, laya_attempt)

    def _safe_default(
        self,
        alert: Alert,
        laya_attempt: TriageDecision | None,
    ) -> TriageDecision:
        """
        The decision of last resort.

        If Laya produced *something*, keep its priority/team but
        flag it as low-confidence and force a page — better to
        over-page than to silently drop a critical alert.
        """
        if laya_attempt is not None:
            return TriageDecision(
                priority=laya_attempt.priority,
                team=laya_attempt.team,
                page_now=True,
                risk_score=laya_attempt.risk_score,
                confidence=0.0,
                reasoning=(
                    "Safe default: kept Laya's answer, forced page "
                    "because confidence was below threshold and Groq failed."
                ),
                decided_by=DecidedBy.FALLBACK,
                latency_ms=0.0,
            )

        return TriageDecision(
            priority=Priority.P1,
            team=Team.UNKNOWN,
            page_now=True,
            risk_score=3,
            confidence=0.0,
            reasoning="Safe default: both Laya and Groq failed. Paging.",
            decided_by=DecidedBy.FALLBACK,
            latency_ms=0.0,
        )