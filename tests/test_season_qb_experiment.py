import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
experiment = importlib.import_module("season_qb_experiment")


def saved(root, raw, suffix="json"):
    path = root / (hashlib.sha256(raw).hexdigest() + "." + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def inputs(tmp_path):
    root = tmp_path / "probability"
    history = saved(root, b"historical fixture", "csv")
    config = (Path(__file__).parents[1] / "configs/season_probability.toml").read_bytes()
    freeze = saved(
        root / "freezes",
        json.dumps(
            {
                "source_path": str(history),
                "source_sha256": history.stem,
                "production_policy_sha256": "f" * 64,
                "config": config.decode(),
                "config_sha256": experiment.sha(config),
            }
        ).encode(),
    )
    player = saved(root, b"player fixture", "parquet")
    baseline = saved(root, b"", "jsonl")
    report = saved(
        root / "reports",
        json.dumps(
            {
                "candidate_rows_paths": {
                    name: str(baseline)
                    for name in ("production_elo", "production_elo_sigmoid", "tuned_elo")
                }
            }
        ).encode(),
    )
    return {
        "output_root": tmp_path / "output",
        "probability_freeze": freeze,
        "probability_report": report,
        "baseline_rows": baseline,
        "player_stats": player,
        "player_stats_sha256": player.stem,
        "qb_config": Path(__file__).parents[1] / "configs/season_qb.toml",
    }


@pytest.mark.parametrize(
    "target,error",
    [
        ("probability_freeze", "PROBABILITY_FREEZE_HASH_MISMATCH"),
        ("player_stats", "PLAYER_SOURCE_CHANGED"),
        ("baseline_rows", "BASELINE_ROWS_HASH_MISMATCH"),
    ],
)
def test_changed_preserved_inputs_fail_before_freeze(tmp_path, target, error):
    args = inputs(tmp_path)
    args[target].write_bytes(b"changed")
    with pytest.raises(ValueError, match=error):
        experiment.run_experiment(**args)
    assert not list(args["output_root"].glob("freezes/*"))


def test_exact_sources_and_freeze_exist_before_data_preparation(tmp_path, monkeypatch):
    args = inputs(tmp_path)
    expected_source = (experiment.repo / "ops/season_qb.py").read_bytes()

    def stop_before_preparation(*_):
        freezes = list(args["output_root"].glob("freezes/*.json"))
        assert len(freezes) == 1
        freeze = json.loads(freezes[0].read_bytes())
        assert experiment.sha(freezes[0].read_bytes()) == freezes[0].stem
        assert Path(freeze["fitted_source_path"]).read_bytes() == expected_source
        assert Path(freeze["player_stats_path"]).read_bytes() == b"player fixture"
        assert freeze["promotion_eligible"] is False
        assert freeze["historical_qb_identity"].startswith("Retrospective")
        bundle = Path(freeze["source_bundle_path"])
        assert experiment.sha(bundle.read_bytes()) == freeze["source_bundle_sha256"]
        assert (
            json.loads(bundle.read_bytes())["files"]["ops/season_qb.py"] == expected_source.decode()
        )
        raise RuntimeError("PREPARATION_BOUNDARY_VERIFIED")

    monkeypatch.setattr(experiment.probability, "historical_games", stop_before_preparation)
    with pytest.raises(RuntimeError, match="PREPARATION_BOUNDARY_VERIFIED"):
        experiment.run_experiment(**args)
    assert not list(args["output_root"].glob("reports/*"))


def test_cached_run_verifies_outputs_and_identity(tmp_path):
    result = saved(tmp_path, b'{"coefficient": 1.0}')
    output = saved(tmp_path, b"preserved output", "txt")
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps(
            {
                "identity": "same",
                "result_path": str(result),
                "result_sha256": result.stem,
                "files": [{"path": str(output), "sha256": output.stem}],
            }
        )
    )
    assert experiment._reuse(index, "same") == {"coefficient": 1.0}
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        experiment._reuse(index, "changed")
    output.write_text("corrupted")
    with pytest.raises(ValueError, match="OUTPUT_HASH_MISMATCH"):
        experiment._reuse(index, "same")
    assert experiment._reuse(tmp_path / "new-identity.json", "changed") is None
