"""Dedicated public inputs and immutable prospective receiving archives."""
import argparse
import csv
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from fantasy.config import ROOT, configuration
from fantasy.receiving import METRICS, digest, forecast_game, kickoff, method_hash, policy, valid
from fantasy.sources import atomic_json, fetch_source, unique_index

ARTIFACT_ROOT = ROOT / "artifacts/fantasy/receiving/v1"


def source(name, season, refresh=False):
    import pyarrow.parquet as pq

    cfg = configuration()
    url = cfg["sources"][name].format(season=season)
    receiving = cfg["cache"] / "receiving"
    cfg = {**cfg, "cache": receiving / "inputs"}
    caches = [cfg["cache"], configuration()["cache"], configuration()["cache"] / "history"]
    matches = []
    if not refresh:
        for cache in caches:
            for path in (cache / "receipts").glob(name + "-*.json"):
                receipt = json.loads(path.read_text())
                if receipt["url"] == url:
                    matches.append((receipt, cache))
    if matches:
        receipt, cache = max(matches, key=lambda item: item[0]["captured_at"])
        raw = (cache / "raw" / receipt["sha256"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
            raise ValueError("Receiving input hash mismatch")
        rows = (pq.read_table(io.BytesIO(raw)).to_pylist() if url.endswith(".parquet")
                else list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))))
    else:
        rows, receipt = fetch_source(name, url, cfg)
    return rows, receipt


def dataset(season, schedule=None, refresh=False):
    players, pr = source("players", season, refresh)
    teams, tr = source("teams", season, refresh)
    schedule, sr = source("schedule", season, refresh) if schedule is None else schedule
    aliases = policy()["team_aliases"]
    games = [{"game_id": r["game_id"], "season": season, "week": int(r["week"]),
              "gameday": r["gameday"], "gametime": r.get("gametime"),
              "away_team": aliases.get(r["away_team"], r["away_team"]),
              "home_team": aliases.get(r["home_team"], r["home_team"]),
              "completed": r.get("home_score") not in (None, "") and r.get("away_score") not in (None, "")}
             for r in schedule if int(r["season"]) == season and r["game_type"] == "REG"]
    unique_index(games, ("game_id",))
    gids = {g["game_id"] for g in games}
    fields = {"player_id", "player_display_name", "position", "team", "season", "game_id", *METRICS}
    players = [{k: r.get(k) for k in fields} for r in players if r.get("season_type") == "REG"
               and r.get("player_id") and r.get("team") and r.get("game_id") in gids]
    teams = [{k: r.get(k) for k in ("team", "season", "game_id", "targets", "attempts")}
             for r in teams if r.get("season_type") == "REG" and r.get("game_id") in gids]
    for row in players + teams:
        row["team"] = aliases.get(row["team"], row["team"])
    unique_index(players, ("player_id", "game_id"))
    unique_index(teams, ("team", "game_id"))
    game_index = {g["game_id"]: g for g in games}
    for row in players + teams:
        game = game_index[row["game_id"]]
        if row["team"] not in (game["away_team"], game["home_team"]):
            raise ValueError("Receiving row team contradicts scheduled game")
    for row in teams:
        if not (isinstance(row["targets"], (int, float)) and isinstance(row["attempts"], (int, float))
                and 0 <= row["targets"] <= row["attempts"] and row["attempts"] == int(row["attempts"])
                and row["targets"] == int(row["targets"])):
            raise ValueError("Invalid team volume")
    return players, teams, games, {"players": pr, "teams": tr, "schedule": sr}


def build(refresh=True):
    cfg = configuration()
    model = json.loads((ARTIFACT_ROOT / "model.json").read_text())
    if model["policy"] != policy() or model["method_sha256"] != method_hash():
        raise ValueError("Receiving method/config changed after benchmark")
    players, teams, games, sources = dataset(cfg["season"], refresh=refresh)
    generated = datetime.now(UTC)
    if any(datetime.fromisoformat(r["captured_at"]) > generated for r in sources.values()):
        raise ValueError("Input receipt is after prediction cutoff")
    upcoming = [g for g in games if kickoff(g) and kickoff(g) > generated]
    if not upcoming:
        raise ValueError("No future games with verified schedule time")
    week = min(g["week"] for g in upcoming)
    rows, volumes = [], []
    for game in upcoming:
        if game["week"] != week:
            continue
        predicted, allocated = forecast_game(game, generated, players, teams, games, model)
        rows.extend(r for r in predicted if r["position"] in {"WR", "TE"})
        volumes.extend({**r, "game_id": game["game_id"]} for r in allocated)
    archive = {"version": 1, "season": cfg["season"], "week": week, "generated_at": generated.isoformat(),
               "input_cutoff": generated.isoformat(), "model_sha256": digest(model),
               "method_sha256": method_hash(), "sources": sources, "rows": rows, "teams": volumes,
               "experimental": model["experimental"], "gates": model["gate_failures"],
               "data_weeks": sorted({g["week"] for g in games if g["completed"]
                                      and g["gameday"] < generated.astimezone(ZoneInfo("America/New_York")).date().isoformat()}),
               "unknown_kickoffs": sum(kickoff(g) is None for g in games)}
    root = cfg["cache"] / "receiving"
    path = archive_forecast(root, archive)
    # Published numeric outcomes only, for later matching of local predictions.
    atomic_json(root / "outcomes.json", {"captured_at": generated.isoformat(), "source": sources["players"],
                "rows": [{**r, "status": "observed" if valid(r) else "invalid / unavailable"} for r in players
                         if any(g["game_id"] == r["game_id"] and g["completed"]
                                and g["gameday"] < generated.astimezone(ZoneInfo("America/New_York")).date().isoformat()
                                for g in games)]})
    print(json.dumps({"archive": path.name, "week": week, "players": len(rows), "generated_at": archive["generated_at"]}))
    return archive


def archive_forecast(root, archive, now=None):
    """Exclusive version write; a calculation crossing kickoff is never prospective."""
    written = now or datetime.now(UTC)
    if any(datetime.fromisoformat(r["kickoff"]) <= written for r in archive["rows"]):
        raise ValueError("Kickoff passed before archival; no prospective receipt written")
    if datetime.fromisoformat(archive["generated_at"]) > written:
        raise ValueError("Prediction cutoff is after archival time")
    if any(datetime.fromisoformat(r["captured_at"]) > datetime.fromisoformat(archive["generated_at"])
           for r in archive["sources"].values()):
        raise ValueError("Input was captured after prediction cutoff")
    archive["archived_at"] = written.isoformat()
    identity = digest(archive)
    path = root / "forecasts" / (written.strftime("%Y%m%dT%H%M%S%fZ") + "-" + identity + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as output:
        json.dump(archive, output, allow_nan=False)
    atomic_json(root / "latest.json", {"archive": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return path


def saved_sheet(cfg):
    """Read-only API: never creates forecasts or fetches sources."""
    try:
        root = cfg["cache"] / "receiving"
        pointer = json.loads((root / "latest.json").read_text())
        if Path(pointer["archive"]).name != pointer["archive"]:
            raise ValueError("Invalid archive name")
        raw = (root / "forecasts" / pointer["archive"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pointer["sha256"]:
            raise ValueError("Forecast archive integrity mismatch")
        result = json.loads(raw)
        if result["season"] != cfg["season"]:
            raise ValueError("Projection season differs")
        model = json.loads((ARTIFACT_ROOT / "model.json").read_text())
        if digest(model) != result["model_sha256"] or method_hash() != result["method_sha256"]:
            raise ValueError("Projection method/model differs from archived version")
        age = (datetime.now(UTC) - datetime.fromisoformat(result["generated_at"])).total_seconds() / 3600
        result.update(state="stale" if age < 0 or age > cfg["stale_after_hours"] else "available", age_hours=age)
        result["benchmark"] = json.loads((ARTIFACT_ROOT / "benchmark.json").read_text())
        result["outcomes"] = json.loads((root / "outcomes.json").read_text())
        return result
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"state": "unavailable", "rows": [], "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse-saved", action="store_true", help="Retain saved source capture times")
    args = parser.parse_args()
    build(refresh=not args.reuse_saved)


if __name__ == "__main__":
    main()
