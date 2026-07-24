"""
Free market-data fetch layer.

Uses yfinance, an unofficial wrapper around Yahoo Finance's public
endpoints. No API key, no cost. Trade-offs (documented for the user):

  - Not official / no SLA. Yahoo can rate-limit or block an IP that
    calls too often. GitHub Actions runners use rotating shared IPs,
    which usually helps but is not guaranteed.
  - Futures tickers (NQ=F, GC=F, DX=F) can have thin/no data during
    exact rollover days or short holiday closures. A fallback ticker
    is tried automatically (see config.yaml).
  - Data is typically delayed slightly intraday (not tick-perfect),
    which is fine for a pre-session bias briefing but NOT for
    execution-grade signals.

Every function returns a dict with a top-level "ok" boolean so
callers can render a clean fallback message instead of crashing.
"""
from __future__ import annotations
import datetime as dt
from app.logger import get_logger

log = get_logger(__name__)

try:
    import yfinance as yf
except ImportError:
    yf = None


def _empty_result(reason: str) -> dict:
    return {"ok": False, "reason": reason}


def fetch_instrument_data(ticker: str, fallback_ticker, interval: str,
                           lookback_days: int, daily_lookback_days: int) -> dict:
    if yf is None:
        return _empty_result("yfinance not installed")

    for candidate in [ticker, fallback_ticker]:
        if not candidate:
            continue
        try:
            tk = yf.Ticker(candidate)

            intraday = tk.history(period=f"{lookback_days}d", interval=interval)
            daily = tk.history(period=f"{daily_lookback_days}d", interval="1d")

            if intraday.empty and daily.empty:
                log.warning(f"No data returned for {candidate}, trying fallback if any.")
                continue

            last_price = None
            if not intraday.empty:
                last_price = float(intraday["Close"].iloc[-1])
            elif not daily.empty:
                last_price = float(daily["Close"].iloc[-1])

            return {
                "ok": True,
                "ticker_used": candidate,
                "last_price": last_price,
                "intraday": intraday,
                "daily": daily,
                "fetched_at_utc": dt.datetime.utcnow().isoformat(),
            }
        except Exception as e:
            log.warning(f"Fetch failed for {candidate}: {e}")
            continue

    return _empty_result(f"All tickers failed for {ticker} / {fallback_ticker}")


def fetch_all(cfg: dict) -> dict:
    instruments = cfg["instruments"]
    a_cfg = cfg["analysis"]
    out = {}
    for key, meta in instruments.items():
        log.info(f"Fetching {key} ({meta['yf_ticker']}) ...")
        out[key] = fetch_instrument_data(
            ticker=meta["yf_ticker"],
            fallback_ticker=meta.get("fallback_ticker"),
            interval=a_cfg["intraday_interval"],
            lookback_days=a_cfg["intraday_lookback_days"],
            daily_lookback_days=a_cfg["daily_lookback_days"],
        )
        if not out[key]["ok"]:
            log.error(f"{key} fetch failed: {out[key]['reason']}")
    return out
