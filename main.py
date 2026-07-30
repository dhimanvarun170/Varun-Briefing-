#!/usr/bin/env python3
"""
CLI entry point.

Commands:
  python main.py generate asia
  python main.py generate ny
  python main.py latest
  python main.py history [--n 5]
  python main.py show --date YYYY-MM-DD --session asia|ny
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

from app.config import load_config, load_manual_overrides
from app.logger import get_logger
from app import data_fetch, macro_calendar, report_generator, storage, level_watch

log = get_logger("main")

SCHEDULED_REPORT_STATE_PATH = "data/scheduled_report_state.json"


def _load_scheduled_state() -> dict:
    import json
    if not os.path.exists(SCHEDULED_REPORT_STATE_PATH):
        return {}
    try:
        with open(SCHEDULED_REPORT_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_scheduled_state(state: dict):
    import json
    os.makedirs(os.path.dirname(SCHEDULED_REPORT_STATE_PATH), exist_ok=True)
    with open(SCHEDULED_REPORT_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def maybe_generate_scheduled_reports(cfg: dict):
    """
    Reliability fallback: GitHub's free-tier once-a-day scheduled
    triggers (asia-report.yml / ny-report.yml) can occasionally be
    delayed by hours under load. This piggybacks report generation
    onto the frequently-running (every 5 min) check-levels job instead,
    which has proven far more reliable - if it's the target IST window
    and today's report hasn't been generated yet, generate it now.
    """
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = dt.datetime.now(ist)
    today_str = now_ist.date().isoformat()
    state = _load_scheduled_state()
    if state.get("date") != today_str:
        state = {"date": today_str}

    is_weekday = now_ist.weekday() < 5

    in_asia_window = (now_ist.hour == 4 and 25 <= now_ist.minute <= 50)
    if is_weekday and in_asia_window and not state.get("asia_generated"):
        log.info("Scheduled-fallback: generating Asia report (piggybacked on check-levels) ...")
        try:
            cmd_generate("asia", cfg)
            state["asia_generated"] = True
        except Exception as e:
            log.error(f"Scheduled-fallback Asia generation failed: {e}")

    in_ny_window = (now_ist.hour == 17 and now_ist.minute >= 55) or \
                   (now_ist.hour == 18 and now_ist.minute <= 20)
    if is_weekday and in_ny_window and not state.get("ny_generated"):
        log.info("Scheduled-fallback: generating NY report (piggybacked on check-levels) ...")
        try:
            cmd_generate("ny", cfg)
            state["ny_generated"] = True
        except Exception as e:
            log.error(f"Scheduled-fallback NY generation failed: {e}")

    _save_scheduled_state(state)


def cmd_check_levels(cfg: dict):
    maybe_generate_scheduled_reports(cfg)
    log.info("Checking D/W/M levels across the full watchlist ...")
    events = level_watch.check_all_levels(cfg)
    is_heartbeat = level_watch.should_send_heartbeat()

    watchlist = None
    if events or is_heartbeat:
        watchlist = level_watch.analyze_full_watchlist(cfg)

    parts = []
    if events:
        parts.append(level_watch.format_alert_message(events))
    if watchlist is not None:
        parts.append(level_watch.format_proximity_summary(watchlist))
        if is_heartbeat:
            level_watch.mark_heartbeat_sent()

    if not parts:
        print("No new touches this check, not due for a heartbeat - nothing to send.")
        return

    msg = "\n\n".join(parts)
    print(msg)

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if bot_token and chat_id:
        import requests
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                timeout=10,
            )
            if not resp.ok:
                log.error(f"Telegram send failed: {resp.status_code} {resp.text}")
        except Exception as e:
            log.error(f"Telegram send error: {e}")
    else:
        log.info("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set - printed summary only, no Telegram send.")


def cmd_generate(session: str, cfg: dict):
    log.info(f"Generating {session} report ...")
    overrides = load_manual_overrides(cfg)
    if not overrides:
        log.info("No manual gamma/options overrides found - proceeding price-action-only "
                  "(this is expected/normal, not an error).")

    try:
        fetch_data = data_fetch.fetch_all(cfg)
    except Exception as e:
        log.error(f"Unexpected error during market data fetch: {e}")
        fetch_data = {}

    try:
        macro_data = macro_calendar.fetch_macro_events(cfg)
    except Exception as e:
        log.error(f"Unexpected error during macro fetch: {e}")
        macro_data = {"ok": False, "reason": str(e), "events": []}

    report = report_generator.build_report(session, cfg, fetch_data, macro_data, overrides)
    markdown = report_generator.render_markdown(report)

    top_setups = []
    try:
        watchlist = level_watch.analyze_full_watchlist(cfg)
        for inst in watchlist:
            if inst.get("ok"):
                inst["headline"] = level_watch.fetch_headline(inst["ticker_used"])
        top_setups = level_watch.rank_top_setups(watchlist, top_n=2)
        watchlist_md = report_generator.render_full_watchlist_markdown(watchlist, top_setups)
        headline_lines = "\n".join(
            f"- {w['label']}: {w.get('headline', 'N/A')}" for w in watchlist if w.get("ok")
        )
        markdown += "\n" + watchlist_md + "\n## Today's Headlines\n" + headline_lines + "\n"
        report["full_watchlist"] = watchlist
        report["top_setups"] = top_setups
    except Exception as e:
        log.error(f"Full watchlist section failed - core report unaffected: {e}")

    reports_dir = cfg["output"]["reports_dir"]
    md_path, json_path = storage.save_report(report, markdown, session, reports_dir)

    print(markdown)
    print(f"\n[saved] {md_path}")
    print(f"[saved] {json_path}")

    date_str = dt.datetime.now(dt.timezone.utc).date().isoformat()
    repo_slug = os.environ.get("GITHUB_REPOSITORY", "")
    repo_url = f"https://github.com/{repo_slug}" if repo_slug else ""
    summary = report_generator.render_telegram_summary(report, repo_url=repo_url, date_str=date_str)
    if top_setups:
        summary += "\n\nTop setups:\n"
        for s in top_setups:
            sign = "+" if s["distance"] >= 0 else ""
            summary += (f"- {s['label']}: {s['bias'].upper()} -> {s['target_level']} "
                        f"({sign}{s['distance']:.2f} away)\n")
    summary_path = os.path.join(reports_dir, "latest_telegram_summary.txt")
    with open(summary_path, "w") as f:
        f.write(summary)
    print(f"[saved] {summary_path}")


def cmd_latest(cfg: dict):
    reports_dir = cfg["output"]["reports_dir"]
    path = storage.find_latest_report(reports_dir)
    if not path:
        print("No reports found yet. Run `python main.py generate asia` or `ny` first.")
        return
    with open(path) as f:
        print(f.read())


def cmd_history(cfg: dict, n: int):
    reports_dir = cfg["output"]["reports_dir"]
    paths = storage.find_last_n_reports(reports_dir, n)
    if not paths:
        print("No reports found yet.")
        return
    for p in paths:
        print(f"\n{'=' * 70}\n{p}\n{'=' * 70}")
        with open(p) as f:
            print(f.read())


def cmd_show(cfg: dict, date: str, session: str):
    reports_dir = cfg["output"]["reports_dir"]
    md = storage.load_report_markdown(reports_dir, date, session)
    if md is None:
        print(f"No report found for {date} / {session}. "
              f"Expected file: {reports_dir}/{date}-{session}.md")
        return
    print(md)


def main():
    parser = argparse.ArgumentParser(description="Free automated NQ/DXY/Gold pre-session briefing system")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="Generate a report for a session")
    p_gen.add_argument("session", choices=["asia", "ny"])

    sub.add_parser("latest", help="Show the most recently generated report")

    p_hist = sub.add_parser("history", help="Show the last N reports")
    p_hist.add_argument("--n", type=int, default=5)

    p_show = sub.add_parser("show", help="Show a specific saved report")
    p_show.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_show.add_argument("--session", required=True, choices=["asia", "ny"])

    sub.add_parser("check-levels", help="Check D/W/M OHLC levels and alert on touch")

    args = parser.parse_args()
    cfg = load_config()

    if args.command == "generate":
        cmd_generate(args.session, cfg)
    elif args.command == "latest":
        cmd_latest(cfg)
    elif args.command == "history":
        cmd_history(cfg, args.n)
    elif args.command == "show":
        cmd_show(cfg, args.date, args.session)
    elif args.command == "check-levels":
        cmd_check_levels(cfg)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
