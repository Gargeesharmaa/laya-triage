"""
Benchmark the triage pipeline.

Generates realistic synthetic alerts, runs them through the full
TriageService pipeline, and reports:

  • How many alerts were handled by Laya (fast path)
  • How many escalated to Groq (slow path)
  • Latency percentiles per path (p50, p95, p99)
  • Cost estimate per path
  • Cost savings vs. an "LLM-only" baseline

Run:
    python -m scripts.benchmark --count 500

Output:
    A summary table to stdout.
    A JSON file (benchmark_results.json) for the LinkedIn chart.
"""

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

from app.models.alert import Alert, AlertSeverity, AlertSource
from app.models.decision import TriageDecision
from app.services.triage import TriageService


# ─────────────────────────────────────────────────────────────────
# Synthetic alert generation
# ─────────────────────────────────────────────────────────────────
SERVICES = [
    "payments-api",
    "checkout-api",
    "auth-service",
    "user-profile",
    "search-api",
    "notifications-worker",
    "frontend-web",
    "mobile-bff",
    "data-pipeline",
    "recommendation-engine",
]

MESSAGES_CRITICAL = [
    "HTTP 500 rate spiked to {rate:.0%} after deploy {sha}",
    "Database connection pool exhausted ({rate:.0%} failures)",
    "Service unreachable — health check failing ({rate:.0%})",
    "p99 latency > 5s for {rate:.0%} of requests",
]

MESSAGES_WARNING = [
    "Elevated error rate at {rate:.0%}",
    "Memory usage at {rate:.0%} of limit",
    "Slow query detected on {sha}",
    "Retry queue backing up ({rate:.0%} depth)",
]

MESSAGES_INFO = [
    "Cron job completed with warnings",
    "Cache hit rate dipped to {rate:.0%}",
    "Config reload successful for {sha}",
]


def _random_sha() -> str:
    """Return a random 7-char hex string like a git short-SHA."""
    return "".join(random.choices("0123456789abcdef", k=7))


def _generate_alert() -> Alert:
    """Generate one realistic-looking alert."""
    # Weight the severity distribution: mostly warnings, some critical, some info
    roll = random.random()
    if roll < 0.25:
        severity = AlertSeverity.CRITICAL
        template = random.choice(MESSAGES_CRITICAL)
        error_rate = random.uniform(0.25, 0.95)
        recent_deploys = random.choice([0, 1, 1, 2])
    elif roll < 0.75:
        severity = AlertSeverity.WARNING
        template = random.choice(MESSAGES_WARNING)
        error_rate = random.uniform(0.05, 0.35)
        recent_deploys = random.choice([0, 0, 1])
    else:
        severity = AlertSeverity.INFO
        template = random.choice(MESSAGES_INFO)
        error_rate = random.uniform(0.0, 0.10)
        recent_deploys = 0

    message = template.format(rate=error_rate, sha=_random_sha())

    return Alert(
        source=random.choice(list(AlertSource)),
        service=random.choice(SERVICES),
        message=message,
        severity=severity,
        error_rate=error_rate,
        recent_deploys=recent_deploys,
    )


# ─────────────────────────────────────────────────────────────────
# Cost model
# ─────────────────────────────────────────────────────────────────
# Approximate costs per 1,000 requests. Adjust if pricing changes.
LAYA_COST_PER_1K = 0.0            # local model, no API cost
GROQ_COST_PER_1K = 0.0            # free tier, no charge
LLM_ONLY_BASELINE_PER_1K = 5.40   # e.g. GPT-4o-mini @ ~1500 tokens/call


# ─────────────────────────────────────────────────────────────────
# Stats collection
# ─────────────────────────────────────────────────────────────────
async def run_benchmark(count: int) -> dict[str, Any]:
    """
    Run `count` alerts through the pipeline and collect stats.
    """
    print(f"Loading TriageService...")
    service = TriageService()

    latencies: list[float] = []
    laya_count = 0
    groq_count = 0
    fallback_count = 0
    priorities: dict[str, int] = {}
    teams: dict[str, int] = {}

    print(f"Running {count} alerts...")
    started = time.perf_counter()

    for i in range(count):
        alert = _generate_alert()
        decision: TriageDecision = await service.triage(alert)

        latencies.append(decision.latency_ms)

        # Bucket by decided_by
        if decision.decided_by.value == "laya":
            laya_count += 1
        elif decision.decided_by.value == "groq":
            groq_count += 1
        else:
            fallback_count += 1

        # Count by priority and team for the summary
        priorities[decision.priority.value] = (
            priorities.get(decision.priority.value, 0) + 1
        )
        teams[decision.team.value] = teams.get(decision.team.value, 0) + 1

        # Progress indicator
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{count} alerts processed")

    total_seconds = time.perf_counter() - started

    # ── Compute summary stats ──
    summary = {
        "count": count,
        "total_seconds": total_seconds,
        "throughput_per_sec": count / total_seconds,
        "laya_count": laya_count,
        "groq_count": groq_count,
        "fallback_count": fallback_count,
        "laya_pct": (laya_count / count) * 100,
        "groq_pct": (groq_count / count) * 100,
        "fallback_pct": (fallback_count / count) * 100,
        "latency_p50": statistics.median(latencies),
        "latency_p95": _percentile(latencies, 95),
        "latency_p99": _percentile(latencies, 99),
        "latency_mean": statistics.mean(latencies),
        "latency_min": min(latencies),
        "latency_max": max(latencies),
        "priorities": priorities,
        "teams": teams,
        "cost_laya_only_usd": round((count / 1000) * LAYA_COST_PER_1K, 4),
        "cost_actual_usd": round(
            (laya_count / 1000) * LAYA_COST_PER_1K
            + (groq_count / 1000) * GROQ_COST_PER_1K,
            4,
        ),
        "cost_llm_only_usd": round((count / 1000) * LLM_ONLY_BASELINE_PER_1K, 4),
    }
    summary["cost_saved_usd"] = round(
        summary["cost_llm_only_usd"] - summary["cost_actual_usd"], 4
    )
    summary["cost_saved_pct"] = round(
        100.0 * summary["cost_saved_usd"] / summary["cost_llm_only_usd"], 2
    ) if summary["cost_llm_only_usd"] > 0 else 0.0

    return summary


def _percentile(values: list[float], p: int) -> float:
    """Simple percentile using linear interpolation."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    k = (len(sorted_values) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    if lo == hi:
        return sorted_values[lo]
    frac = k - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


# ─────────────────────────────────────────────────────────────────
# Output formatting
# ─────────────────────────────────────────────────────────────────
def print_summary(summary: dict[str, Any]) -> None:
    """Pretty-print the benchmark summary."""
    c = summary
    print()
    print("═" * 68)
    print("  BENCHMARK RESULTS")
    print("═" * 68)
    print(f"  Alerts processed      : {c['count']}")
    print(f"  Total time            : {c['total_seconds']:.1f}s")
    print(f"  Throughput            : {c['throughput_per_sec']:.1f} alerts/sec")
    print()
    print("  ── Decision path ──")
    print(f"  Laya (fast, local)    : {c['laya_count']:>4} ({c['laya_pct']:.1f}%)")
    print(f"  Groq (slow, cloud)    : {c['groq_count']:>4} ({c['groq_pct']:.1f}%)")
    print(f"  Fallback (safe page)  : {c['fallback_count']:>4} ({c['fallback_pct']:.1f}%)")
    print()
    print("  ── Latency (ms, end-to-end) ──")
    print(f"  p50                   : {c['latency_p50']:>7.1f}")
    print(f"  p95                   : {c['latency_p95']:>7.1f}")
    print(f"  p99                   : {c['latency_p99']:>7.1f}")
    print(f"  mean                  : {c['latency_mean']:>7.1f}")
    print(f"  min / max             : {c['latency_min']:.1f} / {c['latency_max']:.1f}")
    print()
    print("  ── Distribution ──")
    print(f"  priorities            : {c['priorities']}")
    print(f"  teams                 : {c['teams']}")
    print()
    print("  ── Cost (USD) ──")
    print(f"  This pipeline         : ${c['cost_actual_usd']:.4f}")
    print(f"  LLM-only baseline     : ${c['cost_llm_only_usd']:.4f}")
    print(f"  Saved                 : ${c['cost_saved_usd']:.4f} ({c['cost_saved_pct']:.1f}%)")
    print("═" * 68)
    print()


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the triage pipeline.")
    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Number of alerts to generate (default: 100)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="benchmark_results.json",
        help="Where to write the JSON results",
    )
    args = parser.parse_args()

    summary = asyncio.run(run_benchmark(args.count))
    print_summary(summary)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"Results written to {out_path.resolve()}")


if __name__ == "__main__":
    main()