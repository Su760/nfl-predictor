"""Retrospective scoring and a fixed, outcome-free context lookup predictor."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
import hashlib
import json
import tomllib

from fantasy.config import ROOT
from fantasy.quality import COMPONENTS
from fantasy.usage import POSITIONS, number


def settings():
    return tomllib.loads((ROOT / "configs/fantasy_points.toml").read_text())


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def score(components, scoring, cfg=None):
    cfg = cfg or settings()
    if scoring not in cfg["reception_points"]:
        raise ValueError("Unsupported scoring format")
    values = {k: number(components.get(k)) for k in COMPONENTS}
    if any(v is None for v in values.values()):
        return None
    if any(v < 0 or v != int(v) for k, v in values.items() if not k.endswith("yards")):
        return None
    return (cfg["yard_points"] * (values["rushing_yards"] + values["receiving_yards"])
            + cfg["touchdown_points"] * (values["rushing_tds"] + values["receiving_tds"])
            + cfg["reception_points"][scoring] * values["receptions"])


def context_key(context, cfg):
    """Read only the four preregistered predictors, never outcome columns."""
    pos, kind = context.get("position"), context.get("kind")
    yard, depth = number(context.get("yardline")), number(context.get("depth"))
    if pos not in POSITIONS or kind not in {"target", "carry"} or yard is None or not 0 <= yard <= 100:
        return None
    if kind == "target" and depth is None:
        return None
    parent = pos + ":" + kind
    return parent, parent + ":" + str(bisect_left(cfg["field_edges"], yard)) + ":" + (
        str(bisect_right(cfg["depth_edges"], depth)) if kind == "target" else "rush")


def fit(records, cfg=None):
    cfg = cfg or settings()
    if not records or any(r["season"] not in cfg["train_seasons"] or r["season"] >= 2026 for r in records):
        raise ValueError("Only the fixed training seasons may be fitted")
    groups = defaultdict(list)
    for row in records:
        for event in row["events"]:
            key = context_key(event["context"], cfg)
            if key is None or event["components"] is None:
                raise ValueError("Incomplete training opportunity")
            for group in key:
                groups[group].append(event["components"])
    cells = {}
    for key, samples in groups.items():
        cells[key] = {"n": len(samples), "means": {
            scoring: sum(score(s, scoring, cfg) for s in samples) / len(samples)
            for scoring in cfg["reception_points"]}}
    return {"config": cfg, "config_sha256": fingerprint(cfg), "cells": cells,
            "train_seasons": sorted({r["season"] for r in records})}


def predict(model, contexts, scoring, *, baseline=False):
    cfg, cells = model["config"], model["cells"]
    total = 0.0
    for context in contexts:
        key = context_key(context, cfg)
        if key is None or key[0] not in cells:
            return None
        parent, child = cells[key[0]], cells.get(key[1])
        mean = parent["means"][scoring]
        if child is not None and not baseline:
            n, prior = child["n"], cfg["prior_opportunities"]
            mean = (n * child["means"][scoring] + prior * mean) / (n + prior)
        total += mean
    return total


def total_metric(values, *, blocked=False):
    covered = sum(number(v) is not None for v in values)
    return {"value": sum(values) if values and covered == len(values) and not blocked else None,
            "covered_games": covered, "expected_games": len(values)}


def depth_metric(rows, *, blocked=False):
    metrics = [r.get("metrics", {}) for r in rows]
    sums = total_metric([m.get("depth_sum") for m in metrics], blocked=blocked)
    counts = total_metric([m.get("targets") for m in metrics])
    known = sum(m.get("depth_count") or 0 for m in metrics)  # Coverage count only, never a value.
    sums.update(covered_targets=known, expected_targets=counts["value"])
    sums["value"] = (sums["value"] / counts["value"]
                     if sums["value"] is not None and counts["value"] else None)
    return sums


def summarize_quality(player, window, model=None):
    rows = [g["usage"].get("quality", {}) for g in player["games"]]
    blocked = player["transfer"] and window == "season"
    from fantasy.quality import DEFINITIONS
    metrics = {k: total_metric([r.get("metrics", {}).get(k) for r in rows], blocked=blocked)
               for k in DEFINITIONS if k != "target_depth"}
    metrics["target_depth"] = depth_metric(rows, blocked=blocked)
    points = {}
    for scoring in settings()["reception_points"]:
        actual = total_metric([score(r.get("components", {}), scoring) for r in rows], blocked=blocked)
        expected = total_metric([
            predict(model, r["contexts"], scoring) if model and r.get("reason") is None
            and "contexts" in r else None for r in rows], blocked=blocked)
        delta = actual["value"] - expected["value"] if all(
            v["value"] is not None for v in [actual, expected]) else None
        points[scoring] = {"actual": actual, "expected": expected, "difference": delta}
    return {"metrics": metrics, "points": points,
            "games": [{"game_id": g["game_id"], "week": g["week"], "date": g["date"],
                       "reason": r.get("reason", "quality_not_captured"),
                       "targets": r.get("metrics", {}).get("targets"),
                       "carries": r.get("metrics", {}).get("carries")}
                      for g, r in zip(player["games"], rows)]}
