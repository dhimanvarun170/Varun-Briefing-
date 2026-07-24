"""
Builds the final report object (dict) and renders it to Markdown.
Style: concise prop-trader briefing. No newsletter fluff.
"""
from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo
from app import analysis
from app.logger import get_logger

log = get_logger(__name__)


def _fmt(v, decimals=2):
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:,.{decimals}f}"
    return str(v)


def _bias_line(name: str, a: dict) -> str:
    if not a.get("ok"):
        return f"**{name} bias:** UNKNOWN - {a.get('reason', 'data unavailable')}"
    return f"**{name} bias:** {a['bias'].upper()}"


def build_report(session: str, cfg: dict, fetch_data: dict, macro_data: dict,
                  overrides: dict) -> dict:
    a_cfg = cfg["analysis"]
    now_utc = dt.datetime.now(dt.timezone.utc)
    display_tz = ZoneInfo(cfg.get("display_timezone", "UTC"))
    now_display = now_utc.astimezone(display_tz)

    session_cfg = cfg["sessions"][session]

    nq = analysis.analyze_instrument("NQ", fetch_data.get("NQ", {"ok": False, "reason": "no data"}), a_cfg)
    dxy = analysis.analyze_instrument("DXY", fetch_data.get("DXY", {"ok": False, "reason": "no data"}), a_cfg)
    gc = analysis.analyze_instrument("GC", fetch_data.get("GC", {"ok": False, "reason": "no data"}), a_cfg)
    tnx = analysis.analyze_yield(fetch_data.get("TNX", {"ok": False, "reason": "no data"}))

    nq = analysis.apply_manual_gamma_overrides(nq, overrides, "NQ")
    dxy = analysis.apply_manual_gamma_overrides(dxy, overrides, "DXY")
    gc = analysis.apply_manual_gamma_overrides(gc, overrides, "GC")

    confluence_notes = analysis.build_confluence_notes(nq, dxy, gc, tnx)

    biases = [b["bias"] for b in (nq, gc) if b.get("ok")]
    if not biases:
        final_stance = "WAIT"
    elif biases.count("bullish") == len(biases):
        final_stance = "BULLISH (risk-on lean)"
    elif biases.count("bearish") == len(biases):
        final_stance = "BEARISH (risk-off lean)"
    elif "neutral" in biases or len(set(biases)) > 1:
        final_stance = "NEUTRAL / WAIT - mixed signals, let price confirm"
    else:
        final_stance = "WAIT"

    macro_ok = macro_data.get("ok", False)
    high_impact = macro_data.get("high_impact_upcoming", []) if macro_ok else []
    all_upcoming = macro_data.get("all_upcoming", []) if macro_ok else []

    report = {
        "meta": {
            "session": session,
            "session_label": session_cfg["label"],
            "session_window_utc": session_cfg["session_window_utc"],
            "generated_at_utc": now_utc.isoformat(),
            "generated_at_display_tz": now_display.isoformat(),
            "display_timezone": cfg.get("display_timezone", "UTC"),
        },
        "macro": {
            "ok": macro_ok,
            "reason": macro_data.get("reason") if not macro_ok else None,
            "high_impact_next_window": high_impact,
            "all_upcoming_next_window": all_upcoming,
            "window_hours": macro_data.get("window_hours"),
        },
        "instruments": {
            "NQ": nq,
            "DXY": dxy,
            "GC": gc,
        },
        "yields": {
            "TNX": tnx,
        },
        "confluence_notes": confluence_notes,
        "final_stance": final_stance,
    }
    return report


def _levels_block(name: str, a: dict) -> str:
    if not a.get("ok"):
        return f"- {name}: levels unavailable ({a.get('reason', 'no data')})\n"
    lines = [
        f"- **{name} last price:** {_fmt(a['last_price'])}",
        f"  - Prior day H/L/C: {_fmt(a['prior_day_high'])} / {_fmt(a['prior_day_low'])} / {_fmt(a['prior_day_close'])}",
        f"  - Swing liquidity pool (recent H/L): {_fmt(a['swing_high_liquidity_pool'])} / {_fmt(a['swing_low_liquidity_pool'])}",
    ]
    fvg = a.get("fair_value_gaps", {})
    if fvg.get("bullish_fvg"):
        g = fvg["bullish_fvg"]
        lines.append(f"  - Bullish FVG zone: {_fmt(g['gap_low'])} - {_fmt(g['gap_high'])}")
    if fvg.get("bearish_fvg"):
        g = fvg["bearish_fvg"]
        lines.append(f"  - Bearish FVG zone: {_fmt(g['gap_low'])} - {_fmt(g['gap_high'])}")
    gamma = a.get("gamma_data", {})
    if gamma.get("available"):
        lines.append(
            f"  - Gamma/options (manual): call wall {gamma.get('call_wall', 'N/A')} | "
            f"put wall {gamma.get('put_wall', 'N/A')} | gamma flip {gamma.get('gamma_flip', 'N/A')} | "
            f"net GEX {gamma.get('net_gex', 'N/A')} | max pain {gamma.get('max_pain', 'N/A')} | "
            f"top OI {gamma.get('top_oi', 'N/A')}"
        )
    else:
        lines.append(f"  - Gamma/options: {gamma.get('note', 'unavailable')}")
    return "\n".join(lines) + "\n"


def render_markdown(report: dict) -> str:
    meta = report["meta"]
    nq, dxy, gc = report["instruments"]["NQ"], report["instruments"]["DXY"], report["instruments"]["GC"]
    tnx = report["yields"]["TNX"]
    macro = report["macro"]

    lines = []
    lines.append(f"# Market Pre-Session Briefing - {meta['session_label']}")
    lines.append("")
    lines.append(f"**Timestamp (UTC):** {meta['generated_at_utc']}  ")
    lines.append(f"**Timestamp ({meta['display_timezone']}):** {meta['generated_at_display_tz']}  ")
    lines.append(f"**Session tag:** {meta['session_label']} | window (UTC): {meta['session_window_utc']}")
    lines.append("")
    lines.append("---")
    lines.append("## Macro Events Summary")
    if not macro["ok"]:
        lines.append(f"Macro calendar unavailable this run: {macro['reason']}. "
                      f"Proceed on price action + known scheduled risk only.")
    else:
        hi = macro["high_impact_next_window"]
        if hi:
            lines.append(f"**High-impact events in next {macro['window_hours']}h:**")
            for e in hi:
                lines.append(f"- `{e['time_utc']}` [{e['country']}] {e['title']} "
                              f"(forecast: {e['forecast'] or 'n/a'}, previous: {e['previous'] or 'n/a'})")
        else:
            lines.append(f"No high-impact events flagged in the next {macro['window_hours']}h window.")
        other = [e for e in macro["all_upcoming_next_window"] if e["impact"] != "High"]
        if other:
            lines.append("")
            lines.append(f"Other scheduled events (med/low impact): {len(other)} - see JSON for full list.")
    lines.append("")
    lines.append("---")
    lines.append("## Instrument Bias")
    lines.append("")
    lines.append("### NQ (Nasdaq futures)")
    lines.append(_bias_line("NQ", nq))
    lines.append(_levels_block("NQ", nq))
    lines.append("### DXY (Dollar Index)")
    lines.append(_bias_line("DXY", dxy))
    lines.append(_levels_block("DXY", dxy))
    lines.append("### Gold (GC)")
    lines.append(_bias_line("Gold", gc))
    lines.append(_levels_block("Gold", gc))

    lines.append("---")
    lines.append("## Yields / Rates Confluence")
    if tnx.get("ok"):
        lines.append(f"- 10Y yield: {_fmt(tnx['last_yield'])} (prev {_fmt(tnx['prev_yield'])}) - "
                      f"direction: **{tnx['direction'].upper()}**")
    else:
        lines.append(f"- 10Y yield data unavailable: {tnx.get('reason', 'unknown')}")
    for note in report["confluence_notes"]:
        lines.append(f"- {note}")

    lines.append("")
    lines.append("---")
    lines.append("## Setup Validation Checklist")
    lines.append("- [ ] HTF bias (daily EMA8/EMA21) agrees with intended trade direction")
    lines.append("- [ ] Price at/near a liquidity pool (swing high/low) or FVG zone, not mid-range")
    lines.append("- [ ] DXY / Gold relationship is either normally inverse (confirming) or clearly explained if not")
    lines.append("- [ ] 10Y yield direction supports the trade (or is neutral/flat)")
    lines.append("- [ ] No high-impact macro event due within the next 60-90 minutes")
    lines.append("- [ ] Gamma/options levels (if provided) agree with direction")
    lines.append("")
    lines.append("## Invalidation Conditions")
    lines.append("- Price closes back through prior day close against the stated bias")
    lines.append("- HTF trend flips (EMA8 crosses EMA21 the other way) intraday")
    lines.append("- Gamma flip level is breached against the trade direction (if gamma data available)")
    lines.append("")
    lines.append("## No-Trade Conditions")
    lines.append("- Macro calendar unavailable AND a known high-impact release is due this session")
    lines.append("- DXY and Gold both showing the same-direction bias (non-confirmation)")
    lines.append("- Any instrument bias is UNKNOWN due to data fetch failure")
    lines.append("- Price sitting mid-range with no liquidity pool or FVG nearby")
    lines.append("")
    lines.append("---")
    lines.append(f"## FINAL STANCE: {report['final_stance']}")
    lines.append("")
    lines.append("Generated automatically. Price data via Yahoo Finance (delayed, unofficial). "
                  "Macro calendar via unofficial public feed, best-effort. Gamma/options data "
                  "only present if manually provided. This is a bias/context briefing, "
                  "not a signal or execution recommendation.")

    return "\n".join(lines)


def render_full_watchlist_markdown(watchlist: list, top_setups: list) -> str:
    lines = ["---", "## Full Watchlist - D/W/M Levels & Sweep Status", ""]
    lines.append("Distances shown are plain points/price away from spot, not percentages. "
                  "Swept today means today's session has already traded through that level.")
    lines.append("")
    for inst in watchlist:
        if not inst.get("ok"):
            lines.append(f"### {inst.get('label', inst.get('key'))}")
            lines.append(f"Data unavailable this run.\n")
            continue
        lines.append(f"### {inst['label']} - bias: {inst['bias'].upper()} - spot {_fmt(inst['spot'])}")
        for lvl in inst["levels"]:
            tag = "SWEPT" if lvl["swept_today"] else "not swept"
            sign = "+" if lvl["distance"] >= 0 else ""
            lines.append(f"- {lvl['name']}: {_fmt(lvl['value'])} "
                          f"({sign}{_fmt(lvl['distance'])} away) - {tag}")
        lines.append("")

    lines.append("---")
    lines.append("## Top Setups (from this watchlist)")
    if not top_setups:
        lines.append("No clear-bias / unswept-level setups this run.")
    else:
        for i, s in enumerate(top_setups, 1):
            sign = "+" if s["distance"] >= 0 else ""
            lines.append(
                f"{i}. **{s['label']}** - {s['bias'].upper()}, spot {_fmt(s['spot'])}, "
                f"targeting {s['target_level']} at {_fmt(s['target_value'])} "
                f"({sign}{_fmt(s['distance'])} away, unswept)"
            )
    lines.append("")
    return "\n".join(lines)


def render_telegram_summary(report: dict, repo_url: str = "", date_str: str = "") -> str:
    meta = report["meta"]
    nq, dxy, gc = report["instruments"]["NQ"], report["instruments"]["DXY"], report["instruments"]["GC"]
    tnx = report["yields"]["TNX"]

    def b(a):
        return a["bias"].upper() if a.get("ok") else "UNKNOWN"

    lines = [
        f"Report: {meta['session_label']} - {date_str}",
        "",
        f"NQ: {b(nq)}" + (f" ({_fmt(nq['last_price'])})" if nq.get("ok") else ""),
        f"DXY: {b(dxy)}" + (f" ({_fmt(dxy['last_price'])})" if dxy.get("ok") else ""),
        f"Gold: {b(gc)}" + (f" ({_fmt(gc['last_price'])})" if gc.get("ok") else ""),
    ]
    if tnx.get("ok"):
        lines.append(f"10Y yield: {tnx['direction'].upper()}")
    lines.append("")
    lines.append(f"FINAL STANCE: {report['final_stance']}")
    if repo_url:
        session = meta["session"]
        lines.append("")
        lines.append(f"Full report: {repo_url}/blob/main/reports/{date_str}-{session}.md")
    return "\n".join(lines)


def strip_dataframes(report: dict) -> dict:
    import copy
    return copy.deepcopy(report)
