"""
Minimal smoke tests - no network required. Confirms the report pipeline
never crashes even when every external data source fails.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config
from app import report_generator


def test_report_builds_with_all_sources_down():
    cfg = load_config()
    dead_fetch = {
        "NQ": {"ok": False, "reason": "test: simulated failure"},
        "DXY": {"ok": False, "reason": "test: simulated failure"},
        "GC": {"ok": False, "reason": "test: simulated failure"},
        "TNX": {"ok": False, "reason": "test: simulated failure"},
    }
    dead_macro = {"ok": False, "reason": "test: simulated failure"}

    report = report_generator.build_report("asia", cfg, dead_fetch, dead_macro, {})
    md = report_generator.render_markdown(report)

    assert report["final_stance"] in (
        "WAIT", "NEUTRAL / WAIT - mixed signals, let price confirm"
    )
    assert "UNKNOWN" in md or "unavailable" in md
    assert "FINAL STANCE" in md
    print("OK: report generation is resilient to total data-source failure.")


def test_config_loads():
    cfg = load_config()
    assert "instruments" in cfg
    assert set(cfg["instruments"].keys()) == {"NQ", "DXY", "GC", "TNX"}
    print("OK: config.yaml loads and has expected instruments.")


if __name__ == "__main__":
    test_config_loads()
    test_report_builds_with_all_sources_down()
    print("All smoke tests passed.")
