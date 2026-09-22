import importlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
native = importlib.import_module("season_cutoff")
evaluation = importlib.import_module("season_evaluation")


def test_build_identity_includes_direct_serialization_dependencies():
    assert {
        "ops/season_cutoff.py",
        "ops/season_rebuild.py",
        "ops/week1_live.py",
        "src/nfl_predictor/contracts/enums.py",
        "src/nfl_predictor/features/policy.py",
        "src/nfl_predictor/ratings/base.py",
    } <= set(native.BUILD_DEPENDENCIES)


def game(gid, season, kickoff, home="BUF", away="DET"):
    at = datetime.fromisoformat(kickoff)
    return {
        "game_id": gid,
        "season": season,
        "week": 1,
        "home": home,
        "away": away,
        "kickoff": kickoff,
        "available_at": native.stamp(at + timedelta(hours=24)),
        "game_type": "REG",
        "result": "home",
        "home_score": 20,
        "away_score": 10,
        "neutral_site": False,
    }


def test_horizons_strict_final_availability_not_calendar_date():
    prior = game("prior", 2015, "2015-09-01T20:00:00Z")
    thursday = game("thursday", 2016, "2016-09-01T20:00:00Z")
    target = game("sunday", 2016, "2016-09-04T20:00:00Z")
    future = game("nextseason", 2017, "2017-09-01T20:00:00Z")
    rows = [prior, thursday, target, future]
    at = datetime.fromisoformat(target["kickoff"])
    assert [
        g["game_id"] for g in native.select_history(rows, target, at - timedelta(hours=72))
    ] == ["prior"]
    assert [
        g["game_id"] for g in native.select_history(rows, target, at - timedelta(minutes=60))
    ] == ["prior", "thursday"]
    equal = datetime.fromisoformat(thursday["available_at"])
    assert [g["game_id"] for g in native.select_history(rows, target, equal)] == ["prior"]
    with pytest.raises(ValueError, match="PREGAME"):
        native.select_history(rows, target, at)


def plays(games):
    return pl.DataFrame(
        [
            {
                "game_id": g["game_id"],
                "play_id": float(i),
                "posteam": team,
                "defteam": other,
                "pass_attempt": int(i % 2 == 0),
                "rush_attempt": int(i % 2 == 1),
                "epa": float(i) / 10,
            }
            for g in games
            for i, (team, other) in enumerate(
                [
                    (g["home"], g["away"]),
                    (g["home"], g["away"]),
                    (g["away"], g["home"]),
                    (g["away"], g["home"]),
                ]
            )
        ]
    )


def test_pbp_aggregation_preserves_original_semantics_and_closure():
    g = game("a", 2016, "2016-09-01T20:00:00Z")
    rows, gaps = native.aggregate_plays(plays([g]), [g], {})
    home = next(r for r in rows if r.offense_team == "BUF")
    assert not gaps and home.plays == 2
    assert home.offense_epa_per_play == pytest.approx(0.05)
    assert home.pass_epa_per_play == 0 and home.rush_epa_per_play == 0.1
    assert native.stamp(home.finalized_at_utc) == g["available_at"]
    _, gaps = native.aggregate_plays(plays([g]).filter(pl.col("posteam") == "BUF"), [g], {})
    assert gaps == ["a"]


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("duplicate", "DUPLICATE"),
        ("nan", "NONFINITE"),
        ("fraction", "NONINTEGRAL"),
        ("wrongteam", "MISMATCH"),
    ],
)
def test_invalid_pbp_is_not_silently_used(mutation, error):
    g = game("a", 2016, "2016-09-01T20:00:00Z")
    frame = plays([g])
    if mutation == "duplicate":
        frame = pl.concat([frame, frame.head(1)])
    if mutation == "nan":
        frame = frame.with_columns(pl.lit(float("nan")).alias("epa"))
    if mutation == "fraction":
        frame = frame.with_columns(pl.lit(0.5).alias("pass_attempt"))
    if mutation == "wrongteam":
        frame = frame.with_columns(pl.lit("KC").alias("posteam"))
    with pytest.raises(ValueError, match=error):
        native.aggregate_plays(frame, [g], {})


def test_future_epa_and_target_epa_cannot_affect_features():
    policy = native.load_feature_policy(native.CODE_ROOT / "configs/feature_policy_v1.toml")
    g = game("target", 2016, "2016-09-04T20:00:00Z")
    cutoff = datetime(2016, 9, 4, 19, tzinfo=UTC)
    cls = native.TeamGameEpa
    rows = [
        cls("past", "BUF", "DET", 0.2, 0.3, 0.1, 60, datetime(2016, 9, 2, tzinfo=UTC)),
        cls("past", "DET", "BUF", -0.1, -0.2, 0.0, 60, datetime(2016, 9, 2, tzinfo=UTC)),
    ]
    prior = native.fitted_epa([], cutoff, policy)
    first = native.feature_values(g, rows, prior, cutoff, policy)
    polluted = rows + [
        replace(
            rows[0],
            canonical_event_id="future",
            offense_epa_per_play=100.0,
            finalized_at_utc=cutoff,
        ),
        replace(rows[0], canonical_event_id="target", offense_epa_per_play=100.0),
    ]
    assert native.feature_values(g, polluted, prior, cutoff, policy) == first


def test_native_build_is_reproducible_and_neutral_games_need_no_venue(tmp_path):
    cfg = evaluation.configuration()
    cfg["research_root"] = str(tmp_path / "research")
    cfg["output_root"] = str(tmp_path / "output")
    cfg["native"] = {**cfg["native"], "start_season": 2016, "end_season": 2016}
    frozen = tmp_path / "source.csv"
    frozen.write_text("synthetic schedule for isolated test")
    freeze = tmp_path / "freeze.json"
    freeze.write_text(
        json.dumps({"source_path": str(frozen), "source_sha256": native.file_sha(frozen)})
    )
    cfg["probability_freeze"] = str(freeze)
    rows = [
        game("prior", 2015, "2015-09-01T20:00:00Z"),
        game("thursday", 2016, "2016-09-01T20:00:00Z"),
        game("sunday", 2016, "2016-09-04T20:00:00Z"),
    ]
    rows[-1]["neutral_site"] = True
    for season in [2015, 2016]:
        p = tmp_path / "research/raw/pbp" / f"season={season}" / "data.arrow"
        p.parent.mkdir(parents=True)
        plays([g for g in rows if g["season"] == season]).write_ipc(p)
    manifest = native.build(cfg, rows)
    assert manifest == native.build(cfg, rows)
    t72 = native.load_rows(manifest, "T72")
    t60 = native.load_rows(manifest, "T60")
    assert len(t72) == len(t60) == 2 and len(t72[-1].features) == 4
    assert t72[-1].features != t60[-1].features
    root = Path(manifest["declaration"]).parent
    a = json.loads((root / "selections" / (t72[-1].snapshot_id + ".json")).read_text())
    b = json.loads((root / "selections" / (t60[-1].snapshot_id + ".json")).read_text())
    assert a["current_game_ids"] == [] and b["current_game_ids"] == ["thursday"]
    assert a["prior_game_ids"] == ["prior"]
    assert a["latest_available_at"] < a["cutoff"]
    source_path = root / "input-manifests" / (t60[-1].input_manifest_sha256s[0] + ".json")
    original_manifest = source_path.read_bytes()
    source_path.write_text("tampered")
    with pytest.raises(ValueError, match="INPUT_MANIFEST_HASH_MISMATCH"):
        native.load_rows(manifest, "T60")
    source_path.write_bytes(original_manifest)
    lineage_path = root / "selections" / (t60[-1].snapshot_id + ".json")
    original_lineage = lineage_path.read_bytes()
    lineage_path.write_text("tampered")
    with pytest.raises(ValueError, match="LINEAGE_HASH_MISMATCH"):
        native.load_rows(manifest, "T60")
    lineage_path.write_bytes(original_lineage)
    path = Path(manifest["datasets"]["T60"]["path"])
    path.write_text("tampered")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        native.load_rows(manifest, "T60")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        native.build(cfg, rows)
