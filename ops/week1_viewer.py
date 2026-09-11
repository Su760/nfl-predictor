"""Loopback-only viewer exposing summaries, never raw data or a filesystem browser."""

import json
import re
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ASSETS = Path(__file__).with_name("viewer")
GAME_ROUTE = re.compile(r"/(?:api/)?games/([A-Za-z0-9_-]+)\Z")


def static_response(path):
    """Explicit asset allowlist: never translate request paths to disk paths."""
    assets = {"/": ("index.html", "text/html; charset=utf-8"),
              "/performance": ("index.html", "text/html; charset=utf-8"),
              "/app.js": ("app.js", "text/javascript; charset=utf-8"),
              "/styles.css": ("styles.css", "text/css; charset=utf-8")}
    if GAME_ROUTE.fullmatch(path) and not path.startswith("/api/"):
        path = "/"
    if path not in assets:
        return None
    name, kind = assets[path]
    return (ASSETS / name).read_bytes(), kind, 200


def main():
    from week1_live import configuration

    cfg, root = configuration()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            asset = static_response(self.path)
            if asset is not None:
                content, kind, code = asset
            elif self.path == "/health":
                content, kind, code = b'{"status":"ok"}', "application/json", 200
            elif self.path == "/api/season" or (self.path.startswith("/api/") and GAME_ROUTE.fullmatch(self.path)):
                try:
                    from season_live import configuration as season_configuration

                    season_cfg, season_root = season_configuration()
                    data = json.loads((season_root / "view.json").read_text())
                    worker = json.loads((season_root / "worker.json").read_text())
                    age = (
                        datetime.now(UTC) - datetime.fromisoformat(worker["checked_at"])
                    ).total_seconds()
                    if age > 2 * season_cfg["poll_seconds"]:
                        worker = {**worker, "status": "STALE / OFFLINE"}
                    data["worker"] = worker
                    if self.path != "/api/season":
                        from season_analysis import game_detail

                        game_id = GAME_ROUTE.fullmatch(self.path).group(1)
                        if not any(g["game_id"] == game_id for g in data["games"]):
                            self.send_error(404, "Game not found")
                            return
                        data = game_detail(data, game_id, season_root, season_cfg)
                    content, kind, code = (
                        json.dumps(data, allow_nan=False).encode(),
                        "application/json",
                        200,
                    )
                except (OSError, ValueError):
                    content, kind, code = (
                        b'{"error":"Season worker has no valid view yet"}',
                        "application/json",
                        503,
                    )
            elif self.path == "/api/week1":
                try:
                    data = json.loads((root / "view.json").read_text())
                    worker = (
                        json.loads((root / "worker.json").read_text())
                        if (root / "worker.json").exists()
                        else None
                    )
                    if (
                        worker
                        and (
                            datetime.now(UTC) - datetime.fromisoformat(worker["checked_at"])
                        ).total_seconds()
                        > 2 * cfg["poll_seconds"]
                    ):
                        worker = {**worker, "status": "STALE / OFFLINE"}
                    result = {
                        "games": data["games"],
                        "capture": data["capture"],
                        "history_through_season": data["model"]["history_through_season"],
                        "worker": worker,
                    }
                    content, kind, code = (
                        json.dumps(result, allow_nan=False).encode(),
                        "application/json",
                        200,
                    )
                except (OSError, ValueError):
                    content, kind, code = (
                        b'{"error":"No valid forecast view available yet"}',
                        "application/json",
                        503,
                    )
            else:
                content, kind, code = b"Not found", "text/plain", 404
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

    if cfg["bind"] != "127.0.0.1":
        raise ValueError("PRIVATE_LOOPBACK_BIND_REQUIRED")
    print("Viewer: http://127.0.0.1:" + str(cfg["port"]), flush=True)
    ThreadingHTTPServer((cfg["bind"], cfg["port"]), Handler).serve_forever()


if __name__ == "__main__":
    main()
