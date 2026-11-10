"""Delivery: self-contained HTML digest, plus optional Slack and email distribution."""
from __future__ import annotations

import html
import json
import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

import requests

log = logging.getLogger(__name__)

FOCUS = "#6b2a6b"
PEER = "#9aa3ad"
ORDER = ["OVERALL_STAR", "H_STAR_RATING", "H_RECMND_DY", "H_HSP_RATING_9_10", "READM_30_HF", "READM_30_PN"]
SHORT = {"OVERALL_STAR": "Overall ★", "H_STAR_RATING": "HCAHPS ★", "H_RECMND_DY": "Recommend %",
         "H_HSP_RATING_9_10": "Rated 9-10 %", "READM_30_HF": "HF Readmit %", "READM_30_PN": "PN Readmit %"}

CSS = """
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
color:#1d1f23;background:#f6f5f7}main{max-width:1080px;margin:0 auto;padding:32px 24px 64px}
header{border-bottom:3px solid FOCUS;padding-bottom:16px;margin-bottom:24px}
h1{font-size:26px;margin:0 0 6px}h2{font-size:18px;margin:36px 0 12px}.sub{color:#5b606a;font-size:14px}
.summary{background:#fff;border-left:4px solid FOCUS;padding:16px 20px;white-space:pre-wrap;line-height:1.55;
font-size:14.5px;border-radius:4px}.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:10px;
background:#eee;color:#444;margin-left:8px;vertical-align:middle}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.tile{background:#fff;border-radius:6px;padding:14px;border:1px solid #e4e2e6}
.tile .l{font-size:12px;color:#5b606a;height:30px}.tile .v{font-size:26px;font-weight:650;color:FOCUS}
.tile .m{font-size:12px;color:#5b606a}.up{color:#1a7f4b;font-weight:600}.down{color:#b4342d;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
.card{background:#fff;border:1px solid #e4e2e6;border-radius:6px;padding:14px 16px}
.card h3{font-size:13.5px;margin:0 0 10px}.bar{display:flex;align-items:center;font-size:12px;margin:5px 0}
.bar .n{width:130px;flex:none;color:#3a3d43}.bar .t{flex:1;background:#f0eef2;height:14px;border-radius:3px}
.bar .f{height:14px;border-radius:3px}.bar .x{width:46px;text-align:right;flex:none;font-variant-numeric:tabular-nums}
.mk{font-size:11.5px;color:#5b606a;margin-top:6px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:12.5px;border:1px solid #e4e2e6}
th,td{padding:7px 9px;border-bottom:1px solid #eee;text-align:left}th{background:#faf9fb;font-weight:600}
td.c{text-align:center;font-variant-numeric:tabular-nums}.q1{background:#d9f0e3}.q2{background:#eef7f1}
.q3{background:#fbf0e6}.q4{background:#f6d9d6}.lv{opacity:.55}
.PASS{color:#1a7f4b}.WARN{color:#a66300}.FAIL{color:#b4342d;font-weight:600}
footer{margin-top:40px;font-size:12px;color:#5b606a;line-height:1.6}
""".replace("FOCUS", FOCUS)


def _fmt(v, unit):
    return f"{v:.1f}" if unit == "stars" else f"{v:.1f}%"


def render_html(facts: dict, summary: str, method: str, dq: dict, rank_rows: list[dict]) -> str:
    e = html.escape
    focus = facts["focus_system"]
    measures = sorted(facts["measures"], key=lambda m: ORDER.index(m["measure_id"]))

    tiles = []
    for m in measures:
        cls = "up" if m["gap_vs_market"] > 0 else "down"
        arrow = "▲ better" if m["gap_vs_market"] > 0 else "▼ worse"
        tiles.append(f'<div class="tile"><div class="l">{e(m["label"])}</div>'
                     f'<div class="v">{_fmt(m["focus_avg"], m["unit"])}</div>'
                     f'<div class="m">Market {_fmt(m["market_avg"], m["unit"])} · '
                     f'<span class="{cls}">{arrow}</span></div>'
                     f'<div class="m">#{m["focus_rank_among_systems"]} of {m["systems_compared"]} systems</div></div>')

    cards = []
    for m in measures:
        vals = m["system_avgs"]
        hi = max(vals.values())
        lo = min(vals.values())
        floor = 0 if m["unit"] == "stars" else max(0, lo - (hi - lo) - 2)
        bars = []
        for sysname, v in vals.items():
            w = 100 * (v - floor) / (hi - floor) if hi > floor else 100
            color = FOCUS if sysname == focus else PEER
            weight = "font-weight:650;" if sysname == focus else ""
            bars.append(f'<div class="bar"><span class="n" style="{weight}">{e(sysname)}</span>'
                        f'<span class="t"><span class="f" style="display:block;width:{w:.0f}%;background:{color}">'
                        f'</span></span><span class="x">{_fmt(v, m["unit"])}</span></div>')
        direction = "higher is better" if m["higher_is_better"] else "lower is better"
        cards.append(f'<div class="card"><h3>{e(m["label"])}</h3>{"".join(bars)}'
                     f'<div class="mk">Market avg {_fmt(m["market_avg"], m["unit"])} · {direction}</div></div>')

    # Hospital heatmap: each HonorHealth campus x measure, shaded by market quartile
    by_h: dict[str, dict] = {}
    for r in rank_rows:
        if r["health_system"] == focus:
            by_h.setdefault(r["facility_name"], {})[r["measure_id"]] = r
    head = "".join(f"<th class='c'>{SHORT[m]}</th>" for m in ORDER)
    body = []
    for name in sorted(by_h):
        cells = []
        for mid in ORDER:
            r = by_h[name].get(mid)
            if not r:
                cells.append("<td class='c' style='color:#aaa'>n/a</td>")
                continue
            p = (r["market_rank"] - 1) / max(r["market_n"] - 1, 1)
            q = "q1" if p <= .25 else "q2" if p <= .5 else "q3" if p <= .75 else "q4"
            lv = " lv" if r["low_volume_flag"] else ""
            val = f'{r["value"]:g}'
            cells.append(f"<td class='c {q}{lv}' title='rank {r['market_rank']} of {r['market_n']}'>"
                         f"{val}<br><small>#{r['market_rank']}/{r['market_n']}</small></td>")
        body.append(f"<tr><td>{e(name)}</td>{''.join(cells)}</tr>")

    dq_rows = "".join(f"<tr><td>{e(r['check'])}</td><td>{e(r['dataset'])}</td>"
                      f"<td class='{r['status']}'>{r['status']}</td><td>{e(r['detail'])}</td></tr>"
                      for r in dq["results"])

    alerts = facts["alerts"]
    alert_html = ("<p class='sub'>No release-over-release moves above thresholds "
                  "(first load, or no material change).</p>" if not alerts else
                  "<table><tr><th>Hospital</th><th>System</th><th>Measure</th><th>Prior</th><th>Current</th>"
                  "<th>Change</th></tr>" + "".join(
                      f"<tr><td>{e(a['hospital'])}</td><td>{e(a['system'])}</td><td>{e(a['measure'])}</td>"
                      f"<td class='c'>{a['prior']:g}</td><td class='c'>{a['current']:g}</td>"
                      f"<td class='c'>{a['change']:+g}</td></tr>" for a in alerts) + "</table>")

    periods = "; ".join(f"{k}: {v}" for k, v in facts["reporting_periods"].items())
    tag = "AI-written, number-checked" if method == "llm" else "auto-generated"
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(focus)} CMS Quality &amp; Patient Experience Benchmark</title><style>{CSS}</style></head><body><main>
<header><h1>{e(focus)} vs Phoenix Market: CMS Quality &amp; Patient Experience</h1>
<div class="sub">Release {e(facts['release_id'])} · {facts['focus_hospitals']} {e(focus)} hospitals benchmarked against
{facts['market_hospitals']} Phoenix-metro acute-care hospitals · Source: CMS Provider Data Catalog</div></header>
<h2>Executive summary <span class="tag">{tag}</span></h2><div class="summary">{e(summary)}</div>
<h2>System scorecard</h2><div class="tiles">{''.join(tiles)}</div>
<h2>{e(focus)} vs competing systems</h2><div class="grid">{''.join(cards)}</div>
<h2>Hospital heatmap</h2>
<p class="sub">Value and market rank for each {e(focus)} campus. Green = top half of market, red = bottom quartile.
Faded = low volume (under 100 surveys or 25 cases).</p>
<table><tr><th>Hospital</th>{head}</tr>{''.join(body)}</table>
<h2>Alerts since last release</h2>{alert_html}
<h2>Data quality gate</h2>
<p class="sub">{dq['counts']['PASS']} passed · {dq['counts']['WARN']} warnings · {dq['counts']['FAIL']} failed.
A failure stops the load and keeps the last good release on the dashboard.</p>
<table><tr><th>Check</th><th>Dataset</th><th>Status</th><th>Detail</th></tr>{dq_rows}</table>
<footer>Reporting periods: {e(periods)}.<br>System averages are unweighted means of each system's reporting
hospitals. Peer group: Maricopa and Pinal county acute-care hospitals. Public CMS data; not affiliated with
{e(focus)}.</footer></main></body></html>"""


def post_slack(summary: str, facts: dict, report_url: str | None = None) -> bool:
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        return False
    text = f"*{facts['focus_system']} CMS benchmark, release {facts['release_id']}*\n```{summary}```"
    if report_url:
        text += f"\nFull report: {report_url}"
    resp = requests.post(url, data=json.dumps({"text": text}),
                         headers={"Content-Type": "application/json"}, timeout=20)
    resp.raise_for_status()
    return True


def send_email(html_body: str, facts: dict, attachment: Path | None = None) -> bool:
    host, to = os.getenv("SMTP_HOST"), os.getenv("REPORT_EMAIL_TO")
    if not (host and to):
        return False
    msg = EmailMessage()
    msg["Subject"] = f"{facts['focus_system']} CMS benchmark - release {facts['release_id']}"
    msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "bi-automation@localhost"))
    msg["To"] = to
    msg.set_content("HTML report attached.")
    msg.add_alternative(html_body, subtype="html")
    if attachment:
        msg.add_attachment(attachment.read_bytes(), maintype="text", subtype="html", filename=attachment.name)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587"))) as s:
        s.starttls()
        if os.getenv("SMTP_USER"):
            s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
        s.send_message(msg)
    return True


def post_failure_alert(dq: dict) -> None:
    """Called when the quality gate fails, so a broken CMS pull is noticed the same day."""
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        return
    failed = [r for r in dq["results"] if r["status"] == "FAIL"]
    lines = "\n".join(f"- {r['check']} / {r['dataset']}: {r['detail']}" for r in failed)
    requests.post(url, json={"text": f":rotating_light: CMS benchmark load FAILED quality gate. "
                                     f"Dashboard kept on last good release.\n{lines}"}, timeout=20)
