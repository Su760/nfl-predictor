"""Manual, isolated chronological research. Holdout requires a sealed validation run."""
import argparse
from collections import defaultdict
from datetime import UTC, datetime
import hashlib
import json
from math import sqrt
import random

from fantasy.config import ROOT, configuration
from fantasy.points import fingerprint, fit, predict, score, settings
from fantasy.quality import COMPONENTS, EXCLUSIONS, audit_player
from fantasy.sources import REQUIRED, atomic_json, fetch_source, unique_index
from fantasy.usage import POSITIONS

def method_hash():
    return fingerprint({p: hashlib.sha256((ROOT / "fantasy" / p).read_bytes()).hexdigest()
                        for p in ["quality.py", "points.py", "evaluate.py"]})


def season_records(season, cfg):
    history = {**cfg, "cache": cfg["cache"] / "history"}
    columns = {
        "players": REQUIRED["players"] | set(COMPONENTS) | {"week"},
        "pbp": REQUIRED["pbp"] | {"complete_pass", "receiving_yards", "rushing_yards",
                                   "pass_touchdown", "rush_touchdown", "td_player_id", "air_yards"},
    }
    sources, receipts = {}, {}
    for name in ["players", "pbp"]:
        url = cfg["sources"][name].format(season=season)
        # Explicit immutable receipt reuse, never masquerading as a fresh retrieval.
        matches = []
        for path in (history["cache"] / "receipts").glob(name + "-*.json"):
            receipt = json.loads(path.read_text())
            if receipt["url"] == url:
                matches.append(receipt)
        if matches:
            import pyarrow.parquet as pq
            receipt = sorted(matches, key=lambda r: r["captured_at"])[-1]
            raw = history["cache"] / "raw" / receipt["sha256"]
            if hashlib.sha256(raw.read_bytes()).hexdigest() != receipt["sha256"]:
                raise ValueError("Historical source hash mismatch")
            schema = set(pq.read_schema(raw).names)
            rows = pq.read_table(raw, columns=sorted(schema.intersection(columns[name]))).to_pylist()
        else:
            rows, receipt = fetch_source(name, url, history, columns=columns[name])
        sources[name], receipts[name] = rows, receipt
    players = [p for p in sources["players"] if p["season"] == season
               and p["season_type"] == "REG" and p["position"] in POSITIONS]
    unique_index(players, ("player_id", "game_id"))
    games = defaultdict(list)
    for play in sources["pbp"]:
        games[play["game_id"]].append(play)
    records, coverage = [], {}
    for pos in ["ALL", *sorted(POSITIONS)]:
        coverage[pos] = {"total": 0, "eligible": 0, "excluded": {}}
    for player in players:
        audit = audit_player(player, games[player["game_id"]])
        for pos in ["ALL", player["position"]]:
            c = coverage[pos]
            c["total"] += 1
            if audit["reason"] is None:
                c["eligible"] += 1
            else:
                reason = audit["reason"]
                c["excluded"][reason] = c["excluded"].get(reason, 0) + 1
        if audit["reason"] is None:
            records.append({"season": season, "game_id": player["game_id"],
                            "player_id": player["player_id"], "position": player["position"],
                            "events": audit["events"], "components": audit["components"]})
    print(f"{season}: {coverage['ALL']}", flush=True)
    return records, coverage, receipts


def metrics(rows):
    if not rows or any(any(r.get(k) is None for k in ["actual", "context", "baseline"]) for r in rows):
        raise ValueError("Both models require identical nonempty paired samples")
    result = {"n": len(rows), "games": len({r["game_id"] for r in rows})}
    for model in ["context", "baseline"]:
        errors = [r[model] - r["actual"] for r in rows]
        result[model] = {"mae": sum(abs(x) for x in errors) / len(rows),
                         "rmse": sqrt(sum(x*x for x in errors) / len(rows)),
                         "bias": sum(errors) / len(rows)}
    return result


def uncertainty(rows, cfg):
    groups = defaultdict(list)
    for r in rows:
        groups[r["game_id"]].append((r["baseline"]-r["actual"])**2 - (r["context"]-r["actual"])**2)
    clusters = [(sum(v), len(v)) for v in groups.values()]
    rng, values = random.Random(cfg["bootstrap_seed"]), []
    for _ in range(cfg["bootstrap_samples"]):
        draw = rng.choices(clusters, k=len(clusters))
        values.append(sum(s for s, n in draw) / sum(n for s, n in draw))
    values.sort()
    return {"measure": "baseline MSE minus context MSE; positive favors context",
            "lower_95": values[int(.025*len(values))], "upper_95": values[int(.975*len(values))]}


def evaluate(records, model, coverage):
    report = {"coverage": coverage, "scores": {}, "uncertainty": {}}
    for scoring in model["config"]["reception_points"]:
        paired = [{"game_id": r["game_id"], "position": r["position"],
                   "actual": score(r["components"], scoring, model["config"]),
                   **{name: predict(model, [e["context"] for e in r["events"]], scoring,
                                    baseline=name == "baseline") for name in ["context", "baseline"]}}
                  for r in records]
        report["scores"][scoring] = {pos: metrics([r for r in paired if pos == "ALL" or r["position"] == pos])
                                      for pos in ["ALL", *sorted(POSITIONS)]}
        report["uncertainty"][scoring] = uncertainty(paired, model["config"])
    report["gate_failures"] = gate(report, model["config"])
    return report


def gate(report, cfg):
    failures = []
    for season, positions in report["coverage"].items():
        c = positions["ALL"]
        if not c["total"] or c["eligible"] / c["total"] < cfg["minimum_coverage"]:
            failures.append(f"{season} coverage below {cfg['minimum_coverage']}")
    for scoring, positions in report["scores"].items():
        a, b = positions["ALL"]["context"], positions["ALL"]["baseline"]
        if a["rmse"] > b["rmse"] * (1-cfg["minimum_rmse_improvement"]):
            failures.append(scoring + " RMSE improvement below gate")
        if a["mae"] > b["mae"] * (1+cfg["maximum_mae_regression"]):
            failures.append(scoring + " MAE regression")
        for pos in sorted(POSITIONS):
            a, b = positions[pos]["context"], positions[pos]["baseline"]
            if a["rmse"] > b["rmse"] * (1+cfg["maximum_position_rmse_regression"]):
                failures.append(scoring + " " + pos + " RMSE regression")
    return failures


def run(stage):
    cfg, policy = configuration(), settings()
    root = cfg["cache"] / "expected-points"
    root.mkdir(parents=True, exist_ok=True)
    if stage == "validate":
        if (root / "freeze.json").exists():
            raise ValueError("Validation already sealed; no implicit refit or overwrite")
        if max(policy["train_seasons"]) >= min(policy["validation_seasons"]) or max(
                policy["validation_seasons"]) >= min(policy["holdout_seasons"]) or max(policy["holdout_seasons"]) >= 2026:
            raise ValueError("Chronological split invalid")
        train, validation, coverage, receipts = [], [], {}, {}
        for season in policy["train_seasons"] + policy["validation_seasons"]:
            rows, cov, rec = season_records(season, cfg)
            (train if season in policy["train_seasons"] else validation).extend(rows)
            coverage[str(season)], receipts[str(season)] = cov, rec
        model = fit(train, policy)
        report = evaluate(validation, model, {str(y): coverage[str(y)] for y in policy["validation_seasons"]})
        atomic_json(root / "model.json", model)
        atomic_json(root / "validation.json", report)
        freeze = {"frozen_at": datetime.now(UTC).isoformat(), "policy": policy,
                  "method_sha256": method_hash(), "model_sha256": fingerprint(model),
                  "validation_sha256": fingerprint(report), "coverage": coverage,
                  "receipts": receipts, "exclusion_priority": EXCLUSIONS,
                  "baseline": "position-specific mean supported points per target/carry, same training rows",
                  "decision": "Fixed method retained without tuning; final holdout will test the same gates"}
        atomic_json(root / "freeze.json", freeze)
        print(json.dumps({"validation": report["scores"], "gate_failures": report["gate_failures"],
                          "frozen_at": freeze["frozen_at"]}, indent=2))
    elif stage == "holdout":
        if (root / "holdout.json").exists():
            raise ValueError("Final holdout already evaluated; no overwrite")
        freeze = json.loads((root / "freeze.json").read_text())
        model = json.loads((root / "model.json").read_text())
        validation = json.loads((root / "validation.json").read_text())
        if (freeze["method_sha256"] != method_hash() or freeze["model_sha256"] != fingerprint(model)
                or freeze["validation_sha256"] != fingerprint(validation) or policy != freeze["policy"]):
            raise ValueError("Sealed method, policy or model changed before holdout")
        records, coverage, receipts = [], {}, {}
        for season in policy["holdout_seasons"]:
            rows, cov, rec = season_records(season, cfg)
            records.extend(rows)
            coverage[str(season)], receipts[str(season)] = cov, rec
        report = evaluate(records, model, coverage)
        report.update(evaluated_at=datetime.now(UTC).isoformat(), receipts=receipts,
                      model_sha256=freeze["model_sha256"], frozen_at=freeze["frozen_at"])
        report["display_supported"] = not validation["gate_failures"] and not report["gate_failures"]
        atomic_json(root / "holdout.json", report)
        print(json.dumps({"holdout": report["scores"], "gate_failures": report["gate_failures"],
                          "display_supported": report["display_supported"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["validate", "holdout"])
    run(parser.parse_args().stage)
