"""Dedicated configuration, resolved against this worktree, never NFL runtime."""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configuration():
    path = Path(os.environ.get("FANTASY_CONFIG", ROOT / "configs/fantasy.toml"))
    cfg = tomllib.loads(path.read_text())
    cache = (ROOT / cfg["cache_dir"]).resolve()
    if not cache.is_relative_to(ROOT) or cache == ROOT:
        raise ValueError("Fantasy cache must be a child of this worktree")
    if cfg["bind"] != "127.0.0.1":
        raise ValueError("Fantasy preview must use loopback")
    cfg["cache"] = cache
    return cfg
