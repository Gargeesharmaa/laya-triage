"""
Gradio UI for the Laya Triage system.

Run:
    python -m app.ui
    # → http://127.0.0.1:7860

Features:
  • Submit an alert via a form (pre-filled with a demo alert)
  • See the decision as a rich card: priority badge, confidence bar,
    team chip, page-now indicator, and reasoning
  • Run the benchmark with one click and see live results
  • Beautiful dark theme with color-coded priorities

This file talks to TriageService directly (no HTTP hop), so it
runs standalone. Perfect for demos and screenshots.
"""

import asyncio
import time
from typing import Any

import gradio as gr

from app.models.alert import Alert, AlertSeverity, AlertSource
from app.models.decision import TriageDecision
from app.services.triage import TriageService


# ─────────────────────────────────────────────────────────────────
# Load TriageService once at import time
# ─────────────────────────────────────────────────────────────────
print("Loading TriageService (first run downloads Laya weights)...")
SERVICE = TriageService()
print("TriageService ready. Launching UI...")


# ─────────────────────────────────────────────────────────────────
# Custom CSS — dark theme, cards, badges, animations
# ─────────────────────────────────────────────────────────────────
CUSTOM_CSS = """
:root {
    --bg-primary: #0f172a;
    --bg-card: #1e293b;
    --bg-card-hover: #293548;
    --border: #334155;
    --text-primary: #e2e8f0;
    --text-muted: #94a3b8;
    --accent: #38bdf8;
    --accent-strong: #0ea5e9;
    --p0: #ef4444;
    --p1: #f97316;
    --p2: #eab308;
    --p3: #64748b;
    --success: #22c55e;
    --danger: #dc2626;
}

.gradio-container {
    background: var(--bg-primary) !important;
    max-width: 1400px !important;
    margin: 0 auto !important;
}

body, .gradio-container {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
    color: var(--text-primary) !important;
}

/* Header */
.app-header {
    text-align: center;
    padding: 24px 0 8px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
}
.app-header h1 {
    font-size: 2.2rem;
    font-weight: 800;
    letter-spacing: -0.02em;
    background: linear-gradient(135deg, #38bdf8 0%, #a78bfa 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin: 0;
}
.app-header p {
    color: var(--text-muted);
    font-size: 1rem;
    margin: 8px 0 0;
}

/* Decision card */
.decision-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 24px;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.3);
    animation: fadeIn 0.3s ease;
}
@keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}

.card-row {
    display: flex;
    gap: 16px;
    align-items: center;
    margin-bottom: 16px;
    flex-wrap: wrap;
}

.badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border-radius: 999px;
    font-weight: 700;
    font-size: 0.85rem;
    letter-spacing: 0.02em;
}
.badge-p0 { background: var(--p0); color: white; }
.badge-p1 { background: var(--p1); color: white; }
.badge-p2 { background: var(--p2); color: #1a1a1a; }
.badge-p3 { background: var(--p3); color: white; }

.chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border-radius: 8px;
    background: rgba(56, 189, 248, 0.12);
    color: var(--accent);
    font-weight: 600;
    font-size: 0.85rem;
    border: 1px solid rgba(56, 189, 248, 0.25);
}
.chip-page {
    background: rgba(220, 38, 38, 0.15);
    color: #f87171;
    border-color: rgba(220, 38, 38, 0.3);
}
.chip-no-page {
    background: rgba(34, 197, 94, 0.12);
    color: var(--success);
    border-color: rgba(34, 197, 94, 0.25);
}

.metric-label {
    color: var(--text-muted);
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 6px;
}
.metric-value {
    color: var(--text-primary);
    font-size: 1.5rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}

.confidence-bar {
    height: 8px;
    background: rgba(148, 163, 184, 0.15);
    border-radius: 999px;
    overflow: hidden;
    margin-top: 8px;
}
.confidence-fill {
    height: 100%;
    background: linear-gradient(90deg, #38bdf8 0%, #a78bfa 100%);
    border-radius: 999px;
    transition: width 0.6s ease;
}

.reasoning-box {
    background: rgba(15, 23, 42, 0.6);
    border-left: 3px solid var(--accent);
    padding: 12px 16px;
    border-radius: 8px;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.85rem;
    color: var(--text-primary);
    line-height: 1.5;
    margin-top: 16px;
}

.decided-by {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.decided-laya { background: rgba(34, 197, 94, 0.15); color: var(--success); }
.decided-groq { background: rgba(168, 85, 247, 0.15); color: #c084fc; }
.decided-fallback { background: rgba(220, 38, 38, 0.15); color: #f87171; }

/* Placeholder */
.placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 380px;
    color: var(--text-muted);
    text-align: center;
    padding: 40px;
    border: 2px dashed var(--border);
    border-radius: 16px;
}
.placeholder-icon {
    font-size: 3rem;
    margin-bottom: 12px;
    opacity: 0.4;
}

/* Buttons */
button.primary {
    background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%) !important;
    border: none !important;
    color: white !important;
    font-weight: 700 !important;
    letter-spacing: 0.02em !important;
    transition: transform 0.15s ease, box-shadow 0.15s ease !important;
}
button.primary:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 8px 24px rgba(14, 165, 233, 0.35) !important;
}

/* Footer */
.app-footer {
    text-align: center;
    padding: 24px 0;
    color: var(--text-muted);
    font-size: 0.8rem;
    border-top: 1px solid var(--border);
    margin-top: 32px;
}
"""


# ─────────────────────────────────────────────────────────────────
# HTML rendering helpers
# ─────────────────────────────────────────────────────────────────
PRIORITY_CLASS = {
    "P0": "badge-p0",
    "P1": "badge-p1",
    "P2": "badge-p2",
    "P3": "badge-p3",
}

PRIORITY_ICON = {
    "P0": "🔥",
    "P1": "⚠️",
    "P2": "📋",
    "P3": "💤",
}

DECIDED_CLASS = {
    "laya": "decided-laya",
    "groq": "decided-groq",
    "fallback": "decided-fallback",
}

DECIDED_LABEL = {
    "laya": "⚡ Laya (local)",
    "groq": "☁️ Groq (cloud)",
    "fallback": "🛡️ Safe default",
}


def _render_placeholder() -> str:
    """HTML shown before the first triage."""
    return """
    <div class="placeholder">
        <div class="placeholder-icon">🚨</div>
        <div style="font-size:1.1rem;font-weight:600;color:#cbd5e1;">
            Ready to triage
        </div>
        <div style="margin-top:8px;">
            Fill in the alert on the left and click <b>Triage alert</b>
        </div>
    </div>
    """


def _render_decision(decision: TriageDecision, alert: Alert) -> str:
    """Render a TriageDecision as a rich HTML card."""
    p = decision.priority.value
    badge_cls = PRIORITY_CLASS[p]
    badge_icon = PRIORITY_ICON[p]

    page_chip = (
        '<span class="chip chip-page">🚨 PAGE NOW</span>'
        if decision.page_now
        else '<span class="chip chip-no-page">✓ No page</span>'
    )

    decided_cls = DECIDED_CLASS.get(decision.decided_by.value, "")
    decided_label = DECIDED_LABEL.get(decision.decided_by.value, decision.decided_by.value)

    confidence_pct = decision.confidence * 100
    risk_pct = (decision.risk_score / 5) * 100

    return f"""
    <div class="decision-card">
        <div class="card-row">
            <span class="badge {badge_cls}">{badge_icon} {p}</span>
            <span class="chip">🏷️ {decision.team.value}</span>
            {page_chip}
            <span class="decided-by {decided_cls}">{decided_label}</span>
        </div>

        <div style="display:flex;gap:32px;margin-top:20px;flex-wrap:wrap;">
            <div style="flex:1;min-width:200px;">
                <div class="metric-label">Confidence</div>
                <div class="metric-value">{confidence_pct:.1f}%</div>
                <div class="confidence-bar">
                    <div class="confidence-fill" style="width:{confidence_pct:.1f}%"></div>
                </div>
            </div>
            <div style="flex:1;min-width:200px;">
                <div class="metric-label">Risk score</div>
                <div class="metric-value">{decision.risk_score}/5</div>
                <div class="confidence-bar">
                    <div class="confidence-fill" style="width:{risk_pct:.0f}%"></div>
                </div>
            </div>
            <div style="flex:1;min-width:200px;">
                <div class="metric-label">Latency</div>
                <div class="metric-value">{decision.latency_ms:.0f}<span style="font-size:0.9rem;color:#94a3b8;"> ms</span></div>
            </div>
        </div>

        <div class="reasoning-box">
            <span style="color:#94a3b8;">// reasoning</span><br/>
            {decision.reasoning}
        </div>

        <div style="margin-top:16px;font-size:0.8rem;color:#94a3b8;">
            Source: <b>{alert.source.value}</b> ·
            Service: <b>{alert.service}</b> ·
            Severity: <b>{alert.severity.value}</b> ·
            Error rate: <b>{alert.error_rate:.0%}</b> ·
            Recent deploys: <b>{alert.recent_deploys}</b>
        </div>
    </div>
    """


# ─────────────────────────────────────────────────────────────────
# Event handlers
# ─────────────────────────────────────────────────────────────────
async def triage_handler(
    source: str,
    service: str,
    message: str,
    severity: str,
    error_rate: float,
    recent_deploys: int,
) -> tuple[str, str]:
    """
    Handle the 'Triage alert' button click.

    Returns (decision_html, stats_markdown).
    """
    # ── Validate the input via the Alert schema ──
    try:
        alert = Alert(
            source=source,
            service=service,
            message=message,
            severity=severity,
            error_rate=error_rate,
            recent_deploys=int(recent_deploys),
        )
    except Exception as exc:
        error_html = f"""
        <div class="decision-card" style="border-color:#dc2626;">
            <div class="badge badge-p0">❌ Validation error</div>
            <div style="margin-top:16px;color:#f87171;font-family:monospace;font-size:0.85rem;">
                {exc}
            </div>
        </div>
        """
        return error_html, ""

    # ── Run the pipeline ──
    started = time.perf_counter()
    decision = await SERVICE.triage(alert)
    wall_ms = (time.perf_counter() - started) * 1000

    card_html = _render_decision(decision, alert)

    stats_md = (
        f"**Path:** `{decision.decided_by.value}` · "
        f"**Latency:** `{wall_ms:.0f} ms` · "
        f"**Confidence:** `{decision.confidence:.2f}`"
    )
    return card_html, stats_md


def preset_handler(preset: str) -> tuple[str, str, str, str, float, int]:
    """Fill the form with a preset alert scenario."""
    presets = {
        "🔥 Critical outage": (
            "grafana",
            "payments-api",
            "HTTP 500 rate spiked to 42% after deploy 3f8a1c",
            "critical",
            0.42,
            1,
        ),
        "⚠️ Degraded service": (
            "sentry",
            "checkout-api",
            "p99 latency degraded to 3.2s, some users affected",
            "warning",
            0.18,
            0,
        ),
        "🔐 Suspicious activity": (
            "sentry",
            "auth-service",
            "Multiple failed login attempts from single IP",
            "warning",
            0.05,
            0,
        ),
        "💤 Informational": (
            "prometheus",
            "notifications-worker",
            "Cron job completed with warnings",
            "info",
            0.0,
            0,
        ),
    }
    return presets.get(preset, presets["🔥 Critical outage"])


async def benchmark_handler(count: int) -> tuple[str, str]:
    """
    Run the benchmark and return (results_table_html, summary_md).
    """
    from scripts.benchmark import _generate_alert, _percentile

    import statistics

    count = int(count)
    latencies: list[float] = []
    laya_count = 0
    groq_count = 0
    fallback_count = 0
    priorities: dict[str, int] = {}

    started = time.perf_counter()
    for _ in range(count):
        alert = _generate_alert()
        d = await SERVICE.triage(alert)
        latencies.append(d.latency_ms)
        if d.decided_by.value == "laya":
            laya_count += 1
        elif d.decided_by.value == "groq":
            groq_count += 1
        else:
            fallback_count += 1
        priorities[d.priority.value] = priorities.get(d.priority.value, 0) + 1

    total_s = time.perf_counter() - started
    p50 = statistics.median(latencies)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)

    table = f"""
    <div class="decision-card">
        <div style="font-size:1.1rem;font-weight:700;margin-bottom:16px;">
            Benchmark · {count} alerts in {total_s:.1f}s ({count/total_s:.1f}/sec)
        </div>

        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px;margin-top:16px;">
            <div>
                <div class="metric-label">Laya (fast, local)</div>
                <div class="metric-value" style="color:#22c55e;">{laya_count}</div>
                <div style="color:#94a3b8;font-size:0.8rem;">{100*laya_count/count:.1f}%</div>
            </div>
            <div>
                <div class="metric-label">Groq (cloud)</div>
                <div class="metric-value" style="color:#c084fc;">{groq_count}</div>
                <div style="color:#94a3b8;font-size:0.8rem;">{100*groq_count/count:.1f}%</div>
            </div>
            <div>
                <div class="metric-label">Fallback (safe)</div>
                <div class="metric-value" style="color:#f87171;">{fallback_count}</div>
                <div style="color:#94a3b8;font-size:0.8rem;">{100*fallback_count/count:.1f}%</div>
            </div>
        </div>

        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px;margin-top:24px;padding-top:24px;border-top:1px solid #334155;">
            <div>
                <div class="metric-label">p50 latency</div>
                <div class="metric-value">{p50:.0f}<span style="font-size:0.9rem;color:#94a3b8;"> ms</span></div>
            </div>
            <div>
                <div class="metric-label">p95 latency</div>
                <div class="metric-value">{p95:.0f}<span style="font-size:0.9rem;color:#94a3b8;"> ms</span></div>
            </div>
            <div>
                <div class="metric-label">p99 latency</div>
                <div class="metric-value">{p99:.0f}<span style="font-size:0.9rem;color:#94a3b8;"> ms</span></div>
            </div>
        </div>

        <div class="reasoning-box" style="border-left-color:#22c55e;">
            <span style="color:#94a3b8;">// priority distribution</span><br/>
            {' · '.join(f"{k}: {v}" for k, v in sorted(priorities.items()))}
        </div>

        <div style="margin-top:16px;font-size:0.8rem;color:#94a3b8;">
            Cost: <b style="color:#22c55e;">$0.00</b> ·
            vs. LLM-only baseline: <b style="color:#f87171;">${count/1000*5.40:.2f}</b> ·
            Saved: <b style="color:#22c55e;">${count/1000*5.40:.2f}</b>
        </div>
    </div>
    """

    summary_md = (
        f"**Laya:** `{laya_count}` · "
        f"**Groq:** `{groq_count}` · "
        f"**Fallback:** `{fallback_count}` · "
        f"**p50:** `{p50:.0f}ms` · "
        f"**Cost:** `$0.00`"
    )
    return table, summary_md


# ─────────────────────────────────────────────────────────────────
# Build the Gradio UI
# ─────────────────────────────────────────────────────────────────
def build_ui() -> gr.Blocks:
    """Build and return the Gradio Blocks app."""
    with gr.Blocks(
        title="Laya Triage",
        css=CUSTOM_CSS,
        theme=gr.themes.Base(
            primary_hue="sky",
            neutral_hue="slate",
        ),
    ) as demo:
        # ── Header ──
        gr.HTML(
            """
            <div class="app-header">
                <h1>🚨 Laya Triage</h1>
                <p>Local decision model · Groq LLM fallback · Real-time alert triage</p>
            </div>
            """
        )

        # ── Tabs ──
        with gr.Tabs():
            # ═══════════════════════════════════════════════════════
            # Tab 1: Triage a single alert
            # ═══════════════════════════════════════════════════════
            with gr.Tab("🎯 Triage Alert"):
                with gr.Row():
                    # ── Left column: input form ──
                    with gr.Column(scale=2):
                        gr.Markdown("### Alert details")

                        preset = gr.Dropdown(
                            label="Load a preset",
                            choices=[
                                "🔥 Critical outage",
                                "⚠️ Degraded service",
                                "🔐 Suspicious activity",
                                "💤 Informational",
                            ],
                            value="🔥 Critical outage",
                        )

                        source = gr.Dropdown(
                            label="Source",
                            choices=["grafana", "sentry", "prometheus", "pagerduty", "manual"],
                            value="grafana",
                        )
                        service = gr.Textbox(
                            label="Service",
                            value="payments-api",
                            placeholder="e.g. payments-api",
                        )
                        message = gr.Textbox(
                            label="Message",
                            value="HTTP 500 rate spiked to 42% after deploy 3f8a1c",
                            lines=3,
                        )
                        severity = gr.Radio(
                            label="Severity",
                            choices=["critical", "warning", "info"],
                            value="critical",
                        )
                        with gr.Row():
                            error_rate = gr.Slider(
                                label="Error rate",
                                minimum=0.0,
                                maximum=1.0,
                                value=0.42,
                                step=0.01,
                            )
                            recent_deploys = gr.Number(
                                label="Recent deploys",
                                value=1,
                                precision=0,
                            )

                        triage_btn = gr.Button(
                            "⚡ Triage alert",
                            variant="primary",
                            elem_classes=["primary"],
                        )

                    # ── Right column: decision card ──
                    with gr.Column(scale=3):
                        gr.Markdown("### Decision")
                        decision_html = gr.HTML(value=_render_placeholder())
                        stats_md = gr.Markdown(value="")

                # ── Wire events ──
                preset.change(
                    preset_handler,
                    inputs=[preset],
                    outputs=[source, service, message, severity, error_rate, recent_deploys],
                )
                triage_btn.click(
                    triage_handler,
                    inputs=[source, service, message, severity, error_rate, recent_deploys],
                    outputs=[decision_html, stats_md],
                )

            # ═══════════════════════════════════════════════════════
            # Tab 2: Benchmark
            # ═══════════════════════════════════════════════════════
            with gr.Tab("📊 Benchmark"):
                gr.Markdown(
                    "Run a batch of synthetic alerts through the full pipeline. "
                    "See how many are decided locally (Laya) vs escalated to Groq."
                )
                with gr.Row():
                    bench_count = gr.Slider(
                        label="Number of alerts",
                        minimum=10,
                        maximum=200,
                        value=50,
                        step=10,
                    )
                    bench_btn = gr.Button(
                        "▶ Run benchmark",
                        variant="primary",
                        elem_classes=["primary"],
                    )

                bench_html = gr.HTML(value="")
                bench_summary = gr.Markdown(value="")

                bench_btn.click(
                    benchmark_handler,
                    inputs=[bench_count],
                    outputs=[bench_html, bench_summary],
                )

        # ── Footer ──
        gr.HTML(
            """
            <div class="app-footer">
                Laya Triage · Built with FastAPI + Laya + Groq ·
                <a href="https://github.com/Convai-Innovations/laya" style="color:#38bdf8;">Laya</a> ·
                <a href="https://console.groq.com" style="color:#38bdf8;">Groq</a>
            </div>
            """
        )

    return demo


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ui = build_ui()
    ui.queue()  # enables concurrency for async handlers
    ui.launch(
        server_name="127.0.0.1",
        server_port=7860,
        show_error=True,
        inbrowser=True,
    )