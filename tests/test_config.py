from pathlib import Path

import pytest

from nfl_predictor.config import load_app_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]



def test_environment_data_root_overrides_toml(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "base.toml"
    config_path.write_text('[paths]\ndata_root = "from-file"\n', encoding="utf-8")
    expected = tmp_path / "private-data"
    monkeypatch.setenv("NFL_PREDICTOR_DATA_DIR", str(expected))

    config = load_app_config(config_path)

    assert config.data_root == expected.resolve()
    assert config.code_root.name == "nfl-predictor"


# Catches ambient process state redirecting a caller that supplied an explicit environment.
def test_explicit_environment_isolated_from_ambient_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured"
    explicit = tmp_path / "explicit"
    ambient = tmp_path / "ambient"
    config_path = tmp_path / "base.toml"
    config_path.write_text(
        f'[paths]\ndata_root = "{configured}"\n', encoding="utf-8"
    )
    monkeypatch.setenv("NFL_PREDICTOR_DATA_DIR", str(ambient))

    config = load_app_config(
        config_path, environment={"NFL_PREDICTOR_DATA_DIR": str(explicit)}
    )

    assert config.data_root == explicit.resolve()


def test_config_rejects_relative_environment_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "base.toml"
    config_path.write_text('[paths]\ndata_root = "from-file"\n', encoding="utf-8")
    monkeypatch.setenv("NFL_PREDICTOR_DATA_DIR", "data")

    with pytest.raises(ValueError, match="absolute"):
        load_app_config(config_path)


def test_config_rejects_data_root_inside_code_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "base.toml"
    config_path.write_text('[paths]\ndata_root = "from-file"\n', encoding="utf-8")
    monkeypatch.setenv("NFL_PREDICTOR_DATA_DIR", str(PROJECT_ROOT / "data"))

    with pytest.raises(ValueError, match="outside code_root"):
        load_app_config(config_path)
