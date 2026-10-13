"""
Groq LLM fallback.

This is the SLOW PATH. It only fires when Laya's confidence on
the priority question drops below settings.confidence_threshold.

Why Groq?
  - Free tier: 1,000 req/day on llama-3.3-70b-versatile
  - Ultra-fast LPU inference (~500 tok/s)
  - OpenAI-compatible SDK
  - No credit card required

This file uses JSON Object Mode. Groq guarantees valid JSON, but
NOT schema adherence — we validate the parsed output through the
TriageDecision Pydantic model, which rejects anything malformed.
"""

import json
import time

from groq import AsyncGroq

from app.config import settings
from app.models.alert import Alert
from app.models.decision import (
    DecidedBy,
    Priority,
    Team,
    TriageDecision,
)


class GroqFallback:
    """
    Deep-reasoning fallback using Groq's free Llama models.

    The client is created once in __init__ and reused across
    requests. The Groq SDK attaches the API key automatically.
    """

    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Add it to your .env file. "
                "Get a free key at https://console.groq.com"
            )
        self._client = AsyncGroq(api_key=settings.groq_api_key)
        self._model = settings.groq_model

    async def decide(self, alert: Alert) -> TriageDecision:
        """
        Escalate the alert to Groq's Llama 3.3 70B.

        Returns a TriageDecision tagged with decided_by=GROQ.
        Raises on network failure so the orchestrator can fall back.
        """
        started = time.perf_counter()

        completion = await self._client.chat.completions.create(
            model=self._model,
            messages=self._build_messages(alert),
            temperature=0.1,
            max_tokens=500,
            response_format={"type": "json_object"},
        )

        raw_text = completion.choices[0].message.content
        if not raw_text:
            raise ValueError("Groq returned empty content")

        raw = json.loads(raw_text)

        decision = TriageDecision(
            priority=Priority(raw["priority"]),
            team=Team(raw["team"]),
            page_now=bool(raw["page_now"]),
            risk_score=int(raw["risk_score"]),
            confidence=0.95,
            reasoning=str(raw.get("reasoning", ""))[:200],
            decided_by=DecidedBy.GROQ,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return decision

    def _build_messages(self, alert: Alert) -> list[dict[str, str]]:
        """
        Build the system + user messages for the chat completion.

        The system prompt is the schema contract. JSON mode requires
        the word "JSON" to appear in the prompt — otherwise Groq
        returns HTTP 400.
        """
        system_prompt = (
            "You are an expert SRE triage assistant. "
            "Given an alert, return ONLY a JSON object with exactly these keys: "
            '"priority" (one of: P0, P1, P2, P3), '
            '"team" (one of: backend, frontend, infra, security, unknown), '
            '"page_now" (boolean), '
            '"risk_score" (integer 0-5), '
            '"reasoning" (a single short sentence). '
            "Do not include any text outside the JSON object. "
            "Do not wrap the JSON in markdown fences."
        )

        user_prompt = (
            f"source={alert.source.value}\n"
            f"service={alert.service}\n"
            f"severity={alert.severity.value}\n"
            f"error_rate={alert.error_rate}\n"
            f"recent_deploys={alert.recent_deploys}\n"
            f"message={alert.message[:500]}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]