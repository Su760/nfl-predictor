import pytest

from nfl_predictor.contracts.serialization import canonical_feature_bytes, sha256_hex


def test_canonical_bytes_normalize_negative_zero_and_reject_nan() -> None:
    schema = (("elo_diff", "float64"), ("neutral_site", "bool"))
    assert canonical_feature_bytes(schema, {"elo_diff": -0.0, "neutral_site": False}) == (
        canonical_feature_bytes(schema, {"elo_diff": 0.0, "neutral_site": False})
    )
    assert len(sha256_hex(b"feature-vector")) == 64


def test_canonical_feature_bytes_rejects_non_finite_float64() -> None:
    with pytest.raises(ValueError, match="elo_diff must be finite"):
        canonical_feature_bytes((("elo_diff", "float64"),), {"elo_diff": float("nan")})
