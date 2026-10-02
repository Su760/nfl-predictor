from copy import deepcopy
from datetime import timedelta

import pytest

from fantasy.receiving import fit_priors, forecast_game, kickoff, policy, valid
from fantasy.receiving_eval import calibrate, report, samples


def fixture():
    games = [dict(game_id=f"g{w}", season=2025, week=w, gameday=f"2025-09-{w * 7:02}",
                  gametime="13:00", away_team="A", home_team="B", completed=True) for w in (1, 2, 3)]
    teams = [dict(game_id=g["game_id"], season=2025, team=t, targets=30, attempts=35)
             for g in games for t in ("A", "B")]
    players = [dict(player_id=f"00-000000{i}", player_display_name=pos, position=pos,
                    game_id=g["game_id"], season=2025, team="A", targets=targets,
                    receptions=targets / 2, receiving_yards=targets * 5)
               for g in games for i, pos, targets in [(1, "WR", 8), (2, "TE", 4), (3, "RB", 4), (4, "FB", 2)]]
    cfg = policy()
    train_players = [{**r, "season": 2019} for r in players]
    train_teams = [{**r, "season": 2019} for r in teams]
    model = fit_priors(train_players, train_teams, cfg)
    return players, teams, games, model


def test_only_prior_games_population_and_no_realized_volume():
    players, teams, games, model = fixture()
    cutoff = kickoff(games[2]) - timedelta(hours=24)
    result = forecast_game(games[2], cutoff, players, teams, games, model)
    changed = deepcopy(players)
    changed.append(dict(players[0], game_id="g3", player_id="00-9999999", targets=10000))
    for row in changed:
        if row["game_id"] == "g3":
            row.update(targets=10000, receptions=10000, receiving_yards=999999, team="B")
    future_teams = [{**r, "targets": 99999} if r["game_id"] == "g3" else r for r in teams]
    assert forecast_game(games[2], cutoff, changed, future_teams, games, model) == result
    assert all("g3" not in r["sample"]["game_ids"] for r in result[0])


def test_eastern_sunday_evening_not_available_at_monday_cutoff():
    players, teams, games, model = fixture()
    games[1].update(gameday="2025-09-14", gametime="20:20")
    games[2].update(gameday="2025-09-15", gametime="20:15")
    cutoff = kickoff(games[2]) - timedelta(hours=24)
    assert cutoff.date().isoformat() == "2025-09-15"  # UTC is already Monday.
    assert kickoff(games[1]) > cutoff
    rows, _ = forecast_game(games[2], cutoff, players, teams, games, model)
    assert rows and all(r["sample"]["game_ids"] == ["g1"] for r in rows)


def test_all_pass_catchers_and_coherent_remainder():
    players, teams, games, model = fixture()
    rows, volume = forecast_game(games[2], kickoff(games[2]) - timedelta(hours=24), players, teams, games, model)
    assert {r["position"] for r in rows} == {"WR", "TE", "RB", "FB"}
    t = volume[0]
    assert 0 <= sum(r["estimate"]["targets"] for r in rows) <= t["targets"] <= t["attempts"]
    assert t["allocated_targets"] + t["unallocated_targets"] == pytest.approx(t["targets"])
    assert t["unallocated_targets"] > 0
    assert all(0 <= r["estimate"]["receptions"] <= r["estimate"]["targets"] for r in rows)
    missing = [r for r in players if not (r["game_id"] == "g2" and r["position"] == "WR")]
    other, v = forecast_game(games[2], kickoff(games[2]) - timedelta(hours=24), missing, teams, games, model)
    wr = next(r for r in other if r["position"] == "WR")
    assert wr["sample"]["observed_games"] == 1 and wr["sample"]["expected_games"] == 2
    assert v[0]["unallocated_targets"] > t["unallocated_targets"]


def test_missing_team_data_no_volume_and_invalid_values_unavailable():
    players, teams, games, model = fixture()
    rows, volumes = forecast_game(games[2], kickoff(games[2]) - timedelta(hours=24),
                                  players, [r for r in teams if r["game_id"] != "g2"], games, model)
    assert rows == [] and all(v["targets"] is None for v in volumes)
    assert not valid(dict(targets=None, receptions=0, receiving_yards=0))
    assert not valid(dict(targets=0, receptions=1, receiving_yards=0))
    assert valid(dict(targets=0, receptions=0, receiving_yards=0))
    assert valid(dict(targets=1, receptions=1, receiving_yards=-3))


def test_one_overcounted_game_cannot_hide_behind_another_gap():
    players, teams, games, model = fixture()
    players = [{**r, "targets": 31, "receptions": 1} if r["game_id"] == "g1" and r["position"] == "WR"
               else r for r in players if r["game_id"] != "g2"]
    rows, volumes = forecast_game(games[2], kickoff(games[2]) - timedelta(hours=24), players, teams, games, model)
    assert rows == [] and volumes[0]["status"] == "Prior target coverage incoherent"


def test_priors_exclude_2026_and_intervals_sparse_fallback():
    players, teams, _, model = fixture()
    fit = fit_priors([{**r, "season": 2019} for r in players] + [{**r, "season": 2026, "targets": 9999} for r in players],
                     [{**r, "season": 2019} for r in teams] + [{**r, "season": 2026, "targets": 99999} for r in teams], policy())
    assert fit == model
    rows = [{"position": "WR", "tier": "low", "estimate": {m: 1 for m in ("targets", "receptions", "receiving_yards")},
             "rolling": {m: 2 for m in ("targets", "receptions", "receiving_yards")},
             "actual": {m: 0 for m in ("targets", "receptions", "receiving_yards")}} for _ in range(5)]
    calibrate(rows, model)
    assert "WR/low" not in model["intervals"]["model"]
    assert model["intervals"]["model"]["ALL"]["targets"] == {"radius": 1, "n": 5, "group": "ALL"}


def test_forecast_population_missing_outcomes_not_zero_and_pairs_identical():
    players, teams, games, model = fixture()
    players = [r for r in players if not (r["game_id"] == "g3" and r["position"] == "WR")]
    records, coverage = samples((players, teams, games, {}), model)
    missing = [r for r in records if r["game_id"] == "g3" and r["position"] == "WR"]
    assert len(missing) == 1 and missing[0]["actual"] is None
    assert "unknown" in missing[0]["outcome_status"]
    result = report(records, coverage)
    for group in result["groups"].values():
        assert group["confirmed_dnp"] is None
        for metric in ("targets", "receptions", "receiving_yards"):
            assert group["models"]["model"][metric]["n"] == group["models"]["rolling"][metric]["n"]
    assert result["groups"]["WR"]["missing_outcome"] == 1


def test_kickoff_eastern_dst_and_missing():
    assert kickoff({"gameday":"2026-10-04", "gametime":"13:00"}).isoformat() == "2026-10-04T17:00:00+00:00"
    assert kickoff({"gameday":"2026-11-15", "gametime":"13:00"}).isoformat() == "2026-11-15T18:00:00+00:00"
    assert kickoff({"gameday":"2026-10-04", "gametime":""}) is None
