from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    code_root: Path
    data_root: Path


def load_app_config(path: Path) -> AppConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    code_root = Path(__file__).resolve().parents[2]
    environment_data_root = os.environ.get("NFL_PREDICTOR_DATA_DIR")
    if environment_data_root is not None:
        data_root = Path(environment_data_root)
        if not data_root.is_absolute():
            raise ValueError("NFL_PREDICTOR_DATA_DIR must be absolute")
    else:
        data_root = Path(raw["paths"]["data_root"])
        if not data_root.is_absolute():
            data_root = path.resolve().parent / data_root
    data_root = data_root.resolve()
    if data_root == code_root or code_root in data_root.parents:
        raise ValueError("private data_root must be outside code_root")
    return AppConfig(code_root=code_root, data_root=data_root)
