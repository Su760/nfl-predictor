import copy
import hashlib
import importlib.util
import json
import random
import sys
import tomllib
import types
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "season_simulation", ROOT / "ops/season_simulation.py"
)
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)


def g(home, away, hs=21, aws=14, **extra):
    return {"home": home, "away": away, "home_score": hs, "away_score": aws, **extra}


def table(games, divisions=None):
    return sim.Standings(games, divisions or {"AFC_East": ["A", "B", "C", "D"]}, random.Random(7))


def test_ties_count_half_and_strength_weights_opponent_games():
    s = table([g("A", "B", 10, 10), g("A", "C"), g("B", "D", 0, 10)])
    assert s.pct("A") == Fraction(3, 4)
    assert s.strength("A") == Fraction(1, 6)
    assert s.strength("A", True) == 0


def test_division_three_way_restarts_with_two_club_head_to_head():
    # A beats B, B beats C, C beats A; division record eliminates C.
    # Restart resolves A/B on their meeting, regardless of B's common record.
    games = [g("A", "B"), g("B", "C"), g("C", "A"), g("A", "D"), g("B", "D"), g("C", "D", 0, 10)]
    assert table(games).break_tie(["A", "B", "C"], True) == "A"


def test_wildcard_head_sweep_and_loser_elimination_restart():
    divs = {"AFC_East": ["A"], "AFC_West": ["B"], "AFC_North": ["C"]}
    s = table([g("A", "B"), g("A", "C"), g("B", "C")], divs)
    assert s.break_tie(["A", "B", "C"]) == "A"
    assert s.head_sweep(["A", "B", "C"])["A"] == 1
    s = table([g("A", "C"), g("B", "C"), g("A", "B", 14, 14)], divs)
    assert s.head_sweep(["A", "B", "C"]) == {"A": 1, "B": 1, "C": 0}


def test_common_games_four_minimum_not_four_distinct_opponents():
    divs = {"AFC_East": ["A", "B", "C", "D"]}
    games = [g("A", "C"), g("B", "C"), g("A", "D"), g("B", "D")]
    s = table(games, divs)
    assert s.criterion("common_wild", ["A", "B"]) is None
    s = table(games * 2, divs)
    assert s.criterion("common_wild", ["A", "B"]) == {"A": 1, "B": 1}


def test_points_rank_competition_ranks_and_missing_td_fail_closed():
    s = table([g("A", "B", 10, 10), g("C", "D", 7, 0)])
    assert s.points_rank("A", True) == -4  # PF rank1, PA rank3.
    with pytest.raises(sim.SimulationBlocked, match="MISSING_NET_TOUCHDOWNS:A,B"):
        s.break_tie(["A", "B"], True)


def test_coin_only_after_known_equal_net_touchdowns():
    s = table([g("A", "B", 10, 10, home_td=1, away_td=1)])
    assert s.break_tie(["A", "B"], True) in ("A", "B")


def test_postseason_reseeds_home_advantage_and_neutral_super_bowl():
    class AlwaysUpset:
        def home_probability(self, home, away, neutral):
            return 0.0

    seeds = {"AFC": list("ABCDEFG"), "NFC": list("HIJKLMN")}
    champs, champion, trace = sim.playoff(seeds, AlwaysUpset(), random.Random(1))
    assert len(trace) == 13
    assert [(x["home"], x["away"]) for x in trace[3:5]] == [("A", "G"), ("E", "F")]
    assert trace[-1]["neutral_site"] is True
    assert all(not x["neutral_site"] for x in trace[:-1])
    assert all(x["winner"] in (x["home"], x["away"]) for x in trace)
    assert champion in champs and len(champs) == 2


def fixture():
    cfg = tomllib.loads((ROOT / "configs/season_simulation.toml").read_text())
    cfg["elo"] = tomllib.loads((ROOT / "configs/model_policy_v1.toml").read_text())["elo"]
    cfg["samples"] = 12
    cfg["history_rows"] = [
        {"season": 2025, "game_type": "REG", "home_score": h, "away_score": a}
        for h, a in [(13, 10), (28, 7), (31, 28), (21, 17), (20, 20)]
    ]
    teams = sorted(t for members in cfg["divisions"].values() for t in members)
    # Round robin rotation creates 17 distinct opponents/team, 272 games.
    rotation = teams[:]
    games = []
    for week in range(17):
        for i in range(16):
            games.append(
                {
                    "game_id": str(len(games)),
                    "home": rotation[i],
                    "away": rotation[-i - 1],
                    "week": week + 1,
                    "kickoff": "2026-09-11T00:00:00+00:00",
                    "neutral_site": False,
                    "status": "STATUS_SCHEDULED",
                    "outcomes": [],
                }
            )
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]
    view = {
        "season": 2026,
        "updated_at": "2026-09-10T00:00:00+00:00",
        "games": games,
        "model": {
            "ratings": {t: 1400 + i * 7 for i, t in enumerate(teams)},
            "p_tie": 0.005,
            "model_state_sha256": "test-model",
        },
    }
    return view, cfg


def test_full_season_totals_reproducibility_and_uncertainty():
    view, cfg = fixture()
    original = copy.deepcopy(view)
    result = sim.simulate(view, cfg)
    assert result["status"] == "COMPLETE", result
    assert result == sim.simulate(view, cfg)
    assert view == original
    for key, total in {
        "playoffs": 14,
        "division": 8,
        "one_seed": 2,
        "conference": 2,
        "super_bowl": 1,
    }.items():
        assert sum(t[key] for t in result["team_probabilities"].values()) == pytest.approx(total)
    cfg["rating_sd"] = 0
    assert sim.simulate(view, cfg)["team_probabilities"] != result["team_probabilities"]


def test_final_scores_preserved_and_future_outcomes_rejected(monkeypatch):
    view, cfg = fixture()
    cfg["samples"] = 1
    view["games"][0]["outcomes"] = [
        {
            "status": "FINAL",
            "home_score": 3,
            "away_score": 6,
            "observed_at": "2026-09-09T00:00:00+00:00",
        }
    ]
    original = sim.Standings
    observed = []

    def capture(games, *args):
        observed.append((games[0]["home_score"], games[0]["away_score"]))
        return original(games, *args)

    monkeypatch.setattr(sim, "Standings", capture)
    assert sim.simulate(view, cfg)["status"] == "COMPLETE"
    assert observed == [(3, 6)]
    view["games"][0]["outcomes"][0]["observed_at"] = "2026-09-12T00:00:00+00:00"
    assert sim.simulate(view, cfg)["blocked_reason"].startswith("FUTURE_OUTCOME")


def test_missing_tiebreak_does_not_drop_draw_and_bias_probabilities(monkeypatch):
    view, cfg = fixture()

    def blocked(self):
        raise sim.SimulationBlocked("MISSING_NET_TOUCHDOWNS:BUF,NE")

    monkeypatch.setattr(sim.Standings, "seeds", blocked)
    result = sim.simulate(view, cfg)
    assert result["status"] == "BLOCKED"
    assert result["team_probabilities"] is None
    assert result["blocked_reason"] == "MISSING_NET_TOUCHDOWNS:BUF,NE"


def test_schedule_validation_and_score_pool_no_future_leak():
    view, cfg = fixture()
    view["games"].pop()
    assert sim.simulate(view, cfg)["blocked_reason"] == "INCOMPLETE_OR_INVALID_272_GAME_SCHEDULE"
    rows = cfg["history_rows"] + [
        {"season": 2026, "game_type": "REG", "home_score": 99, "away_score": 0}
    ]
    assert (99, 0) not in sim.score_pools(rows, 2026, 2016)["decisive"]


def snapshot_fixture(tmp_path):
    root = tmp_path / "private"
    raw = b"season,game_type,home_score,away_score\n2025,REG,21,17\n"
    raw_hash = hashlib.sha256(raw).hexdigest()
    (root / "raw").mkdir(parents=True)
    (root / "raw" / f"{raw_hash}.csv").write_bytes(raw)
    policy = tmp_path / "policy.toml"
    policy.write_text("[elo]\ninitial_rating = 1500.0\nk_factor = 20.0\nhome_advantage = 55.0\n")
    config = tmp_path / "simulation.toml"
    config.write_text(
        f'model_policy = "{policy}"\nsamples = 1\nseed = 1\nhistory_start = 2016\nrating_sd = 0.0\n'
    )
    view = {
        "season": 2026,
        "updated_at": "2026-09-10T00:00:00+00:00",
        "sources": {"nflverse_history": {"raw_sha256": raw_hash}},
        "model": {
            "model_state_sha256": "model-state",
            "policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest(),
        },
        "games": [
            {
                "game_id": "g1",
                "kickoff": "2026-09-11T00:00:00+00:00",
                "schedule_version": "schedule-v1",
                "status": "STATUS_SCHEDULED",
                "outcomes": [],
                "inputs": {"schedule": {"status": "AVAILABLE", "raw_sha256": "ignored"}},
            }
        ],
    }
    return root, config, view


def install_snapshot_dependencies(monkeypatch):
    def material(inputs):
        volatile = {"captured_at", "last_successful_check", "raw_sha256", "source_updated_at", "age_seconds"}
        return {key: {field: value for field, value in row.items() if field not in volatile} for key, row in inputs.items()}

    monkeypatch.setitem(sys.modules, "season_live", types.SimpleNamespace(material=material))

    def write_once(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = sim.canonical(value)
        if path.exists():
            if path.read_bytes() != payload:
                raise ValueError("IMMUTABLE_CONFLICT")
            return
        path.write_bytes(payload)

    monkeypatch.setitem(sys.modules, "week1_live", types.SimpleNamespace(write_once=write_once))


def test_snapshot_reuses_duplicate_and_verifies_stored_hash(tmp_path, monkeypatch):
    root, config, view = snapshot_fixture(tmp_path)
    install_snapshot_dependencies(monkeypatch)
    calls = []

    def fake_simulate(received_view, cfg):
        calls.append((copy.deepcopy(received_view), copy.deepcopy(cfg)))
        return {"status": "COMPLETE", "cutoff": received_view["updated_at"]}

    monkeypatch.setattr(sim, "simulate", fake_simulate)
    first = sim.snapshot(view, root, config)
    second = sim.snapshot(copy.deepcopy(view), root, config)
    assert first == second
    assert len(calls) == 1
    stored = root / "simulations" / f"{first['snapshot_id']}.json"
    stored.write_text(json.dumps({**json.loads(stored.read_text()), "status": "CORRUPTED"}))
    with pytest.raises(ValueError, match="SIMULATION_HASH_MISMATCH"):
        sim.snapshot(view, root, config)


def test_material_schedule_and_outcome_changes_refresh_snapshot(tmp_path, monkeypatch):
    root, config, view = snapshot_fixture(tmp_path)
    install_snapshot_dependencies(monkeypatch)
    monkeypatch.setattr(
        sim,
        "simulate",
        lambda received_view, cfg: {
            "status": "COMPLETE",
            "cutoff": received_view["updated_at"],
            "schedule": received_view["games"][0]["schedule_version"],
            "outcomes": received_view["games"][0]["outcomes"],
        },
    )
    original = sim.snapshot(view, root, config)
    changed_schedule = copy.deepcopy(view)
    changed_schedule["games"][0]["schedule_version"] = "schedule-v2"
    schedule_result = sim.snapshot(changed_schedule, root, config)
    changed_outcome = copy.deepcopy(changed_schedule)
    changed_outcome["games"][0]["outcomes"] = [
        {"status": "FINAL", "home_score": 24, "away_score": 17}
    ]
    outcome_result = sim.snapshot(changed_outcome, root, config)
    assert len({original["snapshot_id"], schedule_result["snapshot_id"], outcome_result["snapshot_id"]}) == 3


def test_history_source_change_refreshes_snapshot(tmp_path, monkeypatch):
    root, config, view = snapshot_fixture(tmp_path)
    install_snapshot_dependencies(monkeypatch)
    monkeypatch.setattr(sim, "simulate", lambda received_view, cfg: {"status": "COMPLETE"})
    original = sim.snapshot(view, root, config)
    replacement = b"season,game_type,home_score,away_score\n2025,REG,31,10\n"
    replacement_hash = hashlib.sha256(replacement).hexdigest()
    (root / "raw" / f"{replacement_hash}.csv").write_bytes(replacement)
    changed = copy.deepcopy(view)
    changed["sources"]["nflverse_history"]["raw_sha256"] = replacement_hash
    assert sim.snapshot(changed, root, config)["snapshot_id"] != original["snapshot_id"]
