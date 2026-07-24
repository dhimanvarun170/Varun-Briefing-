"""
Price-action / ICT-lite analysis engine.

Computes, per instrument:
  - HTF trend bias (daily EMA slope + last close vs prior day range)
  - Key levels: prior day high/low, prior day close, recent swing
    high/low (liquidity pools), and any fair value gaps (FVGs) in
    the recent intraday range
  - A bias label: bullish / bearish / neutral

Also computes 10Y yield direction and folds it in as a confluence
check across DXY / NQ / Gold, per the user's standing rule:
  rising yields -> headwind gold & NQ, tailwind DXY
  falling yields -> tailwind gold & NQ, headwind DXY
"""
from __future__ import annotations
from app.logger import get_logger

log = get_logger(__name__)


def _pct_change(new, old):
    if old in (None, 0) or new is None:
        return None
    return (new - old) / old * 100.0


def _swing_points(df, lookback_bars: int):
    if df is None or df.empty or len(df) < 5:
        return None, None
    window = df.tail(lookback_bars)
    swing_high = float(window["High"].max())
    swing_low = float(window["Low"].min())
    return swing_high, swing_low


def _detect_fvgs(df, lookback_bars: int):
    if df is None or df.empty or len(df) < 5:
        return {"bullish_fvg": None, "bearish_fvg": None}

    window = df.tail(lookback_bars).reset_index(drop=True)
    bullish, bearish = None, None
    for i in range(2, len(window)):
        c0 = window.iloc[i - 2]
        c2 = window.iloc[i]
        if c0["High"] < c2["Low"]:
            bullish = {"gap_low": float(c0["High"]), "gap_high": float(c2["Low"])}
        if c0["Low"] > c2["High"]:
            bearish = {"gap_low": float(c2["High"]), "gap_high": float(c0["Low"])}
    return {"bullish_fvg": bullish, "bearish_fvg": bearish}


def analyze_instrument(key: str, fetch_result: dict, a_cfg: dict) -> dict:
    if not fetch_result.get("ok"):
        return {
            "ok": False,
            "reason": fetch_result.get("reason", "unknown fetch error"),
            "bias": "UNKNOWN - data unavailable",
        }

    daily = fetch_result["daily"]
    intraday = fetch_result["intraday"]
    last_price = fetch_result["last_price"]

    if daily.empty or len(daily) < 3:
        return {
            "ok": False,
            "reason": "insufficient daily history",
            "bias": "UNKNOWN - insufficient data",
        }

    prior_day = daily.iloc[-2] if len(daily) >= 2 else daily.iloc[-1]
    prior_high = float(prior_day["High"])
    prior_low = float(prior_day["Low"])
    prior_close = float(prior_day["Close"])

    ema_fast = daily["Close"].ewm(span=8).mean().iloc[-1]
    ema_slow = daily["Close"].ewm(span=21).mean().iloc[-1]
    htf_trend = "up" if ema_fast > ema_slow else "down" if ema_fast < ema_slow else "flat"

    chg_vs_prior_close = _pct_change(last_price, prior_close)

    swing_high, swing_low = _swing_points(intraday if not intraday.empty else daily,
                                           a_cfg["swing_lookback_bars"])
    fvgs = _detect_fvgs(intraday if not intraday.empty else daily,
                         a_cfg["fvg_lookback_bars"])

    if last_price is None:
        bias = "UNKNOWN"
    elif htf_trend == "up" and last_price >= prior_close:
        bias = "bullish"
    elif htf_trend == "down" and last_price <= prior_close:
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "ok": True,
        "ticker_used": fetch_result["ticker_used"],
        "last_price": last_price,
        "prior_day_high": prior_high,
        "prior_day_low": prior_low,
        "prior_day_close": prior_close,
        "pct_change_vs_prior_close": chg_vs_prior_close,
        "htf_trend_ema8_vs_ema21": htf_trend,
        "swing_high_liquidity_pool": swing_high,
        "swing_low_liquidity_pool": swing_low,
        "fair_value_gaps": fvgs,
        "bias": bias,
    }


def analyze_yield(fetch_result: dict) -> dict:
    if not fetch_result.get("ok"):
        return {"ok": False, "reason": fetch_result.get("reason"), "direction": "UNKNOWN"}

    daily = fetch_result["daily"]
    if daily.empty or len(daily) < 2:
        return {"ok": False, "reason": "insufficient data", "direction": "UNKNOWN"}

    last = float(daily["Close"].iloc[-1])
    prev = float(daily["Close"].iloc[-2])
    direction = "rising" if last > prev else "falling" if last < prev else "flat"
    return {
        "ok": True,
        "last_yield": last,
        "prev_yield": prev,
        "direction": direction,
    }


def apply_manual_gamma_overrides(instrument_analysis: dict, overrides: dict, key: str) -> dict:
    if key == "DXY":
        instrument_analysis["gamma_data"] = {
            "available": False,
            "note": "DXY has no reliable options/gamma chain (thin ICE DX "
                    "options market) - price action + macro/news only, by design.",
        }
        return instrument_analysis

    gamma = overrides.get(key)
    if not gamma:
        instrument_analysis["gamma_data"] = {
            "available": False,
            "note": "No manual gamma/options data provided for this run "
                    "(no free API exists for call/put walls, gamma flip, "
                    "net GEX, max pain, or OI). Bias below is price-action-only. "
                    "Paste your morning data into data/manual_overrides.json "
                    "to layer it in.",
        }
    else:
        instrument_analysis["gamma_data"] = {"available": True, **gamma}
    return instrument_analysis


def build_confluence_notes(nq: dict, dxy: dict, gc: dict, tnx: dict) -> list:
    notes = []
    if tnx.get("ok"):
        d = tnx["direction"]
        if d == "rising":
            notes.append("10Y yield RISING -> headwind for Gold and NQ/growth, tailwind for DXY.")
        elif d == "falling":
            notes.append("10Y yield FALLING -> tailwind for Gold and NQ/growth, headwind for DXY.")
        else:
            notes.append("10Y yield FLAT -> limited directional confluence from rates today.")
    else:
        notes.append("10Y yield data unavailable this run - skip rates confluence check, "
                      "rely on DXY/Gold/NQ price action agreement instead.")

    if dxy.get("ok") and gc.get("ok"):
        if dxy["bias"] == "bullish" and gc["bias"] == "bullish":
            notes.append("DXY and Gold both showing bullish bias simultaneously - "
                          "this is a non-confirmation / conflict. Treat both with caution "
                          "until one resolves.")
        elif dxy["bias"] == "bearish" and gc["bias"] == "bearish":
            notes.append("DXY and Gold both showing bearish bias simultaneously - "
                          "non-confirmation / conflict. Wait for resolution.")
        else:
            notes.append("DXY and Gold biases are inversely aligned (normal relationship) - "
                          "supports higher-confidence conviction on both.")
    return notes
