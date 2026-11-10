"""Executive summary for the digest.

If ANTHROPIC_API_KEY is set, an LLM writes the summary from the facts payload only.
A guardrail then checks that every number in the text exists in the facts; if any number
is unsupported, the AI text is rejected and the deterministic template is used instead.
Without a key, the template is used directly, so the pipeline never depends on the LLM.
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger(__name__)

PROMPT = """You are a healthcare BI analyst writing the monthly CMS benchmark summary for {focus} leadership.
Use ONLY the facts in the JSON below. Do not add numbers that are not in it. Do not speculate about causes.

Write:
1. One headline sentence on where {focus} stands vs the Phoenix market.
2. Three bullets: biggest strengths (name the hospital and measure).
3. Three bullets: biggest gaps to address (name the hospital and measure).
4. One bullet on alerts or data quality, if any.
Plain text, under 200 words. Readmission rates: lower is better.

FACTS:
{facts}"""


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".").lstrip("0") or "0" for n in re.findall(r"\d+(?:\.\d+)?", text)}


def _fact_numbers(facts: dict) -> set[str]:
    nums = _numbers(json.dumps(facts))
    expanded = set(nums)
    for n in nums:  # allow 72.0 -> 72 and 1 decimal rounding
        try:
            f = float(n)
            expanded |= {f"{f:g}", f"{f:.1f}", f"{round(f)}", f"{abs(f):g}", f"{abs(f):.1f}"}
        except ValueError:
            pass
    return {e.lstrip("0") or "0" for e in expanded}


def ungrounded_numbers(text: str, facts: dict) -> set[str]:
    allowed = _fact_numbers(facts) | {str(i) for i in range(1, 11)}  # list numbering, "top 3"
    return _numbers(text) - allowed


def template_summary(facts: dict) -> str:
    focus = facts["focus_system"]
    ahead = [m for m in facts["measures"] if m["gap_vs_market"] > 0]
    ranks = sorted({m["focus_rank_among_systems"] for m in facts["measures"]})
    n_sys = facts["measures"][0]["systems_compared"]
    leaders = sorted({m["leader_system"] for m in facts["measures"]})
    lines = [f"{focus} is ahead of the Phoenix-market average on {len(ahead)} of "
             f"{len(facts['measures'])} benchmarked CMS measures "
             f"({facts['focus_hospitals']} hospitals vs {facts['market_hospitals']} in market), "
             f"ranking #{ranks[0]} to #{ranks[-1]} of {n_sys} health systems. "
             f"{' and '.join(leaders)} {'leads' if len(leaders) == 1 else 'lead'} every measure, so the "
             f"system-level margin is narrow; the opportunity is concentrated in specific campuses.", ""]
    lines.append("Strengths:")
    for s in facts["strengths"][:3]:
        lines.append(f"- {s['hospital']}: {s['measure']} {s['value']:g} (rank {s['rank']} of {s['of']})")
    lines.append("")
    lines.append("Gaps to address:")
    for c in facts["concerns"][:3]:
        lines.append(f"- {c['hospital']}: {c['measure']} {c['value']:g} vs market {c['market_avg']:g} "
                     f"(rank {c['rank']} of {c['of']})")
    lines.append("")
    if facts["alerts"]:
        lines.append(f"Alerts: {len(facts['alerts'])} hospital-measure moves exceeded thresholds since last release.")
    dq = facts["data_quality"]
    lines.append(f"Data quality: {dq['PASS']} checks passed, {dq['WARN']} warnings, {dq['FAIL']} failures.")
    return "\n".join(lines)


def write_summary(facts: dict, use_llm: bool = True) -> tuple[str, str]:
    """Return (summary_text, method) where method is 'llm' or 'template'."""
    if not (use_llm and os.getenv("ANTHROPIC_API_KEY")):
        return template_summary(facts), "template"
    try:
        import anthropic

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=os.getenv("HQB_LLM_MODEL", "claude-sonnet-5-5"),
            max_tokens=600,
            messages=[{"role": "user", "content": PROMPT.format(
                focus=facts["focus_system"], facts=json.dumps(facts, indent=1))}],
        )
        text = msg.content[0].text.strip()
    except Exception as exc:  # network, auth, model errors -> never block the report
        log.warning("LLM summary failed (%s); using template", exc)
        return template_summary(facts), "template"

    bad = ungrounded_numbers(text, facts)
    if bad:
        log.warning("LLM summary contained numbers not in facts %s; using template", sorted(bad))
        return template_summary(facts), "template"
    return text, "llm"
