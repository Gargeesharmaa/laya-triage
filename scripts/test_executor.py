"""
Test the executor with three decisions covering all paths.

Run: python -m scripts.test_executor
"""

import asyncio
import logging

from app.models.alert import Alert, AlertSeverity, AlertSource
from app.models.decision import (
    DecidedBy,
    Priority,
    Team,
    TriageDecision,
)
from app.services.executor import Executor


# Configure logging so we actually see the output.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


async def main() -> None:
    executor = Executor()

    alert = Alert(
        source=AlertSource.GRAFANA,
        service="payments-api",
        message="HTTP 500 rate spiked to 42%",
        severity=AlertSeverity.CRITICAL,
        error_rate=0.42,
        recent_deploys=1,
    )

    # ── Path 1: page ──
    print("\n── Path 1: PAGE ──")
    await executor.execute(
        alert,
        TriageDecision(
            priority=Priority.P0,
            team=Team.BACKEND,
            page_now=True,
            risk_score=5,
            confidence=0.94,
            reasoning="Critical + recent deploy — likely rollback candidate.",
            decided_by=DecidedBy.LAYA,
            latency_ms=41.7,
        ),
    )

    # ── Path 2: notify ──
    print("\n── Path 2: NOTIFY ──")
    await executor.execute(
        alert,
        TriageDecision(
            priority=Priority.P1,
            team=Team.INFRA,
            page_now=False,
            risk_score=4,
            confidence=0.88,
            reasoning="Infra degradation, no customer impact yet.",
            decided_by=DecidedBy.LAYA,
            latency_ms=38.2,
        ),
    )

    # ── Path 3: queue ──
    print("\n── Path 3: QUEUE ──")
    await executor.execute(
        alert,
        TriageDecision(
            priority=Priority.P3,
            team=Team.UNKNOWN,
            page_now=False,
            risk_score=1,
            confidence=0.81,
            reasoning="Low-signal informational alert.",
            decided_by=DecidedBy.GROQ,
            latency_ms=1743.1,
        ),
    )


if __name__ == "__main__":
    asyncio.run(main())