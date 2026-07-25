"""
Level-watch: continuously checks NQ / DXY / Gold spot price against
Daily / Weekly / Monthly OHLC levels (prior period High, Low, Close)
and fires a Telegram alert the first time price touches/crosses one.

State is tracked in data/level_alert_state.json so the same level
doesn't spam you every 15 minutes once touched - each level fires
once per UTC calendar day, then resets.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import pandas as pd
from app.logger import get_logger

log = get_logger(__name__)

try:
    import yfinance as yf
except ImportError:
    yf = None

STATE_PATH = "data/level_alert_state.json"

TOUCH_TOLERANCE = {
    "NQ": 0.0008,
    "DXY": 0.0008,
    "GOLD_FUT": 0.0008,
    "GOLD_SPOT": 0.0008,
    "SILVER_FUT": 0.0008,
    "SILVER_SPOT": 0.0008,
    "OIL_FUT": 0.0008,
    "EURUSD": 0.0008,
    "GBPUSD": 0.0008,
    "USDJPY": 0.0008,
    "BTC": 0.0008,
  "US10Y_YIELD": 0.0008,
    "US10Y_NOTE_FUT": 0.0008,
    "US2Y_YIELD": 0.0008,
}


def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"date": "", "triggered": {}}
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"date": "", "triggered": {}}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _reset_state_if_new_day(state: dict) -> dict:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    if state.get("date") != today:
        return {"date": today, "triggered": {}}
    return state


def _compute_levels(daily_df) -> dict:
    levels = {}
    if daily_df is None or daily_df.empty or len(daily_df) < 3:
        return levels

    prior_day = daily_df.iloc[-2]
    levels["Prior Day High"] = float(prior_day["High"])
    levels["Prior Day Low"] = float(prior_day["Low"])
    levels["Prior Day Close"] = float(prior_day["Close"])

    weekly = daily_df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    )
    if len(weekly) >= 2:
        prior_week = weekly.iloc[-2]
        levels["Prior Week High"] = float(prior_week["High"])
        levels["Prior Week Low"] = float(prior_week["Low"])
        levels["Prior Week Close"] = float(prior_week["Close"])

    monthly = daily_df.resample("ME").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    )
    if len(monthly) >= 2:
        prior_month = monthly.iloc[-2]
        levels["Prior Month High"] = float(prior_month["High"])
        levels["Prior Month Low"] = float(prior_month["Low"])
        levels["Prior Month Close"] = float(prior_month["Close"])

    return levels


def fetch_levels_for_instrument(ticker: str, fallback_ticker) -> dict:
    if yf is None:
        return {"ok": False, "reason": "yfinance not installed"}

    for candidate in [ticker, fallback_ticker]:
        if not candidate:
            continue
        try:
            tk = yf.Ticker(candidate)
            daily = tk.history(period="90d", interval="1d")
            intraday = tk.history(period="2d", interval="15m")
            if daily.empty:
                continue
            spot = float(intraday["Close"].iloc[-1]) if not intraday.empty else float(daily["Close"].iloc[-1])
            levels = _compute_levels(daily)
            return {"ok": True, "ticker_used": candidate, "spot": spot, "levels": levels}
        except Exception as e:
            log.warning(f"Level fetch failed for {candidate}: {e}")
            continue
    return {"ok": False, "reason": f"all tickers failed for {ticker}/{fallback_ticker}"}


def check_all_levels(cfg: dict) -> list:
    state = _load_state()
    state = _reset_state_if_new_day(state)

    events = []
    for key, meta in cfg.get("level_watch_instruments", {}).items():
        result = fetch_levels_for_instrument(meta["yf_ticker"], meta.get("fallback_ticker"))
        if not result.get("ok"):
            log.warning(f"{key}: level check skipped - {result.get('reason')}")
            continue

        spot = result["spot"]
        tol = TOUCH_TOLERANCE.get(key, 0.001)
        already = state["triggered"].setdefault(key, [])

        for level_name, level_value in result["levels"].items():
            if level_value is None or level_value == 0:
                continue
            distance = abs(spot - level_value) / level_value
            touched = distance <= tol
            if touched and level_name not in already:
                events.append({
                    "instrument": key,
                    "level_name": level_name,
                    "level_value": level_value,
                    "spot": spot,
                })
                already.append(level_name)

    _save_state(state)
    return events


def _level_type(level_name: str) -> str:
    if level_name.endswith("High"):
        return "high"
    if level_name.endswith("Low"):
        return "low"
    return "close"


def analyze_full_watchlist(cfg: dict) -> list:
    results = []
    for key, meta in cfg.get("level_watch_instruments", {}).items():
        out = {"key": key, "label": meta.get("label", key), "ok": False}
        for candidate in [meta["yf_ticker"], meta.get("fallback_ticker")]:
            if not candidate:
                continue
            try:
                tk = yf.Ticker(candidate)
                daily = tk.history(period="90d", interval="1d")
                intraday = tk.history(period="2d", interval="15m")
                if daily.empty or len(daily) < 3:
                    continue

                spot = float(intraday["Close"].iloc[-1]) if not intraday.empty else float(daily["Close"].iloc[-1])
                today_bar = daily.iloc[-1]
                today_high = float(today_bar["High"])
                today_low = float(today_bar["Low"])

                ema_fast = daily["Close"].ewm(span=8).mean().iloc[-1]
                ema_slow = daily["Close"].ewm(span=21).mean().iloc[-1]
                prior_close = float(daily.iloc[-2]["Close"])
                if ema_fast > ema_slow and spot >= prior_close:
                    bias = "bullish"
                elif ema_fast < ema_slow and spot <= prior_close:
                    bias = "bearish"
                else:
                    bias = "neutral"

                levels = _compute_levels(daily)
                level_rows = []
                for name, value in levels.items():
                    distance = spot - value
                    ltype = _level_type(name)
                    if ltype == "high":
                        swept = today_high >= value
                    elif ltype == "low":
                        swept = today_low <= value
                    else:
                        swept = today_low <= value <= today_high
                    level_rows.append({
                        "name": name, "value": value,
                        "distance": distance, "swept_today": swept,
                    })

                out.update({"ok": True, "ticker_used": candidate, "spot": spot,
                            "bias": bias, "levels": level_rows})
                break
            except Exception as e:
                log.warning(f"Full-watchlist analysis failed for {candidate}: {e}")
                continue
        results.append(out)
    return results


def fetch_headline(ticker: str) -> str:
    import requests
    import xml.etree.ElementTree as ET
    from urllib.parse import quote

    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote(ticker)}&region=US&lang=en-US"
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        item = root.find(".//item")
        if item is not None:
            title = item.find("title")
            if title is not None and title.text:
                return title.text.strip()
        return "No headline available"
    except Exception as e:
        log.warning(f"Headline fetch failed for {ticker}: {e}")
        return "No headline available (feed unavailable)"


def rank_top_setups(watchlist: list, top_n: int = 2) -> list:
    scored = []
    for inst in watchlist:
        if not inst.get("ok") or inst["bias"] == "neutral":
            continue
        candidates = [l for l in inst["levels"] if not l["swept_today"]]
        if not candidates:
            continue
        closest = min(candidates, key=lambda l: abs(l["distance"]))
        scored.append({
            "key": inst["key"], "label": inst["label"], "bias": inst["bias"],
            "spot": inst["spot"], "target_level": closest["name"],
            "target_value": closest["value"], "distance": closest["distance"],
        })
    scored.sort(key=lambda s: abs(s["distance"]))
    return scored[:top_n]


def format_proximity_summary(watchlist: list) -> str:
    """
    Ranks every instrument by how close spot is to its nearest D/W/M
    level (touched or not), so you can see at a glance what's coming
    up across the whole watchlist - not just what already triggered.
    """
    rows = []
    for inst in watchlist:
        if not inst.get("ok") or not inst.get("levels"):
            continue
        closest = min(inst["levels"], key=lambda l: abs(l["distance"]))
        rows.append({
            "label": inst["label"],
            "bias": inst["bias"],
            "spot": inst["spot"],
            "level_name": closest["name"],
            "level_value": closest["value"],
            "distance": closest["distance"],
            "swept": closest["swept_today"],
        })
    rows.sort(key=lambda r: abs(r["distance"]))

    lines = ["Nearest D/W/M levels (closest first):"]
    for r in rows:
        sign = "+" if r["distance"] >= 0 else ""
        tag = "SWEPT" if r["swept"] else "not swept"
        lines.append(
            f"{r['label']}: {r['spot']:,.2f} -> {r['level_name']} "
            f"{r['level_value']:,.2f} ({sign}{r['distance']:.2f} away, {tag}) "
            f"[{r['bias'].upper()}]"
        )
    return "\n".join(lines)
    lines = ["Level Alert"]
    for e in events:
        lines.append(
            f"{e['instrument']}: touched {e['level_name']} "
            f"({e['level_value']:,.2f}) - spot {e['spot']:,.2f}"
        )
    return "\n".join(lines)
