"""Private continuous season worker; real-clock immutable forecast revisions."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

from week1_live import (
    CODE_ROOT,
    build_model,
    canonical,
    digest,
    now,
    replace_view,
    stamp,
    write_once,
)

from nfl_predictor.models.tie import to_three_way
from nfl_predictor.ratings.base import CompletedGame


def configuration(path=None):
    cfg = tomllib.loads(Path(path or CODE_ROOT / "configs/season_live.toml").read_text())
    if cfg["zero_dollar_mode"] is not True or cfg["allow_paid_usage"] is not False:
        raise ValueError("ZERO_DOLLAR_GUARD")
    root = Path(os.environ.get("NFL_SEASON_DATA_DIR", cfg["data_root"])).expanduser().resolve()
    if (
        root == CODE_ROOT
        or CODE_ROOT in root.parents
        or any((p / ".git").exists() for p in (root, *root.parents))
    ):
        raise ValueError("PRIVATE_ROOT_REQUIRED")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return cfg, root


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def code_identity():
    names = [
        "ops/season_live.py",
        "ops/season_sources.py",
        "ops/season_scoring.py",
        "ops/season_analysis.py",
        "configs/season_live.toml",
        "configs/model_policy_v1.toml",
        "src/nfl_predictor/ratings/elo.py",
        "src/nfl_predictor/models/tie.py",
    ]
    return {
        "code_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=CODE_ROOT, text=True
        ).strip(),
        "code_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=all"], cwd=CODE_ROOT, text=True
            ).strip()
        ),
        "source_sha256": digest(
            canonical({n: digest((CODE_ROOT / n).read_bytes()) for n in names})
        ),
        "lock_sha256": digest((CODE_ROOT / "uv.lock").read_bytes()),
    }


def freeze_policy(root, clock):
    path = root / "scoring-policy.json"
    if not path.exists():
        write_once(
            path,
            {
                "version": "season-scoring-v1",
                "effective_at": stamp(clock),
                "official": "latest valid pregame production-role revision",
                "horizons": ["LATEST", "T72", "T60", "FINAL"],
                "ties": "exclude from winner accuracy; include in three-way Brier/log loss",
                "schedule_changes": "current schedule version only; retain all history",
                "corrections": "latest observed official final result; retractions unresolved",
                "calibration_bin_count": 10,
                "promotion": "manual review only; no automatic production rewrite",
            },
        )
    return read(path)


def journal(root, namespace, value):
    key = digest(canonical(value))
    write_once(root / namespace / (key + ".json"), value)
    return key


def recovered_history(root, namespace, game_id):
    values = []
    for path in (root / namespace).glob("*.json"):
        value = read(path)
        if digest(canonical(value)) != path.stem:
            raise ValueError("JOURNAL_HASH_MISMATCH")
        if value.get("game_id") == game_id:
            values.append(value)
    return sorted(values, key=lambda x: (x["observed_at"], x.get("version", 0)))


def all_records(root, game_id):
    values = []
    directory = root / "forecasts" / game_id
    for evidence in sorted(directory.glob("*.evidence.json")):
        proof = read(evidence)
        expected_fields = {
            "record_sha256",
            "receipt_sha256",
            "published_at",
            "receipt_durable_at",
            "deadline",
            "proof_sha256",
        }
        if not isinstance(proof, dict) or set(proof) != expected_fields:
            raise ValueError("INVALID_PUBLICATION_EVIDENCE")
        if any(
            not re.fullmatch("[0-9a-f]{64}", str(proof[k]))
            for k in ("record_sha256", "receipt_sha256", "proof_sha256")
        ):
            raise ValueError("INVALID_PUBLICATION_EVIDENCE")
        if evidence.name != proof["record_sha256"] + ".evidence.json" or proof[
            "proof_sha256"
        ] != digest(canonical({k: v for k, v in proof.items() if k != "proof_sha256"})):
            raise ValueError("PUBLICATION_EVIDENCE_HASH_MISMATCH")
        record = read(directory / (proof["record_sha256"] + ".json"))
        if not record or digest(canonical(record)) != proof["record_sha256"]:
            raise ValueError("FORECAST_HASH_MISMATCH")
        receipt = read(directory / (proof["record_sha256"] + ".receipt.json"))
        if digest(canonical(receipt)) != proof["receipt_sha256"]:
            raise ValueError("RECEIPT_HASH_MISMATCH")
        deadline = datetime.fromisoformat(proof["deadline"])
        if datetime.fromisoformat(proof["published_at"]) < datetime.fromisoformat(
            record["generated_at"]
        ) or datetime.fromisoformat(proof["receipt_durable_at"]) < datetime.fromisoformat(
            proof["published_at"]
        ):
            raise ValueError("INVALID_PUBLICATION_ORDER")
        if (
            max(
                datetime.fromisoformat(record["generated_at"]),
                datetime.fromisoformat(proof["published_at"]),
                datetime.fromisoformat(proof["receipt_durable_at"]),
            )
            >= deadline
        ):
            raise ValueError("POST_DEADLINE_RECORD_REJECTED")
        values.append(
            {**record, "published_at": proof["published_at"], "revision_id": proof["record_sha256"]}
        )
    return values


def publish(root, record, deadline, clock=now):
    """A candidate is not a forecast until its receipt and original durability evidence exist."""
    if clock() >= deadline or datetime.fromisoformat(record["generated_at"]) >= deadline:
        return False
    key = digest(canonical(record))
    directory = root / "forecasts" / record["game_id"]
    write_once(directory / (key + ".json"), record)
    if clock() >= deadline:
        return False
    receipt = {"record_sha256": key, "generated_at": record["generated_at"]}
    write_once(directory / (key + ".receipt.json"), receipt)
    durable = clock()
    if durable >= deadline or durable < datetime.fromisoformat(record["generated_at"]):
        return False
    proof = {
        "record_sha256": key,
        "receipt_sha256": digest(canonical(receipt)),
        "published_at": stamp(durable),
        "receipt_durable_at": stamp(durable),
        "deadline": stamp(deadline),
    }
    proof["proof_sha256"] = digest(canonical(proof))
    write_once(directory / (key + ".evidence.json"), proof)
    return True


def legacy_records(cfg, game):
    root = Path(cfg["legacy_data_root"]).expanduser()
    records = []
    for path in sorted((root / "predictions" / game["game_id"]).glob("*.json")):
        r = read(path)
        if (
            r["game_id"] != game["game_id"]
            or r["home"] != game["home"]
            or r["away"] != game["away"]
        ):
            raise ValueError("LEGACY_IDENTITY_MISMATCH")
        if datetime.fromisoformat(r["generated_at"]) >= datetime.fromisoformat(r["kickoff"]):
            raise ValueError("INVALID_LEGACY_PREGAME")
        records.append(
            {
                **r,
                "revision_id": "legacy-" + digest(path.read_bytes()),
                "model_version": "elo-week1-original",
                "role": "fallback",
                "provenance": "Original local pregame record; imported unchanged",
                "inputs": {
                    "legacy": {
                        "status": "LIMITED",
                        "captured_at": r["capture"]["captured_at"],
                        "reason": "No injury/QB/weather inputs in original forecast",
                    }
                },
            }
        )
    return records


def origin_state(kick, origin, clock, cfg):
    target = kick - timedelta(seconds=cfg["origin_seconds"][origin])
    start = (
        target - timedelta(seconds=cfg["origin_window_seconds"]) if origin != "FINAL" else target
    )
    end = min(kick, target + timedelta(seconds=cfg["origin_window_seconds"]))
    return ("MISSED" if clock >= end else "SCHEDULED" if clock < start else "DUE"), target, end


def current_records(game):
    return [
        r
        for r in game["predictions"]
        if game["kickoff"]
        and datetime.fromisoformat(r["kickoff"]) == datetime.fromisoformat(game["kickoff"])
        and (not r.get("schedule_version") or r["schedule_version"] == game["schedule_version"])
    ]


def model_for_current_results(rows, cfg, games, clock):
    rater, proof = build_model(rows, cfg)
    accepted = []
    for game in sorted(games, key=lambda g: g.get("kickoff") or ""):
        outcomes = game.get("outcomes", [])
        if not outcomes or outcomes[-1]["status"] != "FINAL":
            continue
        o = outcomes[-1]
        known = datetime.fromisoformat(o["observed_at"])
        if known > clock or not game["kickoff"] or datetime.fromisoformat(game["kickoff"]) >= clock:
            continue
        rater.update(
            CompletedGame(
                game["game_id"],
                cfg["season"],
                game["home"],
                game["away"],
                o["home_score"],
                o["away_score"],
                game["neutral_site"],
                known,
            )
        )
        accepted.append(
            {
                "game_id": game["game_id"],
                "outcome_version": o["version"],
                "home_score": o["home_score"],
                "away_score": o["away_score"],
            }
        )
    proof.update(
        ratings=dict(rater.ratings),
        current_final_results=accepted,
        model_version=cfg["model_version"],
        model=cfg["model_label"],
    )
    proof["model_state_sha256"] = digest(
        canonical(
            {
                "ratings": rater.ratings,
                "p_tie": proof["p_tie"],
                "policy": proof["policy_sha256"],
                "results": accepted,
            }
        )
    )
    return rater, proof


def preserve_schedule_identity(game, prior):
    """Retain a recorded identity across provider formatting/schema migrations."""
    if not prior.get("schedule_version") or not game.get("kickoff") or not prior.get("kickoff"):
        return
    old_venue, new_venue = prior.get("venue") or {}, game.get("venue") or {}
    same_venue = (str(old_venue["id"]) == str(new_venue["id"])) if old_venue.get("id") and new_venue.get("id") else old_venue == new_venue
    if (same_venue and datetime.fromisoformat(game["kickoff"]) == datetime.fromisoformat(prior["kickoff"])
        and all(game.get(k) == prior.get(k) for k in ("home", "away", "neutral_site"))):
        game["schedule_version"] = prior["schedule_version"]


def material(inputs):
    return {
        k: {
            f: v
            for f, v in x.items()
            if f
            not in (
                "captured_at",
                "last_successful_check",
                "raw_sha256",
                "source_updated_at",
                "age_seconds",
            )
        }
        for k, x in inputs.items()
    }


def next_check(games, clock, cfg):
    candidates = [clock + timedelta(seconds=cfg["check_seconds"])]
    for game in games:
        if not game["kickoff"]:
            continue
        kick = datetime.fromisoformat(game["kickoff"])
        if clock < kick < clock + timedelta(seconds=cfg["near_game_seconds"]):
            candidates.append(clock + timedelta(seconds=cfg["near_game_check_seconds"]))
        if game["status"] not in (
            "STATUS_FINAL",
            "STATUS_CANCELED",
        ) and kick <= clock < kick + timedelta(hours=cfg["result_poll_hours"]):
            candidates.append(clock + timedelta(seconds=cfg["near_game_check_seconds"]))
        for origin in cfg["origin_seconds"]:
            target = kick - timedelta(seconds=cfg["origin_seconds"][origin])
            if target > clock:
                candidates.append(target)
    return stamp(min(candidates))


def run_once(cfg, root, *, clock=now, fetcher=None):
    from season_scoring import score_season
    from season_sources import fetch_sources

    fetcher = fetcher or fetch_sources
    with (root / "worker.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "ALREADY_RUNNING"}
        policy = freeze_policy(root, clock())
        previous = read(root / "view.json", {})
        source = fetcher(cfg, root, clock)
        checked = clock()
        for required in ("nflverse_history", "schedule"):
            check = source["checks"][required]
            captured = datetime.fromisoformat(check["captured_at"])
            age = (checked - captured).total_seconds()
            if check["status"] != "AVAILABLE" or not 0 <= age <= cfg["maximum_capture_age_seconds"]:
                raise ValueError("REQUIRED_INPUT_STALE_OR_FAILED: " + required)
        old = {g["game_id"]: g for g in previous.get("games", [])}
        for path in sorted((root / "schedules").glob("*.json")):
            version = read(path)
            if digest(canonical(version)) != path.stem:
                raise ValueError("JOURNAL_HASH_MISMATCH")
            event_id = version["game_id"]
            if event_id not in old:
                history = recovered_history(root, "schedules", event_id)
                latest = history[-1]
                old[event_id] = {
                    **latest,
                    "season": cfg["season"],
                    "schedule_history": history,
                    "outcomes": recovered_history(root, "outcomes", event_id),
                    "status": "SOURCE_EVENT_MISSING",
                    "neutral_site": latest.get("neutral_site", False),
                }
                old[event_id]["predictions"] = legacy_records(cfg, old[event_id]) + all_records(
                    root, event_id
                )
        games = source["games"]
        for game in games:
            prior = old.get(game["game_id"], {})
            history = recovered_history(root, "schedules", game["game_id"]) or list(
                prior.get("schedule_history", [])
            )
            preserve_schedule_identity(game, history[-1] if history else prior)
            if not history or history[-1]["schedule_version"] != game["schedule_version"]:
                version = {
                    k: game.get(k)
                    for k in (
                        "game_id",
                        "schedule_version",
                        "kickoff",
                        "venue",
                        "home",
                        "away",
                        "week",
                        "status",
                        "neutral_site",
                        "season",
                    )
                }
                version["observed_at"] = stamp(checked)
                journal(root, "schedules", version)
                history.append(version)
            game["schedule_history"] = history
            outcomes = recovered_history(root, "outcomes", game["game_id"]) or list(
                prior.get("outcomes", [])
            )
            candidate = game.pop("result", None)
            if candidate is None and outcomes and outcomes[-1]["status"] == "FINAL":
                candidate = {
                    "status": "UNRESOLVED",
                    "home_score": None,
                    "away_score": None,
                    "source": "result source no longer confirms final",
                }
            if candidate is not None:
                if candidate["status"] == "FINAL" and (
                    not game["kickoff"] or datetime.fromisoformat(game["kickoff"]) >= checked
                ):
                    raise ValueError("FINAL_RESULT_BEFORE_KICKOFF")
                key = {k: candidate.get(k) for k in ("status", "home_score", "away_score")}
                oldkey = {k: outcomes[-1].get(k) for k in key} if outcomes else None
                if key != oldkey:
                    o = {
                        **candidate,
                        "game_id": game["game_id"],
                        "observed_at": stamp(checked),
                        "version": len(outcomes) + 1,
                        "supersedes": outcomes[-1]["version"] if outcomes else None,
                    }
                    journal(root, "outcomes", o)
                    outcomes.append(o)
            game["outcomes"] = outcomes
            game["predictions"] = legacy_records(cfg, game) + all_records(root, game["game_id"])
        # Missing source events cannot silently disappear from the denominator.
        for event_id, prior in old.items():
            if event_id not in {g["game_id"] for g in games}:
                games.append(
                    {
                        **prior,
                        "status": "SOURCE_EVENT_MISSING",
                        "inputs": {"schedule": {"status": "MISSING"}},
                    }
                )
        rater, model = model_for_current_results(source["rows"], cfg, games, checked)
        if cfg.get("analysis_enabled"):
            policy_bytes = (CODE_ROOT / cfg["model_policy"]).read_bytes()
            if digest(policy_bytes) != model["policy_sha256"]:
                raise ValueError("EXPLANATION_MODEL_POLICY_HASH_MISMATCH")
            model["elo_policy"] = tomllib.loads(policy_bytes.decode())["elo"]
        identity = code_identity()
        journal(
            root,
            "source-versions",
            {
                "identity": identity,
                "files": {
                    name: (CODE_ROOT / name).read_text()
                    for name in (
                        "ops/season_live.py",
                        "ops/season_sources.py",
                        "ops/season_scoring.py",
                        "ops/season_analysis.py",
                        "configs/season_live.toml",
                        "configs/model_policy_v1.toml",
                        "src/nfl_predictor/ratings/elo.py",
                        "src/nfl_predictor/models/tie.py",
                    )
                },
            },
        )
        model_id = journal(root, "models", {**model, **identity})
        for game in games:
            game["origins"] = {o: "BLOCKED" for o in cfg["origin_seconds"]}
            if not game["kickoff"]:
                game["forecast_status"] = "KICKOFF_TBD"
                continue
            kick = datetime.fromisoformat(game["kickoff"])
            current = current_records(game)
            for o in game["origins"]:
                state, _, _ = origin_state(kick, o, checked, cfg)
                game["origins"][o] = "COMPLETE" if any(r["origin"] == o for r in current) else state
            game["forecast_status"] = (
                "PREGAME_RECORDED" if current else "MISSED" if checked >= kick else "PENDING"
            )
            if (
                checked >= kick
                or kick > checked + timedelta(days=cfg["slate_days"])
                or game["status"] != "STATUS_SCHEDULED"
            ):
                continue
            inputs = game.get("inputs", {})
            fingerprint = digest(
                canonical(
                    {
                        "inputs": material(inputs),
                        "model": model["model_state_sha256"],
                        "schedule_version": game["schedule_version"],
                    }
                )
            )
            latest = max(current, key=lambda r: r["generated_at"]) if current else None
            origins = [o for o in game["origins"] if game["origins"][o] == "DUE"]
            if latest is None:
                origins.append("ON_DEMAND")
            elif latest.get("input_fingerprint") != fingerprint:
                origins.append("UPDATE")
            for origin in origins:
                generated = clock()
                if generated >= kick:
                    continue
                ph, pa, pt = to_three_way(
                    rater.home_probability(game["home"], game["away"], game["neutral_site"]),
                    model["p_tie"],
                )
                reasons = [origin]
                if latest and latest.get("model_state_sha256") != model["model_state_sha256"]:
                    reasons.append(
                        "MODEL_STATE_UPDATED_FROM_FINAL_RESULTS_OR_INITIAL_SEASON_IMPORT"
                    )
                if latest and latest.get("input_fingerprint") != fingerprint:
                    previous_inputs = material(latest.get("inputs", {}))
                    for name, value in material(inputs).items():
                        if previous_inputs.get(name) != value:
                            reasons.append(
                                name.upper() + "_CHANGED; NO_UNVALIDATED_PROBABILITY_ADJUSTMENT"
                            )
                if len(game["schedule_history"]) > 1:
                    reasons.append("CURRENT_SCHEDULE_VERSION")
                record = {
                    "game_id": game["game_id"],
                    "home": game["home"],
                    "away": game["away"],
                    "kickoff": game["kickoff"],
                    "schedule_version": game["schedule_version"],
                    "origin": origin,
                    "generated_at": stamp(generated),
                    "mode": "LIVE_BASELINE",
                    "role": "fallback",
                    "status": "VALID",
                    "model_id": model_id,
                    "model_version": cfg["model_version"],
                    "model_label": cfg["model_label"],
                    "model_state_sha256": model["model_state_sha256"],
                    "p_home": ph,
                    "p_away": pa,
                    "p_tie": pt,
                    "win_probability": max(ph, pa),
                    "predicted_winner": game["home"] if ph >= pa else game["away"],
                    "inputs": inputs,
                    "input_fingerprint": fingerprint,
                    "reasons": reasons,
                    "neutral_site": game["neutral_site"],
                    **identity,
                }
                if cfg.get("analysis_enabled"):
                    from season_analysis import explain_forecast
                    record["venue"] = game.get("venue")
                    record["explanation"] = explain_forecast(record, model, generated)
                deadline = (
                    origin_state(kick, origin, generated, cfg)[2]
                    if origin in cfg["origin_seconds"]
                    else kick
                )
                publish(root, record, deadline, clock)
            game["predictions"] = legacy_records(cfg, game) + all_records(root, game["game_id"])
            current = current_records(game)
            game["prediction"] = max(current, key=lambda r: r["generated_at"]) if current else None
            game["forecast_status"] = "PREGAME_RECORDED" if current else "BLOCKED"
            for o in game["origins"]:
                if any(r["origin"] == o for r in current):
                    game["origins"][o] = "COMPLETE"
        for game in games:
            records = current_records(game)
            game["prediction"] = max(records, key=lambda r: r["generated_at"]) if records else None
        checked = clock()
        checks = source["checks"]
        for name, check in checks.items():
            if check.get("status") in ("OK", "AVAILABLE", "LIMITED", "PROVENANCE_LIMITED"):
                check["last_successful_check"] = check.get("captured_at", stamp(checked))
            else:
                check["last_successful_check"] = (
                    previous.get("sources", {}).get(name, {}).get("last_successful_check")
                )
        view = {
            "season": cfg["season"],
            "games": games,
            "sources": checks,
            "scoring_policy": policy,
            "scorecards": score_season(games, checked, policy),
            "updated_at": stamp(checked),
            "last_successful_source_check": stamp(checked),
            "next_scheduled_run": next_check(games, checked, cfg),
            "last_forecast_generation": max(
                (r["generated_at"] for g in games for r in g["predictions"]), default=None
            ),
            "model": model,
            "worker_status": "HEALTHY",
            "full_v2_status": "BLOCKED: registry, reviewed artifacts, PIT dataset absent",
            "paid_usage": 0,
            "hosting": "local launchd; awake/network required",
        }
        # Reporting is deterministic in selected forecast/outcome versions; repeated polls
        # without new evidence do not create a new improvement entry.
        improvements = read(root / "improvement-index.json", {})
        for week, analysis in view["scorecards"]["weekly_error_analysis"].items():
            week_games = [g for g in games if str(g["week"]) == str(week)]
            if not any(g["outcomes"] for g in week_games):
                continue
            observation = {
                "season": cfg["season"],
                "week": int(week),
                "analysis": analysis,
                "scorecard": view["scorecards"]["summary"]["weekly"][str(week)],
                "selected_games": [
                    g for g in view["scorecards"]["games"] if str(g["week"]) == str(week)
                ],
            }
            evidence_hash = digest(canonical(observation))
            if improvements.get(str(week), {}).get("evidence_hash") != evidence_hash:
                report = {
                    **observation,
                    "generated_at": stamp(checked),
                    "evidence_hash": evidence_hash,
                    "report_status": "COMPLETE"
                    if all(
                        g["outcomes"] and g["outcomes"][-1]["status"] == "FINAL" for g in week_games
                    )
                    else "PARTIAL",
                    "hypothesis": "No causal defect inferred from outcome alone; review calibration and timestamped source gaps.",
                    "proposed_change": "Investigate evidence before proposing a model change.",
                    "experiment": "Chronological challenger evaluation with frozen holdout; no weekly retuning on this scorecard.",
                    "experiment_results": None,
                    "promotion_decision": "NO_CHANGE; manual evidence review required",
                    "rollback_model_version": cfg["model_version"],
                }
                report_id = journal(root, "weekly-reports", report)
                improvements[str(week)] = {
                    "evidence_hash": evidence_hash,
                    "report_id": report_id,
                    "report_status": report["report_status"],
                }
        replace_view(root / "improvement-index.json", improvements)
        view["improvement_log"] = improvements
        if cfg.get("postgame_enabled"):
            try:
                from season_postgame import refresh_postgame
                view["postgame_evidence"] = refresh_postgame(view, root, cfg, clock)
            except (ValueError, OSError, KeyError, TypeError) as error:
                view["postgame_evidence"] = {}
                view["postgame_status"] = {"status": "FAILED", "reason": str(error)}
        if cfg.get("analysis_enabled"):
            try:
                from season_analysis import refresh_analysis
                refresh_analysis(view, root, cfg, clock=clock(), evidence=view.get("postgame_evidence", {}))
                view["analysis_status"] = {"status": "READY"}
            except (ValueError, OSError, KeyError, TypeError) as error:
                view["analysis"] = {}
                view["analysis_status"] = {"status": "FAILED", "reason": str(error)}
        if cfg.get("simulation_enabled", True) and cfg.get("simulation_config"):
            try:
                from season_simulation import snapshot
                view["simulation"] = snapshot(view, root, cfg["simulation_config"])
                prior = previous.get("simulation", {})
                history = list(previous.get("simulation_history", []))
                if prior.get("snapshot_id") and prior["snapshot_id"] != view["simulation"].get("snapshot_id"):
                    history.append(prior)
                view["simulation_history"] = history
            except (ValueError, OSError, KeyError, TypeError) as error:
                view["simulation"] = {"status": "FAILED", "blocked_reason": str(error)}
                view["simulation_history"] = previous.get("simulation_history", [])
        replace_view(root / "view.json", view)
        journal(
            root,
            "scorecards",
            {"generated_at": stamp(checked), "policy": policy, "cards": view["scorecards"]},
        )
        replace_view(
            root / "worker.json",
            {
                "status": "HEALTHY",
                "checked_at": stamp(checked),
                "pid": os.getpid(),
                "next_scheduled_run": view["next_scheduled_run"],
            },
        )
        return view


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--config")
    args = parser.parse_args()
    cfg, root = configuration(args.config)
    while True:
        try:
            view = read(root / "view.json", {})
            if args.once or not view or now() >= datetime.fromisoformat(view["next_scheduled_run"]):
                view = run_once(cfg, root)
                print(
                    json.dumps(
                        {
                            "updated_at": view.get("updated_at"),
                            "games": len(view.get("games", [])),
                            "next_scheduled_run": view.get("next_scheduled_run"),
                        }
                    ),
                    flush=True,
                )
            else:
                replace_view(
                    root / "worker.json",
                    {
                        "status": "HEALTHY",
                        "checked_at": stamp(now()),
                        "pid": os.getpid(),
                        "next_scheduled_run": view["next_scheduled_run"],
                    },
                )
        except Exception as error:
            replace_view(
                root / "worker.json",
                {
                    "status": "FAILED",
                    "checked_at": stamp(now()),
                    "pid": os.getpid(),
                    "error": type(error).__name__ + ": " + str(error)[:240],
                },
            )
            if args.once:
                raise
        if args.once:
            break
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
