from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import math

import pytest

from fantasy import receiving_grade as grade


def archive_entry():
    rows = []
    for i, pos, usage, hours in [(1, "WR", "low", 12), (2, "TE", "high", 60)]:
        values = dict(targets=i + 1, receptions=i, receiving_yards=10 if i == 1 else 30)
        rolling = dict(targets=4, receptions=i + 1, receiving_yards=20 if i == 1 else 50)
        rows.append(dict(player_id=f"00-000000{i}", game_id=f"g{i}", name=pos, team="A", position=pos,
                         tier=usage, cutoff="2026-09-30T00:00:00+00:00",
                         kickoff=f"2026-{'09-30T12' if i == 1 else '10-02T12'}:00:00+00:00",
                         lead_hours=hours, estimate=values, rolling=rolling,
                         interval={k: dict(low=max(0, v - 1), high=v + 1) for k, v in values.items()},
                         rolling_interval={k: dict(low=max(0, v - 2), high=v + 2) for k, v in rolling.items()}))
    rows[0]["interval"]["targets"]["low"] = 0
    return dict(id="primary", primary=True, locations=["fixture.json"], archive_sha256="fixture",
                archive=dict(rows=rows, season=2026, week=4, generated_at="2026-09-30T00:00:00+00:00",
                             input_cutoff="2026-09-30T00:00:00+00:00", archived_at="2026-09-30T00:00:01+00:00",
                             sources={}, model_sha256="saved-model", method_sha256="saved-method"))


def outcomes():
    return dict(captured_at="2026-10-06T12:00:00+00:00",
                games=[dict(game_id=f"g{i}", completed=True, teams=["A", "B"]) for i in (1, 2)],
                rows=[dict(player_id="00-0000001", game_id="g1", team="A", targets=0, receptions=0, receiving_yards=0),
                      dict(player_id="00-0000002", game_id="g2", team="A", targets=6, receptions=4, receiving_yards=60)])


def test_saved_estimates_paired_math_zero_and_horizon_not_recomputed_predictions():
    result = grade.grade_archive(archive_entry(), outcomes(), grade.settings())
    all_rows = result["groups"]["ALL"]
    assert result["complete"] and all_rows["observed"] == 2
    a, b = [all_rows["models"][k]["targets"] for k in ("model", "rolling")]
    assert a["mae"] == 2.5 and a["rmse"] == pytest.approx(math.sqrt(6.5)) and a["bias"] == -.5
    assert b["mae"] == 3 and b["rmse"] == pytest.approx(math.sqrt(10)) and b["bias"] == 1
    assert a["interval_coverage"] == .5 and a["interval_width"] == 2.5
    assert a["n"] == b["n"] == 2
    assert result["records"][0]["actual"]["targets"] == 0
    assert "horizon/0–<24h" in result["groups"] and "horizon/48–<72h" in result["groups"]
    assert result["groups"]["position/WR"]["observed"] == 1
    assert result["groups"]["tier/high"]["observed"] == 1


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), True, -1, 1.5])
def test_invalid_components_never_become_zero(bad):
    data = outcomes()
    data["rows"][0]["targets"] = bad
    result = grade.grade_archive(archive_entry(), data, grade.settings())
    assert result["groups"]["ALL"]["invalid"] == 1
    assert not result["complete"] and result["records"][0]["actual"] is None
    assert result["groups"]["ALL"]["models"]["model"]["targets"]["n"] == 1


def test_pending_missing_invalid_duplicates_and_unavailable_intervals():
    data = outcomes()
    data["games"][0]["completed"] = False
    data["rows"] = []
    result = grade.grade_archive(archive_entry(), data, grade.settings())
    assert result["groups"]["ALL"]["pending"] == 1 and result["groups"]["ALL"]["missing"] == 1
    assert result["groups"]["ALL"]["models"]["model"]["targets"]["mae"] is None
    assert "DNP/no-stat/unknown" in result["records"][1]["reason"]
    data = outcomes()
    data["rows"].append(deepcopy(data["rows"][0]))
    result = grade.grade_archive(archive_entry(), data, grade.settings())
    assert result["records"][0]["status"] == "invalid" and "Duplicate" in result["records"][0]["reason"]
    entry = archive_entry()
    entry["archive"]["rows"][0]["interval"]["targets"] = None
    result = grade.grade_archive(entry, outcomes(), grade.settings())
    stats = result["groups"]["ALL"]["models"]["model"]["targets"]
    assert stats["n"] == 2 and stats["interval_n"] == 1 and stats["interval_missing"] == 1


def test_outcome_completion_uses_eastern_day_no_same_day_live_zero():
    captured = datetime(2026, 10, 6, 1, tzinfo=UTC)  # Still Monday in Eastern.
    players = [dict(season=2026, season_type="REG", player_id="p", game_id="g", team="A",
                    targets=float("nan"), receptions=0, receiving_yards=0)]
    games = [dict(season="2026", game_type="REG", game_id="g", gameday="2026-10-05",
                  home_score="0", away_score="0", away_team="A", home_team="B")]
    rows, normalized = grade.normalize_outcomes(players, games, 2026, captured)
    assert not normalized[0]["completed"] and rows[0]["targets"] is None
    assert rows[0]["invalid_fields"] == ["targets"]
    _, normalized = grade.normalize_outcomes(players, games, 2026, datetime(2026, 10, 6, 12, tzinfo=UTC))
    assert normalized[0]["completed"]  # Real numeric score zeros are not missing.


def setup_run(tmp_path, monkeypatch):
    monkeypatch.setattr(grade, "ROOT", tmp_path)
    root = tmp_path / "bundle"
    root.mkdir()
    item = archive_entry()["archive"]
    raw = json.dumps(item).encode()
    (root / "first-prospective.json").write_bytes(raw)
    (root / "SHA256SUMS").write_text(hashlib.sha256(raw).hexdigest()+"  first-prospective.json\n")
    monkeypatch.setattr(grade, "settings", lambda: dict(primary_archive="bundle/first-prospective.json", primary_forecasts=2,
                                                      horizon_edges_hours=[24, 48, 72]))
    cfg = dict(cache=tmp_path / "cache", season=2026, stale_after_hours=24, sources=dict(players="players", schedule="schedule"))
    def fetcher(name, url, config):
        source = ([dict(season=2026, season_type="REG", **r) for r in outcomes()["rows"]] if name == "players" else
                  [dict(season="2026", game_type="REG", game_id=f"g{i}", gameday="2026-09-28", away_team="A",
                        home_team="B", away_score="0", home_score="1") for i in (1, 2)])
        return source, dict(name=name, sha256=hashlib.sha256(json.dumps(source).encode()).hexdigest(),
                            captured_at="2026-09-29T00:00:00+00:00", provider_modified_at=None)
    return cfg, fetcher, raw


def test_refresh_without_future_games_preserves_forecasts_and_correction_versions(tmp_path, monkeypatch):
    cfg, fetcher, original = setup_run(tmp_path, monkeypatch)
    for module, name in [("fantasy.receiving_data", "build"), ("fantasy.receiving", "forecast_game"),
                         ("fantasy.receiving", "fit_priors"), ("fantasy.receiving_eval", "run")]:
        monkeypatch.setattr(module + "." + name, lambda *a, **k: pytest.fail("Forbidden prediction/experiment"))
    card, first = grade.run(refresh=True, week=4, cfg=cfg, fetcher=fetcher)
    root = cfg["cache"] / "receiving/grading"
    bytes_before = (root / "scorecards" / first["file"]).read_bytes()
    def corrected(name, *a):
        rows, receipt = fetcher(name, *a)
        if name == "players":
            rows[0]["receiving_yards"] = -2
            receipt["sha256"] = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
        return rows, receipt
    later, saved = grade.run(refresh=True, week=4, cfg=cfg, fetcher=corrected)
    assert first != saved and (root / "scorecards" / first["file"]).read_bytes() == bytes_before
    assert later["source_revision"] == 2 and later["source_bytes_changed"]
    assert later["previous_capture"] == card["outcome_capture"]["file"]
    assert (tmp_path / "bundle/first-prospective.json").read_bytes() == original
    assert len(list((root / "outcomes").glob("*.json"))) == 2
    loaded = grade.saved_scorecard(cfg, first["file"])
    assert loaded["source_revision"] == 1 and len(loaded["captures"]) == 2
    assert grade.saved_scorecard(cfg, "../secrets")["state"] == "unavailable"


def test_other_versions_separate_primary_fixed_and_identical_copies_deduplicated(tmp_path, monkeypatch):
    cfg, _, raw = setup_run(tmp_path, monkeypatch)
    root = cfg["cache"] / "receiving/forecasts"
    root.mkdir(parents=True)
    (root / "same.json").write_bytes(raw)
    different = json.loads(raw)
    different["rows"][0]["estimate"]["targets"] = 999
    (root / "later.json").write_text(json.dumps(different))
    (root / "bad.json").write_text("corrupt")
    (root / ("20261002T000000000000Z-" + "0" * 64 + ".json")).write_bytes(raw)
    invalid = deepcopy(different)
    invalid["rows"][0]["game_id"] = None
    (root / "missing-id.json").write_text(json.dumps(invalid))
    entries, rejected = grade.load_archives(cfg, grade.settings())
    assert len(entries) == 2 and sum(v["primary"] for v in entries) == 1 and len(rejected) == 3
    assert len(entries[0]["locations"]) == 2
    assert entries[0]["archive"]["rows"][0]["estimate"]["targets"] == 2
    assert grade.grade_archive(entries[1], outcomes(), grade.settings())["primary"] is False


def test_failed_refresh_retains_latest_and_tampering_is_not_silently_accepted(tmp_path, monkeypatch):
    cfg, fetcher, _ = setup_run(tmp_path, monkeypatch)
    _, receipt = grade.run(refresh=True, cfg=cfg, fetcher=fetcher)
    root = cfg["cache"] / "receiving/grading"
    pointer = (root / "latest.json").read_bytes()
    def fail(*a):
        raise ValueError("Source unavailable")
    with pytest.raises(ValueError, match="Source unavailable"):
        grade.run(refresh=True, cfg=cfg, fetcher=fail)
    assert (root / "latest.json").read_bytes() == pointer
    with (root / "scorecards" / receipt["file"]).open("ab") as output:
        output.write(b" ")
    assert grade.saved_scorecard(cfg)["state"] == "unavailable"


def test_real_primary_is_preserved_273_and_readonly():
    cfg = dict(cache=grade.ROOT / ".fantasy-cache", season=2026)
    entries, _ = grade.load_archives(cfg, grade.settings())
    assert entries[0]["primary"] and len(entries[0]["archive"]["rows"]) == 273


def test_duplicate_games_and_unavailable_paired_estimates():
    data = outcomes()
    data["games"].append(deepcopy(data["games"][0]))
    result = grade.grade_archive(archive_entry(), data, grade.settings())
    assert result["records"][0]["status"] == "invalid"
    entry = archive_entry()
    entry["archive"]["rows"][0]["estimate"] = None
    entry["archive"]["rows"][1]["rolling"]["targets"] = None
    result = grade.grade_archive(entry, outcomes(), grade.settings())
    assert result["groups"]["ALL"]["invalid_forecast"] == 2
    assert result["groups"]["ALL"]["models"]["rolling"]["targets"]["mae"] is None


def test_scorecard_api_is_readonly_and_independent_personal_outcomes_are_honest(tmp_path, monkeypatch):
    from fantasy import server
    cfg, fetcher, _ = setup_run(tmp_path, monkeypatch)
    _, first = grade.run(refresh=True, cfg=cfg, fetcher=fetcher)
    def forbidden(*a, **k):
        pytest.fail("Read-only API attempted capture/forecast/experiment")
    monkeypatch.setattr(grade, "capture_outcomes", forbidden)
    monkeypatch.setattr(server, "saved_sheet", lambda cfg: {"state": "available", "rows": []})
    root = cfg["cache"] / "receiving/grading"
    before = sorted(str(p) for p in root.rglob("*"))
    response = server.response("/api/fantasy/receiving-grade?capture=" + first["file"], cfg)
    assert json.loads(response[0])["versions"][0]["groups"]["ALL"]["forecasted"] == 2
    assert json.loads(server.response("/api/fantasy/receiving-grade?capture=../bad", cfg)[0])["state"] == "unavailable"
    sheet = json.loads(server.response("/api/fantasy/receiving", cfg)[0])
    assert sheet["outcomes"]["rows"][0]["status"] == "observed"
    assert sheet["outcomes"]["rows"][0]["targets"] == 0
    assert sorted(str(p) for p in root.rglob("*")) == before
    pointer = json.loads((root / "outcome-latest.json").read_text())
    capture = grade.read_version(root / "outcomes", pointer["file"])
    capture["games"].append(deepcopy(capture["games"][0]))
    revised = grade.write_version(root / "outcomes", capture)
    grade.atomic_json(root / "outcome-latest.json", revised)
    assert grade.latest_personal_outcomes(cfg)["rows"][0]["status"] != "observed"
    with (root / "outcomes" / revised["file"]).open("ab") as output:
        output.write(b" ")
    sheet = json.loads(server.response("/api/fantasy/receiving", cfg)[0])
    assert sheet["outcomes"]["state"] == "unavailable" and sheet["outcomes"]["rows"] == []
