"""
Test the Groq fallback directly.

Run: python -m scripts.test_groq
Requires: GROQ_API_KEY in .env
"""

import asyncio

from app.models.alert import Alert, AlertSeverity, AlertSource
from app.services.groq_client import GroqFallback


async def main() -> None:
    client = GroqFallback()

    alert = Alert(
        source=AlertSource.GRAFANA,
        service="payments-api",
        message="HTTP 500 rate spiked to 42% after deploy 3f8a1c",
        severity=AlertSeverity.CRITICAL,
        error_rate=0.42,
        recent_deploys=1,
    )

    decision = await client.decide(alert)
    print(decision.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())