"""
Loads config.yaml and optional manual gamma/options overrides.
"""
import json
import os
import yaml

_CONFIG_CACHE = None


def load_config(path: str = "config.yaml") -> dict:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    with open(path, "r") as f:
        _CONFIG_CACHE = yaml.safe_load(f)
    return _CONFIG_CACHE


def load_manual_overrides(cfg: dict) -> dict:
    """
    Optional manual input for options/gamma data that has no free API:
    call_wall, put_wall, gamma_flip, net_gex, max_pain, top_oi (per NQ/GC).
    Returns {} if the file doesn't exist or is invalid - callers must
    handle the empty case and fall back to price-action-only analysis.
    """
    path = cfg.get("manual_overrides_path", "data/manual_overrides.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}
