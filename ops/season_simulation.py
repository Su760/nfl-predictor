"""Auditable season Monte Carlo; missing late tiebreak inputs fail closed."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import random
import sys
import tomllib
from collections import Counter
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from nfl_predictor.ratings.elo import EloRater


class SimulationBlocked(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class Standings:
    def __init__(self, games, divisions, rng):
        self.games, self.divisions, self.rng = games, divisions, rng
        self.teams = sorted(t for members in divisions.values() for t in members)
        self.div = {t: d for d, members in divisions.items() for t in members}
        self.conf = {t: self.div[t].split("_")[0] for t in self.teams}
        self.rows = {t: [] for t in self.teams}
        for g in games:
            for side, other in (("home", "away"), ("away", "home")):
                pf, pa = g[side + "_score"], g[other + "_score"]
                td, against = g.get(side + "_td"), g.get(other + "_td")
                self.rows[g[side]].append(
                    {
                        "opponent": g[other],
                        "points": pf,
                        "against": pa,
                        "value": 2 if pf > pa else 1 if pf == pa else 0,
                        "net_td": None if td is None or against is None else td - against,
                    }
                )

    def selected(self, t, opponents=None):
        return [r for r in self.rows[t] if opponents is None or r["opponent"] in opponents]

    def pct(self, t, opponents=None):
        rows = self.selected(t, opponents)
        return Fraction(sum(r["value"] for r in rows), 2 * len(rows)) if rows else Fraction(0)

    def strength(self, t, victories=False):
        opponents = [r["opponent"] for r in self.rows[t] if not victories or r["value"] == 2]
        rows = [r for o in opponents for r in self.rows[o]]
        return Fraction(sum(r["value"] for r in rows), 2 * len(rows)) if rows else Fraction(0)

    def points_rank(self, t, conference):
        pool = [o for o in self.teams if not conference or self.conf[o] == self.conf[t]]
        scored = {o: sum(r["points"] for r in self.rows[o]) for o in pool}
        allowed = {o: sum(r["against"] for r in self.rows[o]) for o in pool}
        return -(
            2
            + sum(scored[o] > scored[t] for o in pool)
            + sum(allowed[o] < allowed[t] for o in pool)
        )

    def common(self, tied, minimum):
        opponents = set.intersection(*({r["opponent"] for r in self.rows[t]} for t in tied))
        if any(len(self.selected(t, opponents)) < minimum for t in tied):
            return None
        return opponents

    def head_sweep(self, tied):
        wins = [
            t
            for t in tied
            if all(
                any(r["opponent"] == o and r["value"] == 2 for r in self.rows[t])
                for o in tied
                if o != t
            )
        ]
        losses = [
            t
            for t in tied
            if all(
                any(r["opponent"] == o and r["value"] == 0 for r in self.rows[t])
                for o in tied
                if o != t
            )
        ]
        # Each representative is from a different division (at most one meeting).
        if len(wins) == 1:
            return {t: int(t == wins[0]) for t in tied}
        if losses:
            return {t: int(t not in losses) for t in tied}
        return None

    def criterion(self, name, tied):
        common = self.common(tied, 4 if name == "common_wild" else 1)
        if name == "sweep":
            return self.head_sweep(tied)
        if name == "head" and any(not self.selected(t, set(tied) - {t}) for t in tied):
            return None
        if name.startswith("common") and common is None:
            return None
        values = {}
        for t in tied:
            conference = {o for o in self.teams if self.conf[o] == self.conf[t]}
            if name == "head":
                value = self.pct(t, set(tied) - {t})
            elif name == "division":
                value = self.pct(t, set(self.divisions[self.div[t]]))
            elif name in ("common_div", "common_wild"):
                value = self.pct(t, common)
            elif name == "conference":
                value = self.pct(t, conference)
            elif name in ("sov", "sos"):
                value = self.strength(t, name == "sov")
            elif name in ("rank_conf", "rank_all"):
                value = self.points_rank(t, name == "rank_conf")
            elif name in ("net_common", "net_conf", "net_all"):
                opponents = (
                    common if name == "net_common" else conference if name == "net_conf" else None
                )
                value = sum(r["points"] - r["against"] for r in self.selected(t, opponents))
            elif name == "net_td":
                if any(r["net_td"] is None for r in self.rows[t]):
                    raise SimulationBlocked("MISSING_NET_TOUCHDOWNS:" + ",".join(sorted(tied)))
                value = sum(r["net_td"] for r in self.rows[t])
            else:
                raise ValueError(name)
            values[t] = value
        return values

    def break_tie(self, tied, division=False):
        tied = sorted(tied)
        if len(tied) == 1:
            return tied[0]
        if division:
            steps = [
                "head",
                "division",
                "common_div",
                "conference",
                "sov",
                "sos",
                "rank_conf",
                "rank_all",
                "net_common",
                "net_all",
                "net_td",
            ]
        else:
            steps = [
                "head" if len(tied) == 2 else "sweep",
                "conference",
                "common_wild",
                "sov",
                "sos",
                "rank_conf",
                "rank_all",
                "net_conf",
                "net_all",
                "net_td",
            ]
        for step in steps:
            values = self.criterion(step, tied)
            if values is None:
                continue
            keep = [t for t in tied if values[t] == max(values.values())]
            if len(keep) < len(tied):
                return self.break_tie(keep, division)  # NFL restart after any reduction.
        return self.rng.choice(tied)  # Only after all documented criteria were evaluated.

    def rank(self, teams, division=False, division_order=None):
        remaining, result = set(teams), []
        while remaining:
            best = max(self.pct(t) for t in remaining)
            tied = [t for t in remaining if self.pct(t) == best]
            if not division:
                representatives = []
                for d in sorted({self.div[t] for t in tied}):
                    members = [t for t in tied if self.div[t] == d]
                    if division_order is not None:
                        representatives.append(next(t for t in division_order[d] if t in members))
                    else:
                        representatives.append(self.break_tie(members, True))
                tied = representatives
            winner = self.break_tie(tied, division)
            result.append(winner)
            remaining.remove(winner)
        return result

    def seeds(self):
        order = {d: self.rank(members, True) for d, members in self.divisions.items()}
        result = {}
        for conference in sorted(set(self.conf.values())):
            champs = [ranked[0] for d, ranked in order.items() if d.startswith(conference + "_")]
            candidates = [t for t in self.teams if self.conf[t] == conference and t not in champs]
            # Stop after three wildcards: irrelevant lower ranks must not block qualification.
            winners = self.rank(champs, division_order=order)
            for _ in range(3):
                best = max(self.pct(t) for t in candidates)
                tied = [t for t in candidates if self.pct(t) == best]
                reps = [
                    next(t for t in order[d] if t in tied)
                    for d in sorted({self.div[t] for t in tied})
                ]
                winner = self.break_tie(reps)
                winners.append(winner)
                candidates.remove(winner)
            result[conference] = winners
        return result


def playoff(seeds, rater, rng):
    trace = []

    def game(home, away, neutral=False):
        winner = home if rng.random() < rater.home_probability(home, away, neutral) else away
        trace.append({"home": home, "away": away, "neutral_site": neutral, "winner": winner})
        return winner

    champs = []
    for conference in sorted(seeds):
        ranked = seeds[conference]
        survivors = [ranked[0]] + [game(ranked[a], ranked[b]) for a, b in ((1, 6), (2, 5), (3, 4))]
        survivors.sort(key=ranked.index)
        finalists = [game(survivors[0], survivors[3]), game(survivors[1], survivors[2])]
        finalists.sort(key=ranked.index)
        champs.append(game(*finalists))
    return champs, game(*champs, neutral=True), trace


def score_pools(rows, season, start):
    pools = {"decisive": [], "tie": []}
    for row in rows:
        if not start <= int(row["season"]) < season or row.get("game_type") != "REG":
            continue
        if row.get("home_score") in (None, "") or row.get("away_score") in (None, ""):
            continue
        home, away = int(float(row["home_score"])), int(float(row["away_score"]))
        if min(home, away) < 0:
            raise SimulationBlocked("INVALID_HISTORICAL_SCORE")
        pools["tie" if home == away else "decisive"].append((max(home, away), min(home, away)))
    return pools


def simulate(view: dict, cfg: dict) -> dict:
    metadata = {
        "samples": cfg["samples"],
        "seed": cfg["seed"],
        "cutoff": view["updated_at"],
        "model_state_sha256": view["model"]["model_state_sha256"],
        "view_sha256": digest(view),
        "config_sha256": digest(cfg),
        "method": "Elo Monte Carlo with per-season normal strength sensitivity and empirical conditional scores",
        "limits": [
            "Uncalibrated baseline; uncertainty scale is configured sensitivity, not learned or validated.",
            "Team strength fixed within each draw; no known future injuries, transactions or weather.",
            "Scores pooled by winner/tie, not matchup/total calibrated; no live-score conditioning.",
            "Any required missing touchdown tiebreak blocks all probabilities; no dropped samples.",
        ],
        "rating_sd": cfg["rating_sd"],
        "status": "BLOCKED",
        "team_probabilities": None,
    }
    try:
        teams = sorted(t for group in cfg["divisions"].values() for t in group)
        games = sorted(view["games"], key=lambda g: (g.get("kickoff") or "", g["game_id"]))
        if (
            len(teams) != 32
            or len(set(teams)) != 32
            or len(cfg["divisions"]) != 8
            or any(len(members) != 4 for members in cfg["divisions"].values())
            or Counter(d.split("_")[0] for d in cfg["divisions"]) != {"AFC": 4, "NFC": 4}
        ):
            raise SimulationBlocked("INVALID_32_TEAM_ALIGNMENT")
        counts = Counter(t for g in games for t in (g["home"], g["away"]))
        if (
            len(games) != 272
            or len({g["game_id"] for g in games}) != 272
            or set(counts) != set(teams)
            or any(n != 17 for n in counts.values())
        ):
            raise SimulationBlocked("INCOMPLETE_OR_INVALID_272_GAME_SCHEDULE")
        if cfg["samples"] < 1 or cfg["rating_sd"] < 0 or not math.isfinite(cfg["rating_sd"]):
            raise SimulationBlocked("INVALID_SIMULATION_PARAMETERS")
        ratings, pt = view["model"]["ratings"], view["model"]["p_tie"]
        if (
            set(ratings) != set(teams)
            or not all(math.isfinite(v) for v in ratings.values())
            or not 0 <= pt <= 1
        ):
            raise SimulationBlocked("INVALID_MODEL_STATE")
        pools = score_pools(cfg["history_rows"], view["season"], cfg["history_start"])
        if not pools["decisive"] or (pt > 0 and not pools["tie"]):
            raise SimulationBlocked("MISSING_HISTORICAL_CONDITIONAL_SCORE_POOL")
        finals = {}
        cutoff = datetime.fromisoformat(view["updated_at"])
        for g in games:
            if g["home"] == g["away"] or not isinstance(g.get("neutral_site"), bool):
                raise SimulationBlocked("INVALID_GAME_CONTEXT:" + g["game_id"])
            if g.get("status") in ("STATUS_CANCELED", "SOURCE_EVENT_MISSING"):
                raise SimulationBlocked("UNRESOLVED_SCHEDULE:" + g["game_id"])
            outcomes = g.get("outcomes", [])
            if outcomes:
                o = outcomes[-1]
                if o["status"] != "FINAL":
                    raise SimulationBlocked("UNRESOLVED_OUTCOME:" + g["game_id"])
                if datetime.fromisoformat(o["observed_at"]) > cutoff:
                    raise SimulationBlocked("FUTURE_OUTCOME:" + g["game_id"])
                for side in ("home", "away"):
                    if type(o[side + "_score"]) is not int or o[side + "_score"] < 0:
                        raise SimulationBlocked("INVALID_FINAL_SCORE:" + g["game_id"])
                finals[g["game_id"]] = o
            elif g.get("status") == "STATUS_FINAL":
                raise SimulationBlocked("MISSING_FINAL_OUTCOME:" + g["game_id"])
        metadata["preserved_final_games"] = len(finals)
        tally = {t: Counter() for t in teams}
        rng = random.Random(cfg["seed"])
        for _ in range(cfg["samples"]):
            rater = EloRater(**cfg["elo"])
            rater.ratings = {t: ratings[t] + rng.gauss(0, cfg["rating_sd"]) for t in teams}
            completed = []
            for g in games:
                if g["game_id"] in finals:
                    completed.append({**g, **finals[g["game_id"]]})
                    continue
                if rng.random() < pt:
                    hs, aws = rng.choice(pools["tie"])
                else:
                    high, low = rng.choice(pools["decisive"])
                    hs, aws = (
                        (high, low)
                        if rng.random()
                        < rater.home_probability(g["home"], g["away"], g["neutral_site"])
                        else (low, high)
                    )
                completed.append({**g, "home_score": hs, "away_score": aws})
            seeds = Standings(completed, cfg["divisions"], rng).seeds()
            for ranked in seeds.values():
                for i, t in enumerate(ranked):
                    tally[t]["playoffs"] += 1
                    tally[t]["division"] += int(i < 4)
                    tally[t]["one_seed"] += int(i == 0)
            champs, champion, _ = playoff(seeds, rater, rng)
            for t in champs:
                tally[t]["conference"] += 1
            tally[champion]["super_bowl"] += 1
        metrics = ("playoffs", "division", "one_seed", "conference", "super_bowl")
        metadata.update(
            status="COMPLETE",
            team_probabilities={
                t: {m: tally[t][m] / cfg["samples"] for m in metrics} for t in teams
            },
        )
        metadata["maximum_monte_carlo_standard_error"] = 0.5 / math.sqrt(cfg["samples"])
    except SimulationBlocked as error:
        metadata["blocked_reason"] = str(error)
    return metadata


def snapshot(view, root, config_path="configs/season_simulation.toml"):
    """Reuse unchanged evidence; atomically preserve every new simulation snapshot."""
    from season_live import material
    from week1_live import write_once

    cfg = tomllib.loads(Path(config_path).read_text())
    signature = digest({
        "config": cfg,
        "history": view["sources"]["nflverse_history"]["raw_sha256"],
        "source": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model": view["model"]["model_state_sha256"],
        "games": [{k: g.get(k) for k in
                   ("game_id", "kickoff", "schedule_version", "status", "outcomes")}
                  | {"inputs": material(g.get("inputs", {}))} for g in view["games"]],
    })
    index = root / "simulation-index" / (signature + ".json")
    if index.exists():
        reference = json.loads(index.read_text())
        stored = root / "simulations" / (reference["snapshot_id"] + ".json")
        result = json.loads(stored.read_text())
        if digest(result) != reference["snapshot_id"]:
            raise ValueError("SIMULATION_HASH_MISMATCH")
        return {**result, "snapshot_id": reference["snapshot_id"]}
    history_hash = view["sources"]["nflverse_history"]["raw_sha256"]
    if len(history_hash) != 64 or any(c not in "0123456789abcdef" for c in history_hash):
        raise ValueError("INVALID_HISTORY_HASH")
    raw = (root / "raw" / (history_hash + ".csv")).read_bytes()
    if hashlib.sha256(raw).hexdigest() != history_hash:
        raise ValueError("HISTORY_HASH_MISMATCH")
    cfg["history_rows"] = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    policy_bytes = Path(cfg["model_policy"]).read_bytes()
    if hashlib.sha256(policy_bytes).hexdigest() != view["model"]["policy_sha256"]:
        raise ValueError("MODEL_POLICY_HASH_MISMATCH")
    cfg["elo"] = tomllib.loads(policy_bytes.decode())["elo"]
    result = simulate(view, cfg)
    result["source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["history_raw_sha256"] = history_hash
    result["elo_source_sha256"] = hashlib.sha256(
        (Path(__file__).resolve().parents[1] / "src/nfl_predictor/ratings/elo.py").read_bytes()
    ).hexdigest()
    result["evidence_signature"] = signature
    snapshot_id = digest(result)
    write_once(root / "simulations" / (snapshot_id + ".json"), result)
    write_once(index, {"snapshot_id": snapshot_id})
    return {**result, "snapshot_id": snapshot_id}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/season_simulation.toml")
    args = parser.parse_args()
    cfg = tomllib.loads(Path(args.config).read_text())
    root = Path(cfg["data_root"]).expanduser().resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("SIMULATION_DATA_MUST_BE_PRIVATE_OUTSIDE_CODE_ROOT")
    result = snapshot(json.loads((root / "view.json").read_text()), root, args.config)
    print(json.dumps({"status": result["status"], "snapshot_id": result["snapshot_id"],
                      "blocked_reason": result.get("blocked_reason")}))
    return 0 if result["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    sys.exit(main())
