"""
Save/load reports as markdown + json under reports/YYYY-MM-DD-{session}.{md,json}
Also provides read helpers used by the CLI query commands
(latest / history / show today's asia / show today's ny).
"""
from __future__ import annotations
import json
import os
import glob
import datetime as dt
from app.logger import get_logger

log = get_logger(__name__)


def _date_str(d=None) -> str:
    d = d or dt.datetime.now(dt.timezone.utc).date()
    return d.isoformat()


def save_report(report: dict, markdown: str, session: str, reports_dir: str,
                 date_str: str = None) -> tuple:
    date_str = date_str or _date_str()
    os.makedirs(reports_dir, exist_ok=True)

    md_path = os.path.join(reports_dir, f"{date_str}-{session}.md")
    json_path = os.path.join(reports_dir, f"{date_str}-{session}.json")

    with open(md_path, "w") as f:
        f.write(markdown)

    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    log.info(f"Saved report: {md_path}, {json_path}")
    return md_path, json_path


def get_report_path(reports_dir: str, date_str: str, session: str, ext: str) -> str:
    return os.path.join(reports_dir, f"{date_str}-{session}.{ext}")


def load_report_markdown(reports_dir: str, date_str: str, session: str):
    path = get_report_path(reports_dir, date_str, session, "md")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def load_report_json(reports_dir: str, date_str: str, session: str):
    path = get_report_path(reports_dir, date_str, session, "json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def find_latest_report(reports_dir: str):
    files = glob.glob(os.path.join(reports_dir, "*.md"))
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def find_last_n_reports(reports_dir: str, n: int = 5) -> list:
    files = glob.glob(os.path.join(reports_dir, "*.md"))
    files.sort(key=os.path.getmtime, reverse=True)
    return files[:n]
