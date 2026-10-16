"""
Slack notification client.

Sends rich Block Kit messages to a Slack channel via an Incoming
Webhook URL. Used by the executor to notify on-call engineers.

Setup:
  1. Create a Slack app at https://api.slack.com/apps
  2. Enable "Incoming Webhooks"
  3. Click "Add New Webhook to Workspace"
  4. Pick a channel (e.g. #oncall-alerts)
  5. Copy the webhook URL into SLACK_WEBHOOK_URL in .env
"""

import logging

from slack_sdk.webhook import WebhookClient
from slack_sdk.errors import SlackApiError

from app.config import settings
from app.models.alert import Alert
from app.models.decision import TriageDecision
import asyncio

logger = logging.getLogger(__name__)


# Priority → Slack attachment color (hex)
PRIORITY_COLOR = {
    "P0": "#ef4444",   # red
    "P1": "#f97316",   # orange
    "P2": "#eab308",   # yellow
    "P3": "#64748b",   # gray
}

# Priority → emoji prefix
PRIORITY_EMOJI = {
    "P0": ":rotating_light:",
    "P1": ":warning:",
    "P2": ":clipboard:",
    "P3": ":zzz:",
}


class SlackNotifier:
    """
    Sends Slack notifications for triage decisions.

    Constructed once at app startup. If SLACK_WEBHOOK_URL is not
    set, notifications are silently skipped (no crash).
    """

    def __init__(self) -> None:
        url = settings.slack_webhook_url
        if url:
            self._client: WebhookClient | None = WebhookClient(url)
            logger.info("Slack notifier enabled.")
        else:
            self._client = None
            logger.warning("SLACK_WEBHOOK_URL not set — Slack notifications disabled.")

    @property
    def enabled(self) -> bool:
        """True if a webhook URL is configured."""
        return self._client is not None

    async def notify(
        self,
        alert: Alert,
        decision: TriageDecision,
        action: str,  # "PAGE" | "NOTIFY" | "QUEUE"
    ) -> None:
        """
        Send a Slack message for this triage decision.

        Never raises. If Slack fails, we log and continue —
        a failed notification should never break the pipeline.
        """
        if not self.enabled:
            return

        try:
            blocks = self._build_blocks(alert, decision, action)
            color = PRIORITY_COLOR.get(decision.priority.value, "#64748b")

            # `text` is the fallback shown in notifications & search.
            # `blocks` is the rich Block Kit layout.
            # `attachments` gives us the colored left border.
            response = self._client.send(
                text=f"{action}: {decision.priority.value} — {alert.service}",
                blocks=blocks,
                attachments=[
                    {
                        "color": color,
                        "blocks": [],
                    }
                ],
            )

            if response.status_code != 200:
                logger.warning(
                    "Slack returned %s: %s",
                    response.status_code,
                    response.body,
                )
        except SlackApiError as exc:
            logger.error("Slack API error: %s", exc)
        except Exception:
            logger.exception("Slack notification failed for %s", alert.service)


    def notify_sync(
        self,
        alert: Alert,
        decision: TriageDecision,
        action: str,
    ) -> None:
        """
        Synchronous wrapper for `notify()`.

        Called from the Executor's sync methods. Schedules the
        async `notify` on the running event loop if one exists,
        otherwise runs it synchronously.
        """
        if not self.enabled:
            return
        try:
            # We're inside an event loop (FastAPI) — schedule a task.
            loop = asyncio.get_running_loop()
            loop.create_task(self.notify(alert, decision, action))
        except RuntimeError:
            # No event loop — run synchronously.
            asyncio.run(self.notify(alert, decision, action))


    def _build_blocks(
        self,
        alert: Alert,
        decision: TriageDecision,
        action: str,
    ) -> list[dict]:
        """Build the Block Kit payload for the message."""
        emoji = PRIORITY_EMOJI.get(decision.priority.value, ":grey_question:")

        # Header
        blocks: list[dict] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {action} · {decision.priority.value} · {alert.service}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Priority:*\n{decision.priority.value}"},
                    {"type": "mrkdwn", "text": f"*Team:*\n{decision.team.value}"},
                    {"type": "mrkdwn", "text": f"*Risk:*\n{decision.risk_score}/5"},
                    {"type": "mrkdwn", "text": f"*Confidence:*\n{decision.confidence:.0%}"},
                    {"type": "mrkdwn", "text": f"*Decided by:*\n{decision.decided_by.value}"},
                    {"type": "mrkdwn", "text": f"*Latency:*\n{decision.latency_ms:.0f}ms"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Message*\n> {alert.message[:300]}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Source: `{alert.source.value}` · "
                            f"Severity: `{alert.severity.value}` · "
                            f"Error rate: `{alert.error_rate:.0%}` · "
                            f"Deploys: `{alert.recent_deploys}`"
                        ),
                    }
                ],
            },
        ]

        # Add reasoning if present
        if decision.reasoning:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Reasoning*\n```{decision.reasoning}```",
                    },
                }
            )

        # Add a "page now" banner if applicable
        if decision.page_now:
            blocks.insert(
                1,
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":rotating_light: *PAGE NOW* — an on-call engineer should respond immediately.",
                    },
                },
            )

        return blocks