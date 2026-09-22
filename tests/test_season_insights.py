import copy
import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
s = importlib.import_module("season_insights")


def test_public_evaluation_removes_private_artifact_paths():
    evaluation = {
        "declaration": {
            "native_manifest": {
                "declaration": "/Users/private/live-data/declaration.json",
                "datasets": {
                    "T60": {
                        "path": "/Users/private/live-data/T60.jsonl",
                        "sha256": "saved-hash",
                    }
                },
            }
        }
    }
    public = s.public_evaluation(evaluation)
    manifest = public["declaration"]["native_manifest"]
    assert "declaration" not in manifest
    assert "path" not in manifest["datasets"]["T60"]
    assert manifest["datasets"]["T60"]["sha256"] == "saved-hash"
    assert evaluation["declaration"]["native_manifest"]["datasets"]["T60"]["path"].startswith(
        "/Users/"
    )


def market_fixture():
    game = {"home": "BUF", "away": "DET", "kickoff": "2026-09-18T00:00:00Z"}
    prediction = {
        "p_home": 0.594,
        "p_away": 0.396,
        "p_tie": 0.01,
        "published_at": "2026-09-16T00:00:00Z",
    }
    event = {
        "competitions": [
            {
                "competitors": [
                    {"homeAway": side, "team": {"abbreviation": team}}
                    for side, team in [("home", "BUF"), ("away", "DET")]
                ],
                "odds": [
                    {
                        "provider": {"id": "1", "name": "Book"},
                        "moneyline": {
                            "home": {"close": {"odds": "-200"}},
                            "away": {"close": {"odds": "+170"}},
                        },
                    }
                ],
            }
        ]
    }
    return game, prediction, event


def test_odds_conversion_and_no_margin():
    assert s.no_vig("+100", "-100")["home"] == 0.5
    p = s.no_vig("-200", "+170")
    assert p["home"] == pytest.approx((2 / 3) / (2 / 3 + 100 / 270))
    assert p["home"] + p["away"] == pytest.approx(1)
    for bad in ["nan", "inf", "0", "-99"]:
        with pytest.raises(ValueError):
            s.no_vig(bad, "-110")


def test_market_timing_and_source_pairing_fail_closed():
    g, p, event = market_fixture()
    clock = datetime(2026, 9, 17, tzinfo=UTC)
    m = s.market_comparison(g, p, event, "2026-09-16T01:00:00Z", clock)
    assert m["timing"] == "LATER_THAN_FORECAST"
    assert m["model_home_conditional"] == pytest.approx(0.6)
    assert m["model_uses_market"] is False
    assert (
        s.market_comparison(g, p, event, "2026-09-18T00:00:00Z", clock)["status"] == "UNAVAILABLE"
    )
    assert (
        s.market_comparison(g, p, event, "2026-09-17T01:00:00Z", clock)["status"] == "UNAVAILABLE"
    )
    offer = event["competitions"][0]["odds"][0]
    second = copy.deepcopy(offer)
    del offer["moneyline"]["away"]
    del second["moneyline"]["home"]
    second["provider"]["id"] = "2"
    event["competitions"][0]["odds"].append(second)
    assert (
        s.market_comparison(g, p, event, "2026-09-16T01:00:00Z", clock)["status"] == "UNAVAILABLE"
    )


def test_wrong_teams_do_not_produce_comparison():
    g, p, event = market_fixture()
    g["home"] = "KC"
    assert (
        s.market_comparison(g, p, event, "2026-09-16T01:00:00Z", datetime(2026, 9, 17, tzinfo=UTC))[
            "status"
        ]
        == "UNAVAILABLE"
    )


def game():
    return {
        "game_id": "a",
        "season": 2026,
        "week": 1,
        "kickoff": "2026-09-10T00:00:00Z",
        "home": "BUF",
        "away": "DET",
        "neutral_site": False,
        "outcomes": [
            {
                "observed_at": "2026-09-10T04:00:00Z",
                "status": "FINAL",
                "home_score": 20,
                "away_score": 10,
            }
        ],
    }


def test_rankings_use_only_prior_week_known_finals_and_preserve_history():
    g = game()
    before = copy.deepcopy(g)
    view = {"games": [g]}
    cutoff = datetime(2026, 9, 11, tzinfo=UTC)
    assert s.finalized_before(view, 1, cutoff) == []
    finals = s.finalized_before(view, 2, cutoff)
    policy = {
        "initial": 1505,
        "home_field_points": 65,
        "k_factor": 20,
        "offseason_retention": 2 / 3,
        "logistic_scale": 400,
        "mov_denominator": 2.2,
        "mov_rating_scale": 0.001,
    }
    base = {"BUF": 1505.0, "DET": 1505.0}
    ranks = s.rating_table(base, policy, finals)
    assert ranks[0]["team"] == "BUF" and ranks[0]["rating"] > 1505
    assert base == {"BUF": 1505.0, "DET": 1505.0} and g == before
    g["outcomes"].append(
        {
            "observed_at": "2026-09-12T04:00:00Z",
            "status": "FINAL",
            "home_score": 0,
            "away_score": 40,
        }
    )
    assert s.finalized_before(view, 2, cutoff)[0]["final"]["home_score"] == 20
    assert s.finalized_before(view, 2, datetime(2026, 9, 10, 4, tzinfo=UTC)) == []


def test_tendency_filters_denominators_and_future_game_exclusion():
    common = {
        "game_id": "past",
        "posteam": "BUF",
        "defteam": "DET",
        "play_type": "pass",
        "qb_kneel": 0,
        "qb_spike": 0,
        "epa": 1.0,
        "qtr": 1,
        "score_differential": 0,
        "down": 1,
        "qb_dropback": 1,
        "yards_gained": 20,
    }
    rows = [
        common,
        {**common, "play_type": "run", "qb_dropback": 0, "epa": -1.0, "yards_gained": 5},
        {**common, "qb_kneel": 1},
        {**common, "qb_spike": 1},
        {**common, "game_id": "future", "epa": 100.0},
        {**common, "qtr": 4, "down": 3, "epa": 0.0},
    ]
    t = s.tendencies(pl.DataFrame(rows), "BUF", ["past"], s.configuration())
    assert t["offense_plays"] == 3 and t["neutral_plays"] == 2 and t["early_down_plays"] == 2
    assert t["offense_epa"] == 0 and t["neutral_pass_rate"] == 0.5
    assert t["explosive_rate"] == pytest.approx(2 / 3)
    assert t["small_sample"] and t["game_ids"] == ["past"]
    empty = s.tendencies(pl.DataFrame(rows), "BUF", [], s.configuration())
    assert empty["offense_epa"] is None and empty["neutral_pass_rate"] is None


def test_viewer_new_routes_do_not_expose_arbitrary_files():
    viewer = importlib.import_module("week1_viewer")
    for path in ["/rankings", "/teams/BUF", "/insights.js"]:
        assert viewer.static_response(path)[2] == 200
    for path in ["/teams/../../../etc/passwd", "/insights.json", "/teams/BUF/extra"]:
        assert viewer.static_response(path) is None


def test_market_respects_configured_provider_team_aliases():
    g, p, event = market_fixture()
    g["home"] = "LA"
    event["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "LAR"
    assert (
        s.market_comparison(
            g,
            p,
            event,
            "2026-09-16T01:00:00Z",
            datetime(2026, 9, 17, tzinfo=UTC),
            aliases={"LAR": "LA"},
        )["status"]
        == "OBSERVED_CONTEXT"
    )


def test_team_matchup_handles_tbd_kickoffs_without_mutating_schedule():
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for viewer sorting regression")
    source = Path(__file__).parents[1] / "ops/viewer/insights.js"
    code = (
        source.read_text()
        + """
const games=[{game_id:'later',week:18,home:'BUF',away:'DET',kickoff:null},{game_id:'next',week:2,home:'BUF',away:'DET',kickoff:'2026-09-18T00:00:00Z'}];
const saved=JSON.stringify(games);
if(nextTeamMatchup(games,'BUF',2).game_id!=='next')throw Error('wrong matchup');
if(nextTeamMatchup(games,'BUF',18).game_id!=='later')throw Error('TBD missing');
if(JSON.stringify(games)!==saved)throw Error('schedule changed');
"""
    )
    subprocess.run([node, "-e", code], check=True, capture_output=True)


def test_archived_price_selection_uses_latest_predecision_quote_not_later_better_price():
    g, p, event = market_fixture()
    g["source_event_id"] = "game"
    receipts = [
        {"captured_at": at, "raw_sha256": sha}
        for at, sha in [
            ("2026-09-16T01:00:00Z", "later"),
            ("2026-09-15T23:59:00Z", "latest"),
            ("2026-09-15T23:00:00Z", "older"),
        ]
    ]
    seen = []

    def load(sha):
        seen.append(sha)
        return {"game": event}

    result = s.market_at_forecast(g, p, receipts, load, datetime(2026, 9, 17, tzinfo=UTC), {}, 7200)
    assert result["raw_sha256"] == "latest" and seen == ["latest"]
    assert result["timing"] == "OBSERVED_BY_FORECAST"
    assert (
        s.market_at_forecast(g, p, receipts[2:], load, datetime(2026, 9, 17, tzinfo=UTC), {}, 30)
        is None
    )
