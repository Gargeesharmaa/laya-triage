"""
Execution layer.

Takes a TriageDecision and performs the corresponding action:
  - page_now=True   → page the on-call engineer (Slack)
  - priority P0/P1  → notify the team channel (Slack)
  - priority P2/P3  → create a ticket / queue for review (log only)

Slack is optional. If SLACK_WEBHOOK_URL is not set, the executor
falls back to logging only. The pipeline never breaks.
"""

import logging

from app.models.alert import Alert
from app.models.decision import Priority, TriageDecision
from app.services.slack_client import SlackNotifier

logger = logging.getLogger(__name__)


class Executor:
    """
    Routes a decision to the appropriate action.

    Sends real Slack notifications when configured, and always
    logs a structured line for observability and benchmarking.
    """

    def __init__(self, slack: SlackNotifier | None = None) -> None:
        self._slack = slack or SlackNotifier()

    async def execute(self, alert: Alert, decision: TriageDecision) -> None:
        """
        Perform the action implied by the decision.

        Always returns None. Never raises.
        """
        try:
            if decision.page_now:
                self._page(alert, decision)
            elif decision.priority in (Priority.P0, Priority.P1):
                self._notify(alert, decision)
            else:
                self._queue(alert, decision)
        except Exception:
            logger.exception(
                "Executor failed for %s/%s — decision=%s by=%s",
                alert.source.value,
                alert.service,
                decision.priority.value,
                decision.decided_by.value,
            )

    def _page(self, alert: Alert, decision: TriageDecision) -> None:
        """High-urgency path: notify on-call immediately."""
        logger.warning(
            "🚨 PAGE | %s | %s | priority=%s team=%s risk=%d "
            "confidence=%.2f by=%s latency=%.0fms | %s",
            alert.source.value,
            alert.service,
            decision.priority.value,
            decision.team.value,
            decision.risk_score,
            decision.confidence,
            decision.decided_by.value,
            decision.latency_ms,
            decision.reasoning,
        )
        # Fire Slack notification (async, best-effort)
        self._slack.notify_sync(alert, decision, action="PAGE")

    def _notify(self, alert: Alert, decision: TriageDecision) -> None:
        """Medium-urgency path: notify the team channel."""
        logger.info(
            "📣 NOTIFY | %s | %s | priority=%s team=%s risk=%d "
            "confidence=%.2f by=%s latency=%.0fms | %s",
            alert.source.value,
            alert.service,
            decision.priority.value,
            decision.team.value,
            decision.risk_score,
            decision.confidence,
            decision.decided_by.value,
            decision.latency_ms,
            decision.reasoning,
        )
        self._slack.notify_sync(alert, decision, action="NOTIFY")

    def _queue(self, alert: Alert, decision: TriageDecision) -> None:
        """Low-urgency path: batch for later review."""
        logger.info(
            "📥 QUEUE  | %s | %s | priority=%s team=%s risk=%d "
            "confidence=%.2f by=%s latency=%.0fms",
            alert.source.value,
            alert.service,
            decision.priority.value,
            decision.team.value,
            decision.risk_score,
            decision.confidence,
            decision.decided_by.value,
            decision.latency_ms,
        )
        self._slack.notify_sync(alert, decision, action="QUEUE")