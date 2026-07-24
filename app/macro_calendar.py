"""
Free macro/economic calendar layer.

Data source: nfs.faireconomy.media/ff_calendar_thisweek.json
This is a public JSON feed ForexFactory serves for embeddable widgets.
It is UNOFFICIAL: no auth, no key, no guaranteed uptime or schema
stability. Treat it as best-effort. If it fails or the schema shifts,
the report must still generate with a clear fallback note - it must
NEVER crash the pipeline.
"""
from __future__ import annotations
import datetime as dt
import requests
from dateutil import parser as dateparser
from app.logger import get_logger

log = get_logger(__name__)

IMPACT_MAP = {"High": 3, "Medium": 2, "Low": 1, "Holiday": 0}


def fetch_macro_events(cfg: dict) -> dict:
    mc_cfg = cfg["macro_calendar"]
    url = mc_cfg["source_url"]
    timeout = mc_cfg.get("request_timeout_seconds", 10)

    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (compatible; free-market-briefing-bot/1.0)"
        })
        resp.raise_for_status()
        raw_events = resp.json()
    except Exception as e:
        log.error(f"Macro calendar fetch failed: {e}")
        return {"ok": False, "reason": str(e), "events": []}

    now = dt.datetime.now(dt.timezone.utc)
    lookahead_hours = mc_cfg.get("lookahead_hours", 24)
    horizon = now + dt.timedelta(hours=lookahead_hours)

    parsed = []
    for ev in raw_events:
        try:
            date_str = ev.get("date") or ev.get("Date")
            if not date_str:
                continue
            event_time = dateparser.parse(date_str)
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=dt.timezone.utc)
            else:
                event_time = event_time.astimezone(dt.timezone.utc)

            parsed.append({
                "title": ev.get("title") or ev.get("Title") or "Unknown event",
                "country": ev.get("country") or ev.get("Country") or "",
                "impact": ev.get("impact") or ev.get("Impact") or "Low",
                "time_utc": event_time.isoformat(),
                "forecast": ev.get("forecast") or ev.get("Forecast") or "",
                "previous": ev.get("previous") or ev.get("Previous") or "",
            })
        except Exception:
            continue

    upcoming = [e for e in parsed
                if now <= dateparser.parse(e["time_utc"]) <= horizon]
    upcoming.sort(key=lambda e: e["time_utc"])

    high_impact = [e for e in upcoming if e["impact"] == "High"]

    return {
        "ok": True,
        "fetched_at_utc": now.isoformat(),
        "window_hours": lookahead_hours,
        "all_upcoming": upcoming,
        "high_impact_upcoming": high_impact,
    }
