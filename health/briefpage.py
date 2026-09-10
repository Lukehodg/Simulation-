"""The brief as a page you can glance at.

Same "Instrument" language as `health serve` — rules instead of cards,
monospaced figures in a column, the uncertainty next to the number — but
self-contained: one HTML file with its CSS inlined, so it opens straight from
`data/briefs/` or off a synced folder with no server running.

`render(brief)` turns a `brief.Brief` into that file's text. It reads the
computed payload for the figures and the model's prose for the words; it does
no arithmetic of its own.
"""

from __future__ import annotations

import html
import re
from datetime import date
from typing import Any

from .brief import Brief

_REC_CLASS = {"push": "good", "proceed": "ink", "hold": "warn", "pull_back": "bad"}

_CSS = """
:root{--ink:#101418;--paper:#F7F8F7;--panel:#FFF;--soft:#5A6470;--rule:#D8DDDA;
--hair:#E9ECEA;--signal:#1F4B99;--warn:#A9741A;--good:#2E6E4E;--bad:#9A3B34}
@media (prefers-color-scheme:dark){:root{--ink:#E6EAEE;--paper:#0E1214;
--panel:#131719;--soft:#8894A2;--rule:#262D31;--hair:#1B2124;--signal:#7FA8E8;
--warn:#D2A24E;--good:#6BB98C;--bad:#E08A80}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);line-height:1.55;
font-family:"Chivo",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
-webkit-font-smoothing:antialiased}
.mono{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;
font-variant-numeric:tabular-nums}
.wrap{max-width:760px;margin:0 auto;padding:34px 26px 60px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:16px;
border-bottom:2px solid var(--ink);padding-bottom:12px}
.top h1{font-size:21px;font-weight:700;margin:0;letter-spacing:-.01em}
.top .meta{font-family:"JetBrains Mono",monospace;font-size:10.5px;color:var(--soft);
letter-spacing:.07em;text-transform:uppercase;text-align:right}
.verdict{display:grid;grid-template-columns:auto 1fr;gap:22px;align-items:center;
padding:22px 0;border-bottom:1px solid var(--rule)}
.verdict .call{font-size:30px;font-weight:700;letter-spacing:-.02em;line-height:1;
text-transform:uppercase;white-space:nowrap}
.verdict .call.good{color:var(--good)}.verdict .call.warn{color:var(--warn)}
.verdict .call.bad{color:var(--bad)}.verdict .call.ink{color:var(--ink)}
.verdict p{margin:0;font-size:15px;color:var(--soft)}
section{padding:18px 0;border-bottom:1px solid var(--rule)}
h3{margin:0 0 12px;font-size:11px;font-weight:600;letter-spacing:.12em;
text-transform:uppercase;color:var(--soft);font-family:"JetBrains Mono",monospace}
.vital{display:grid;grid-template-columns:130px 66px 1fr 92px;gap:12px;
align-items:center;padding:8px 0;border-bottom:1px solid var(--hair)}
.vital:last-child{border-bottom:none}
.vital .k{font-size:13.5px}
.vital .val{font-family:"JetBrains Mono",monospace;font-size:15px;text-align:right}
.vital .z{font-family:"JetBrains Mono",monospace;font-size:12px;text-align:right;color:var(--soft)}
.vital .z.bad{color:var(--bad)}.vital .z.good{color:var(--good)}.vital .z.warn{color:var(--warn)}
.bar{position:relative;height:16px;background:var(--hair);border-radius:2px}
.bar .mid{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--soft)}
.bar .fill{position:absolute;top:0;bottom:0;background:var(--soft);border-radius:2px}
.bar .fill.bad{background:var(--bad)}.bar .fill.good{background:var(--good)}
.bar .fill.warn{background:var(--warn)}
.sleep{display:grid;grid-template-columns:1fr;gap:6px}
.sleep .line{display:flex;justify-content:space-between;font-size:13.5px}
.sleep .line .mono{font-size:13px}
.debtbar{height:14px;background:var(--hair);border-radius:2px;position:relative;margin-top:4px}
.debtbar .fill{position:absolute;left:0;top:0;bottom:0;background:var(--warn);border-radius:2px}
.prose{padding-top:6px}
.prose p{font-size:15.5px;line-height:1.68;margin:0 0 14px;max-width:64ch}
.prose p:last-child{margin-bottom:0}
.prose strong{font-weight:600}
.spark{display:grid;grid-template-columns:120px 1fr 64px;gap:12px;align-items:center;
padding:7px 0;border-bottom:1px solid var(--hair)}
.spark:last-child{border-bottom:none}
.spark .k{font-size:13px}
.spark .now{font-family:"JetBrains Mono",monospace;font-size:13px;text-align:right}
.list{font-family:"JetBrains Mono",monospace;font-size:12px;color:var(--soft);
line-height:1.75;margin:0;padding:0;list-style:none}
.list li{padding:3px 0;border-bottom:1px solid var(--hair)}
.list li:last-child{border-bottom:none}
.list li::before{content:"— ";color:var(--soft)}
footer{font-family:"JetBrains Mono",monospace;font-size:10.5px;color:var(--soft);
padding-top:18px;line-height:1.7}
@media (max-width:560px){.vital{grid-template-columns:96px 56px 1fr;}.vital .z{display:none}
.wrap{padding:24px 16px 44px}.verdict{grid-template-columns:1fr;gap:8px}}
"""


def _esc(text: Any) -> str:
    return html.escape(str(text))


def _prose(text: str) -> str:
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = _esc(para).replace("\n", " ")
        para = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", para)
        out.append(f"<p>{para}</p>")
    return "\n".join(out)


#: which direction is bad for each recovery metric
_BAD_WHEN_NEGATIVE = {"hrv_rmssd": True, "recovery_score": True, "resting_hr": False}


def _tone(z: float | None, bad_when_negative: bool) -> str:
    if z is None:
        return ""
    adverse = (z < 0) if bad_when_negative else (z > 0)
    if adverse:
        return "bad" if abs(z) >= 1.5 else "warn" if abs(z) >= 0.5 else ""
    return "good" if abs(z) >= 0.5 else ""


def _dev_bar(z: float | None, tone: str) -> str:
    """A track from -3 to +3 SD with a centre line and a marker at `z`."""
    if z is None:
        return '<div class="bar"><span class="mid"></span></div>'
    half = min(3.0, abs(z)) / 3 * 50            # percent from centre
    left = 50 - half if z < 0 else 50
    return (f'<div class="bar"><span class="mid"></span>'
            f'<span class="fill {tone}" style="left:{left:.1f}%;width:{half:.1f}%"></span></div>')


def _vitals(readiness: dict) -> str:
    rows = []
    for v in readiness.get("vitals", []):
        z = v.get("z")
        tone = _tone(z, _BAD_WHEN_NEGATIVE.get(v["metric"], True))
        zcls = tone
        zlabel = "—" if z is None else f"{z:+.1f} SD"
        rows.append(
            f'<div class="vital"><span class="k">{_esc(v["label"])}</span>'
            f'<span class="val">{_esc(_num(v["value"]))}</span>'
            f'{_dev_bar(z, tone)}'
            f'<span class="z {zcls}">{_esc(zlabel)} · n{v.get("n", 0)}</span></div>')
    return "\n".join(rows)


def _num(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.4g}"
    return str(value)


def _sleep(debt: dict) -> str:
    need = debt.get("need_hours")
    last = debt.get("last_night_hours")
    hours = debt.get("debt_hours") or 0.0
    width = min(100.0, abs(hours) / 12 * 100)
    return (
        '<div class="sleep">'
        f'<div class="line"><span>Last night</span>'
        f'<span class="mono">{_esc(_hrs(last))} h &nbsp;/&nbsp; need {_esc(_hrs(need))} h</span></div>'
        f'<div class="line"><span>Debt, {debt.get("nights", 0)} nights</span>'
        f'<span class="mono">{hours:+.1f} h</span></div>'
        f'<div class="debtbar"><span class="fill" style="width:{width:.0f}%"></span></div>'
        f'<div class="line" style="color:var(--soft)"><span class="mono" '
        f'style="font-size:10.5px">{_esc(debt.get("basis",""))}</span></div>'
        '</div>')


def _hrs(value: Any) -> str:
    return "—" if value is None else f"{float(value):.1f}"


def _sparklines(series: dict[str, list[dict]]) -> str:
    order = ["hrv_rmssd", "resting_hr", "recovery_score", "sleep_duration", "strain"]
    labels = {"hrv_rmssd": "HRV", "resting_hr": "Resting HR",
              "recovery_score": "Recovery", "sleep_duration": "Sleep (min)",
              "strain": "Strain"}
    rows = []
    for metric in order:
        pts = [p["value"] for p in series.get(metric, []) if p.get("value") is not None]
        if len(pts) < 2:
            continue
        rows.append(
            f'<div class="spark"><span class="k">{labels[metric]}</span>'
            f'{_spark_svg(pts)}'
            f'<span class="now">{pts[-1]:.4g}</span></div>')
    return "\n".join(rows)


def _spark_svg(values: list[float]) -> str:
    w, h, pad = 320, 30, 3
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = (w - 2 * pad) / (len(values) - 1)
    pts = " ".join(
        f"{pad + i * step:.1f},{pad + (1 - (v - lo) / span) * (h - 2 * pad):.1f}"
        for i, v in enumerate(values))
    last_x = pad + (len(values) - 1) * step
    last_y = pad + (1 - (values[-1] - lo) / span) * (h - 2 * pad)
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none">'
            f'<polyline points="{pts}" fill="none" stroke="var(--signal)" stroke-width="1.5"/>'
            f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.5" fill="var(--signal)"/></svg>')


def _drivers(drivers: dict) -> str:
    survivors = drivers.get("survivors", [])
    if not survivors:
        return (f'<p class="list" style="border:none">Nothing cleared the interval '
                f'out of {drivers.get("comparisons_made", 0)} pairs tested.</p>')
    items = "".join(
        f'<li>{_esc(s["input"])} → {_esc(s["recovery_metric"])} '
        f'(lag {s["lag_days"]}d): {_esc(s["reading"])}</li>'
        for s in survivors)
    return (f'<ul class="list">{items}</ul>'
            f'<p class="list" style="border:none;padding-top:8px">'
            f'{_esc(drivers.get("caveat",""))}</p>')


def _strength(link: dict) -> str:
    rows = link.get("exercises", [])
    if not rows:
        return '<p class="list" style="border:none">Not enough dated sessions per lift yet.</p>'
    out = []
    for ex in rows:
        buckets = ex["by_recovery"]
        cells = ", ".join(
            f'{b} {buckets[b]["mean_e1rm_vs_trend_kg"]:+.1f}kg (n{buckets[b]["n"]})'
            for b in ("green", "amber", "red") if b in buckets)
        out.append(f'<li>{_esc(ex["exercise"])}: {_esc(cells)}</li>')
    lows = link.get("session_lows", {})
    out.append(f'<li>session lows: {lows.get("on_a_red_or_under_slept_day", 0)} of '
               f'{lows.get("total", 0)} on a red or under-slept day</li>')
    return f'<ul class="list">{"".join(out)}</ul>'


def render(brief: Brief) -> str:
    p = brief.payload
    weekly = brief.span == "week"
    readiness = p.get("readiness") or p.get("readiness_today") or {}
    rec = readiness.get("recommendation", "proceed")
    debt = p.get("sleep_debt", {})

    parts: list[str] = []
    parts.append(
        f'<div class="top"><h1>{"Weekly" if weekly else "Daily"} brief</h1>'
        f'<div class="meta">{_esc(p.get("week") or p.get("date", date.today()))}<br>'
        f'computed on this machine</div></div>')

    if rec:
        parts.append(
            f'<div class="verdict"><span class="call {_REC_CLASS.get(rec,"ink")}">'
            f'{_esc(rec.replace("_"," "))}</span>'
            f'<p>{_esc(readiness.get("advice",""))}</p></div>')

    if readiness.get("vitals"):
        parts.append(f'<section><h3>Where the body is</h3>{_vitals(readiness)}</section>')

    if debt:
        parts.append(f'<section><h3>Sleep</h3>{_sleep(debt)}</section>')

    if brief.text:
        parts.append(f'<section><h3>The read</h3><div class="prose">{_prose(brief.text)}</div></section>')

    if weekly:
        series = p.get("daily_series", {})
        if series:
            parts.append(f'<section><h3>The week</h3>{_sparklines(series)}</section>')
        if p.get("recovery_drivers"):
            parts.append(f'<section><h3>What moved recovery</h3>{_drivers(p["recovery_drivers"])}</section>')
        if p.get("strength_vs_recovery"):
            parts.append(f'<section><h3>Strength vs recovery</h3>{_strength(p["strength_vs_recovery"])}</section>')

    reasons = readiness.get("reasons", [])
    caveats = readiness.get("caveats", [])
    if reasons or caveats:
        body = "".join(f"<li>{_esc(r)}</li>" for r in reasons)
        body += "".join(f'<li style="color:var(--warn)">{_esc(c)}</li>' for c in caveats)
        parts.append(f'<section><h3>Reasons &amp; gaps</h3><ul class="list">{body}</ul></section>')

    usage = brief.usage
    tok = (f' · {usage["input"]}+{usage["output"]} tokens' if usage else "")
    parts.append(
        f'<footer>{_esc(brief.model)}{tok} · only the labelled figures left the '
        f'machine · not a diagnosis</footer>')

    return _PAGE.format(css=_CSS, title=f'{"weekly" if weekly else "today"} brief',
                        body="\n".join(parts))


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>health · {title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Chivo:wght@400;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>{css}</style></head>
<body><div class="wrap">
{body}
</div></body></html>
"""
