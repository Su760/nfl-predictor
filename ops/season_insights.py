"""Read-only football views derived from saved forecasts, results and public PBP."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import tomllib
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import polars as pl
from season_evaluation import configuration
from week1_live import build_model, canonical, digest, replace_view, stamp, write_once

from nfl_predictor.ratings.base import CompletedGame
from nfl_predictor.ratings.elo import EloRater


def time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return result


def no_vig(home, away):
    """Proportional normalization of both American moneylines in one market."""

    def implied(value):
        if isinstance(value, bool):
            raise TypeError("INVALID_AMERICAN_ODDS")
        value = float(value)
        if not math.isfinite(value) or abs(value) < 100:
            raise ValueError("INVALID_AMERICAN_ODDS")
        return 100 / (value + 100) if value > 0 else -value / (-value + 100)

    h, a = implied(home), implied(away)
    return {"home": h / (h + a), "away": a / (h + a), "overround": h + a - 1}


def market_comparison(game, prediction, event, captured_at, clock, aliases=None):
    missing = {
        "status": "UNAVAILABLE",
        "reason": "No compatible two-sided pregame moneyline capture.",
        "model_uses_market": False,
    }
    if not prediction or not event or not game.get("kickoff"):
        return missing
    captured = time(captured_at)
    if captured > clock or captured >= time(game["kickoff"]):
        return missing
    competition = event.get("competitions", [{}])[0]
    teams = {
        c["homeAway"]: c.get("team", {}).get("abbreviation")
        for c in competition.get("competitors", [])
    }
    teams = {side: (aliases or {}).get(team, team) for side, team in teams.items()}
    if teams != {"home": game["home"], "away": game["away"]}:
        return missing
    for offer in competition.get("odds", []):
        provider = offer.get("provider", {})
        if not provider.get("id") or not provider.get("name"):
            continue
        try:
            # Both sides from exactly this provider and response object, same quote field.
            home = offer["moneyline"]["home"]["close"]["odds"]
            away = offer["moneyline"]["away"]["close"]["odds"]
            probabilities = no_vig(home, away)
        except (KeyError, TypeError, ValueError):
            continue
        generated = prediction.get("published_at") or prediction.get("generated_at")
        if not generated or time(generated) >= time(game["kickoff"]) or time(generated) > clock:
            return missing
        q = prediction["p_home"] / (prediction["p_home"] + prediction["p_away"])
        return {
            "status": "OBSERVED_CONTEXT",
            "source": "ESPN / " + provider["name"],
            "provider_id": str(provider["id"]),
            "observed_at": captured_at,
            "provider_updated_at": None,
            "prediction_at": generated,
            "home_odds": home,
            "away_odds": away,
            "market_home": probabilities["home"],
            "market_away": probabilities["away"],
            "overround": probabilities["overround"],
            "model_home_conditional": q,
            "model_home": prediction["p_home"],
            "model_tie": prediction["p_tie"],
            "disagreement_pp": 100 * (q - probabilities["home"]),
            "model_uses_market": False,
            "timing": "LATER_THAN_FORECAST"
            if captured > time(generated)
            else "OBSERVED_BY_FORECAST",
            "method": "Convert both American moneylines to implied probabilities, then divide each by their sum. Model comparison conditions on no tie; tie probability is shown separately.",
            "limitations": "One provider, one captured response, both close fields. Provider update times and contract terms are unavailable; synchronization cannot be independently verified. Observed pregame context, not a guaranteed edge or historical market baseline.",
        }
    return missing


@lru_cache(maxsize=4)
def scoreboard_receipts(root, revision):
    receipts = []
    for path in (Path(root) / "captures").glob("*.json"):
        value = json.loads(path.read_text())
        if "/scoreboard?" in value.get("source_url", ""):
            receipts.append(value)
    return sorted(receipts, key=lambda r: r["captured_at"], reverse=True)


@lru_cache(maxsize=64)
def archived_events(root, sha):
    body = (Path(root) / "raw" / (sha + ".json")).read_bytes()
    if digest(body) != sha:
        raise ValueError("ODDS_CAPTURE_HASH_MISMATCH")
    return {str(e["id"]): e for e in json.loads(body).get("events", [])}


def market_at_forecast(game, prediction, receipts, load_events, clock, aliases, max_age):
    """Latest qualifying archived paired quote, selected by time, never by outcome."""
    if not prediction:
        return None
    decision = time(prediction.get("published_at") or prediction["generated_at"])
    for receipt in sorted(receipts, key=lambda r: r["captured_at"], reverse=True):
        age = (decision - time(receipt["captured_at"])).total_seconds()
        if age < 0:
            continue
        if age > max_age:
            break
        events = load_events(receipt["raw_sha256"])
        comparison = market_comparison(
            game,
            prediction,
            events.get(str(game.get("source_event_id"))),
            receipt["captured_at"],
            clock,
            aliases,
        )
        if comparison["status"] != "UNAVAILABLE":
            return {
                **comparison,
                "raw_sha256": receipt["raw_sha256"],
                "selection": "Latest compatible capture at or before selected forecast; maximum capture age "
                + str(max_age)
                + " seconds.",
            }
    return None


def finalized_before(view, week, cutoff):
    result = []
    for g in view["games"]:
        if g["week"] >= week or not g.get("kickoff") or time(g["kickoff"]) >= cutoff:
            continue
        outcomes = [o for o in g.get("outcomes", []) if time(o["observed_at"]) < cutoff]
        if outcomes and outcomes[-1]["status"] == "FINAL":
            result.append({**g, "final": outcomes[-1]})
    return sorted(result, key=lambda g: (g["kickoff"], g["game_id"]))


def rating_table(base, policy, games):
    rater = EloRater(**policy)
    rater.ratings = dict(base)
    for g in games:
        o = g["final"]
        rater.update(
            CompletedGame(
                g["game_id"],
                g["season"],
                g["home"],
                g["away"],
                o["home_score"],
                o["away_score"],
                g["neutral_site"],
                time(o["observed_at"]),
            )
        )
    return [
        {"team": team, "rating": rating, "rank": i + 1}
        for i, (team, rating) in enumerate(
            sorted(rater.ratings.items(), key=lambda x: (-x[1], x[0]))
        )
    ]


def tendencies(frame, team, allowed_ids, cfg):
    # Official scrimmage pass/run plays; sacks and scrambles count as dropbacks.
    f = frame.filter(
        pl.col("game_id").is_in(allowed_ids),
        pl.col("play_type").is_in(["pass", "run"]),
        pl.col("qb_kneel").fill_null(0) == 0,
        pl.col("qb_spike").fill_null(0) == 0,
        pl.col("epa").is_finite(),
    )
    offense = f.filter(pl.col("posteam") == team)
    defense = f.filter(pl.col("defteam") == team)
    neutral = offense.filter(
        pl.col("qtr").is_between(1, 3),
        pl.col("score_differential").abs() <= cfg["neutral_score_margin"],
    )
    early = offense.filter(pl.col("down").is_in([1, 2]))

    def passing(rows):
        return rows.filter(pl.col("qb_dropback") == 1).height / rows.height if rows.height else None

    explosive = offense.filter(
        ((pl.col("qb_dropback") == 1) & (pl.col("yards_gained") >= cfg["explosive_pass_yards"]))
        | ((pl.col("qb_dropback") != 1) & (pl.col("yards_gained") >= cfg["explosive_rush_yards"]))
    )
    return {
        "games": offense["game_id"].n_unique(),
        "offense_plays": offense.height,
        "defense_plays": defense.height,
        "offense_epa": offense["epa"].mean(),
        "defense_epa": defense["epa"].mean(),
        "neutral_pass_rate": passing(neutral),
        "neutral_plays": neutral.height,
        "early_down_pass_rate": passing(early),
        "early_down_plays": early.height,
        "explosive_rate": explosive.height / offense.height if offense.height else None,
        "explosive_plays": explosive.height,
        "small_sample": offense.height < cfg["minimum_tendency_plays"],
        "game_ids": sorted(offense["game_id"].unique().to_list()),
    }


@lru_cache(maxsize=4)
def read_pbp(path):
    return pl.read_parquet(
        path,
        columns=[
            "game_id",
            "posteam",
            "defteam",
            "play_type",
            "qb_kneel",
            "qb_spike",
            "epa",
            "qtr",
            "score_differential",
            "down",
            "qb_dropback",
            "yards_gained",
        ],
    )


@lru_cache(maxsize=4)
def initial_ratings(source_path, season_config_text):
    import tomllib

    cfg = tomllib.loads(season_config_text)
    with Path(source_path).open() as f:
        _, proof = build_model(list(csv.DictReader(f)), cfg)
    return proof["ratings"]


def snapshot(view, cfg=None, clock=None):
    cfg = cfg or configuration()
    clock = clock or datetime.now(UTC)
    root = Path(cfg["output_root"]).expanduser()
    season_root = Path(cfg["season_root"]).expanduser()
    from season_live import CODE_ROOT

    freeze = json.loads(Path(cfg["probability_freeze"]).expanduser().read_text())
    live_config = tomllib.loads((CODE_ROOT / "configs/season_live.toml").read_text())
    base = initial_ratings(
        freeze["source_path"], (CODE_ROOT / "configs/season_live.toml").read_text()
    )
    pbp_receipt_path = root / "pbp.json"
    receipt = json.loads(pbp_receipt_path.read_text()) if pbp_receipt_path.exists() else None
    frame = read_pbp(str(root / "raw" / (receipt["sha256"] + ".parquet"))) if receipt else None
    weeks = {}
    current = min(
        (g["week"] for g in view["games"] if g.get("kickoff") and time(g["kickoff"]) > clock),
        default=max(g["week"] for g in view["games"]),
    )
    prior = None
    for week in range(1, current + 1):
        starts = [
            time(g["kickoff"]) for g in view["games"] if g["week"] == week and g.get("kickoff")
        ]
        cutoff = min(clock, min(starts))
        finals = finalized_before(view, week, cutoff)
        rows = rating_table(base, view["model"]["elo_policy"], finals)
        previous = {r["team"]: r for r in prior} if prior else {}
        for row in rows:
            team = row["team"]
            games = [g for g in finals if team in (g["home"], g["away"])]
            recent = games[-cfg["recent_games"] :]
            row.update(
                rank_change=previous[team]["rank"] - row["rank"] if previous else None,
                rating_change=row["rating"] - previous[team]["rating"] if previous else None,
                completed_games=len(games),
                recent_results=[
                    {
                        "game_id": g["game_id"],
                        "opponent": g["away"] if team == g["home"] else g["home"],
                        "points_for": g["final"][
                            "home_score" if team == g["home"] else "away_score"
                        ],
                        "points_against": g["final"][
                            "away_score" if team == g["home"] else "home_score"
                        ],
                    }
                    for g in recent
                ],
            )
            row["season_to_date"] = (
                tendencies(frame, team, [g["game_id"] for g in games], cfg)
                if frame is not None
                else None
            )
            row["recent"] = (
                tendencies(frame, team, [g["game_id"] for g in recent], cfg)
                if frame is not None
                else None
            )
        weeks[str(week)] = {
            "as_of": stamp(cutoff),
            "week": week,
            "completed_games": len(finals),
            "rows": rows,
        }
        prior = rows
    source = view["sources"].get("espn_scoreboard", {})
    raw_path = season_root / "raw" / (str(source.get("raw_sha256")) + ".json")
    events = (
        {str(e["id"]): e for e in json.loads(raw_path.read_text()).get("events", [])}
        if raw_path.exists()
        else {}
    )
    receipts = scoreboard_receipts(str(season_root), view["updated_at"])
    market = {}
    for g in view["games"]:
        sid = next(
            (
                c.get("prediction_id")
                for c in view["scorecards"].get("games", [])
                if c["game_id"] == g["game_id"]
            ),
            None,
        )
        p = next(
            (
                p
                for p in g.get("predictions", [])
                if (p.get("revision_id") or p.get("forecast_id")) == sid
            ),
            g.get("prediction"),
        )
        saved_market = market_at_forecast(
            g,
            p,
            receipts,
            lambda sha: archived_events(str(season_root), sha),
            clock,
            live_config["team_aliases"],
            live_config["maximum_capture_age_seconds"],
        )
        market[g["game_id"]] = saved_market or market_comparison(
            g,
            p,
            events.get(str(g.get("source_event_id"))),
            source.get("captured_at"),
            clock,
            aliases=live_config["team_aliases"],
        )
    evaluation = root / "evaluation.json"
    residual = root / "residual.json"
    return {
        "current_week": current,
        "season": view["season"],
        "source_checked_at": view["last_successful_source_check"],
        "weeks": weeks,
        "market": market,
        "evaluation": public_evaluation(json.loads(evaluation.read_text()))
        if evaluation.exists()
        else None,
        "residual": public_evaluation(json.loads(residual.read_text()))
        if residual.exists()
        else None,
        "pbp": receipt,
        "definitions": {
            "ratings": "Production Elo strength on a neutral field, independent of this week's opponent. Prior-season ratings regressed using the unchanged production policy; update only with finalized games before the selected week and as-of time. Positive rank change means moved up. Equal ratings ordered by team code.",
            "tendencies": "Descriptive, not inputs to production Elo. Pass/run plays with finite EPA; exclude kneels and spikes. Dropbacks include sacks and scrambles. Neutral = quarters 1–3, score margin at most 7; early down = first or second down. Explosive = 20+ yards on a dropback or 10+ on a run. Each rate uses its displayed play count as denominator.",
            "efficiency": "EPA is expected points added. Higher offensive EPA/play and lower defensive EPA allowed/play are better. Unadjusted for opponent. Tendencies have no universally better direction. Below 100 offensive plays is flagged as a small sample.",
            "history": "Only completed games before selected week; current corrected PBP describes those games, not as-issued historical inputs. Season-to-date and last four completed games are shown separately. Missing plays are not filled. No pace estimate without a validated clock definition.",
        },
    }


def public_evaluation(evaluation):
    """Remove machine-local artifact paths while retaining hashes and lineage IDs."""
    result = copy.deepcopy(evaluation)
    manifest = result.get("declaration", {}).get("native_manifest")
    if manifest:
        manifest.pop("declaration", None)
        for dataset in manifest.get("datasets", {}).values():
            dataset.pop("path", None)
    return result


def capture_pbp(path):
    cfg = configuration()
    root = Path(cfg["output_root"]).expanduser()
    body = Path(path).read_bytes()
    sha = digest(body)
    frame = pl.read_parquet(path, columns=["game_id", "season"])
    from season_live import configuration as live_configuration

    live, _ = live_configuration()
    if set(frame["season"].unique().to_list()) != {live["season"]}:
        raise ValueError("PBP_SEASON_MISMATCH")
    raw = root / "raw" / (sha + ".parquet")
    raw.parent.mkdir(parents=True, exist_ok=True)
    if raw.exists() and raw.read_bytes() != body:
        raise ValueError("PBP_HASH_COLLISION")
    if not raw.exists():
        raw.write_bytes(body)
    receipt = {
        "sha256": sha,
        "source_url": cfg["pbp_url"],
        "captured_at": stamp(datetime.now(UTC)),
        "games": frame["game_id"].n_unique(),
        "season": live["season"],
        "provider_published_at": None,
    }
    write_once(root / "captures" / (digest(canonical(receipt)) + ".json"), receipt)
    replace_view(root / "pbp.json", receipt)
    print(json.dumps(receipt))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-pbp", required=True, type=Path)
    capture_pbp(parser.parse_args().capture_pbp)
