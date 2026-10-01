"""Preservation checks use saved reports, never rerun the consumed experiment."""
import hashlib
import json
import shutil

import pytest

from fantasy.restore import ARTIFACTS, BUNDLE, restore, verified_files
from fantasy.server import opportunity_method, payload


def test_restore_original_bytes_and_api_acceptance(tmp_path, monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError("Preservation must not fit or evaluate plays")

    for name in ("fit", "season_records", "evaluate", "uncertainty", "run"):
        monkeypatch.setattr("fantasy.evaluate." + name, prohibited)
    files = verified_files(BUNDLE)
    cache = tmp_path / "cache"
    status = restore(cache)
    assert status["display_supported"]
    for name in ARTIFACTS:
        assert (cache / "expected-points" / name).read_bytes() == files[name]
    before = {n: (cache / "expected-points" / n).stat().st_mtime_ns for n in ARTIFACTS}
    assert restore(cache) == status
    assert before == {n: (cache / "expected-points" / n).stat().st_mtime_ns for n in ARTIFACTS}
    accepted, model = opportunity_method({"cache": cache})
    assert model is not None and accepted["display_supported"]
    api = payload({"cache": cache, "season": 2026, "forecast_url": "http://127.0.0.1:8510",
                   "stale_after_hours": 24}, "last_game")
    assert api["opportunity_method"]["display_supported"]
    assert api["state"] == "unavailable"  # A model does not synthesize usage rows.


def test_receipts_match_sealed_reports_and_public_sources():
    files = verified_files(BUNDLE)
    freeze, holdout = [json.loads(files[n + ".json"]) for n in ("freeze", "holdout")]
    receipts = {**freeze["receipts"], **holdout["receipts"]}
    assert set(receipts) == {str(s) for s in range(2019, 2026)}
    for season, sources in receipts.items():
        assert set(sources) == {"players", "pbp"}
        for name, receipt in sources.items():
            assert json.loads(files[f"receipts/{season}-{name}.json"]) == receipt
            assert receipt["url"].startswith("https://github.com/nflverse/nflverse-data/releases/download/")
    manifest = json.loads(files["manifest.json"])
    assert manifest["sealed_model_sha256"] == freeze["model_sha256"] == holdout["model_sha256"]


@pytest.mark.parametrize("name", ARTIFACTS + ("manifest.json", "receipts/2019-pbp.json"))
def test_corruption_refused_before_destination_created(tmp_path, name):
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    with (bundle / name).open("ab") as output:
        output.write(b" ")
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="Checksum mismatch"):
        restore(cache, bundle)
    assert not cache.exists()


def test_divergent_local_evidence_not_overwritten(tmp_path):
    root = tmp_path / "expected-points"
    root.mkdir()
    (root / "model.json").write_bytes(b"local evidence")
    with pytest.raises(ValueError, match="Existing artifacts differ"):
        restore(tmp_path)
    assert (root / "model.json").read_bytes() == b"local evidence"
    assert list(root.iterdir()) == [root / "model.json"]


def test_modified_method_rejected_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr("fantasy.server.method_hash", lambda: "different-method")
    with pytest.raises(ValueError, match="API integrity check rejected"):
        restore(tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_incompatible_repository_refused(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    for name in json.loads((BUNDLE / "manifest.json").read_text())["repository_files"]:
        target = repository / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"changed source")
    monkeypatch.setattr("fantasy.restore.ROOT", repository)
    with pytest.raises(ValueError, match="Incompatible repository file"):
        restore(tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_inventory_and_traversal_rejected(tmp_path):
    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    (bundle / "extra.json").write_text("{}")
    with pytest.raises(ValueError, match="inventory"):
        verified_files(bundle)
    (bundle / "SHA256SUMS").write_text(hashlib.sha256(b"x").hexdigest() + "  ../escape\n")
    with pytest.raises(ValueError, match="Invalid checksum entry"):
        verified_files(bundle)
