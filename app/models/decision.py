"""
Typed decision model.

This is the OUTPUT of the decision layer — what Laya or Groq
returns after reasoning over an alert.

Every field is constrained. There is no free-text field where an
LLM could invent a value like "URGENT!!!" or "P4-ish". The types
force the decision layer to pick from known enums or bounded ints.
"""

from enum import Enum

from pydantic import BaseModel, Field


class Priority(str, Enum):
    """
    Operational priority.

    P0 = page immediately (service down, customers impacted)
    P1 = page during business hours (severe degradation)
    P2 = create a ticket (elevated errors, service up)
    P3 = ignore / batch (informational)
    """
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class Team(str, Enum):
    """
    Which team owns the alert.

    Keep this list small. Every new team is one more routing rule
    you'll have to maintain, and one more value Laya has to learn.
    """
    BACKEND = "backend"
    FRONTEND = "frontend"
    INFRA = "infra"
    SECURITY = "security"
    UNKNOWN = "unknown"


class DecidedBy(str, Enum):
    """Which layer produced the decision. Essential for benchmarking."""
    LAYA = "laya"
    GROQ = "groq"
    FALLBACK = "fallback"


class TriageDecision(BaseModel):
    """
    The final decision for an alert.

    Returned by /triage endpoint, logged by the executor, and
    used to feed the benchmark chart for the LinkedIn post.
    """

    priority: Priority = Field(
        ...,
        description="Operational priority (P0–P3)",
    )
    team: Team = Field(
        ...,
        description="Owning team",
    )
    page_now: bool = Field(
        ...,
        description="Whether an on-call engineer should be paged immediately",
    )
    risk_score: int = Field(
        ...,
        ge=0,
        le=5,
        description="Risk severity on a 0–5 scale",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Calibrated confidence in the priority decision",
    )
    reasoning: str = Field(
        default="",
        max_length=500,
        description="Short human-readable explanation",
    )
    decided_by: DecidedBy = Field(
        default=DecidedBy.LAYA,
        description="Which layer produced this decision",
    )
    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="End-to-end decision latency in milliseconds",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "priority": "P0",
                "team": "backend",
                "page_now": True,
                "risk_score": 5,
                "confidence": 0.94,
                "reasoning": "Critical + recent deploy — likely rollback candidate.",
                "decided_by": "laya",
                "latency_ms": 41.7,
            }
        }
    }