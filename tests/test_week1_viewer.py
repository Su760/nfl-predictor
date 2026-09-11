"""The private viewer serves only explicit application routes and assets."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("week1_viewer", Path(__file__).parents[1] / "ops/week1_viewer.py")
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)
static_response = viewer.static_response


def test_application_routes_and_assets():
    for path in ("/", "/performance", "/games/2026_01_SF_LA"):
        body, kind, status = static_response(path)
        assert status == 200 and kind.startswith("text/html")
        assert b'<main id="app"' in body
    assert static_response("/app.js")[1].startswith("text/javascript")


def test_asset_allowlist_prevents_private_file_access():
    for path in ("/../view.json", "/%2e%2e/view.json", "/styles.css/../view.json",
                 "/games/../view.json", "/games/%2fetc%2fpasswd", "/view.json",
                 "/api/games/2026_01_SF_LA", "/app.js?file=/etc/passwd"):
        assert static_response(path) is None
