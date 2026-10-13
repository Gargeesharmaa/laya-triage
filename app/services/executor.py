"""
Execution layer.

Takes a TriageDecision and performs the corresponding action:
  - page_now=True   → page the on-call engineer (PagerDuty, Slack)
  - priority P0/P1  → notify the team channel
  - priority P2/P3  → create a ticket / queue for review

This project uses structured logging as the "action" so the
pipeline runs end-to-end without needing Slack/PagerDuty keys.

In production, replace each branch's log call with the real API
call. The rest of the pipeline stays unchanged.
"""

import logging

from app.models.alert import Alert
from app.models.decision import Priority, TriageDecision

logger = logging.getLogger(__name__)


class Executor:
    """
    Routes a decision to the appropriate action.

    This class is intentionally dumb. It doesn't call Laya, it
    doesn't call Groq, it doesn't validate. It just takes a
    TriageDecision and does what the decision says.
    """

    async def execute(self, alert: Alert, decision: TriageDecision) -> None:
        """
        Perform the action implied by the decision.

        Always returns None. Never raises. A failed action should
        not crash the triage pipeline.
        """
        try:
            if decision.page_now:
                self._page(alert, decision)
            elif decision.priority in (Priority.P0, Priority.P1):
                self._notify(alert, decision)
            else:
                self._queue(alert, decision)
        except Exception:
            # Never let an action failure kill the pipeline.
            # The decision has already been made and logged.
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
        # In production:
        #   pagerduty.trigger(service=decision.team, summary=alert.message)
        #   slack.post(channel=f"#oncall-{decision.team}", ...)

    def _notify(self, alert: Alert, decision: TriageDecision) -> None:
        """Medium-urgency path: notify the team channel, no page."""
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
        # In production:
        #   slack.post(channel=f"#{decision.team}-alerts", ...)

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
        # In production:
        #   jira.create(project="OPS", summary=alert.message, ...)