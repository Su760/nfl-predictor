"""Read-only loopback preview. No forecasting imports, workers or source refreshes."""
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from fantasy.config import ROOT, configuration
from fantasy.evaluate import gate, method_hash
from fantasy.points import fingerprint, settings, summarize_quality
from fantasy.quality import DEFINITIONS
from fantasy.receiving_data import saved_sheet
from fantasy.receiving_grade import latest_personal_outcomes, saved_scorecard
from fantasy.trends import weekly_trends
from fantasy.usage import METRICS, WINDOW_NOTE, summarize


def opportunity_method(cfg):
    status = {"display_supported": False, "status": "Not evaluated / artifact unavailable"}
    try:
        root = cfg["cache"] / "expected-points"
        freeze, validation, holdout, model = [json.loads((root / (name + ".json")).read_text())
                                              for name in ["freeze", "validation", "holdout", "model"]]
        if (freeze["method_sha256"] != method_hash() or model["config"] != settings()
                or fingerprint(model) != freeze["model_sha256"]
                or fingerprint(validation) != freeze["validation_sha256"]
                or holdout["model_sha256"] != freeze["model_sha256"]):
            raise ValueError("Method artifacts do not match the sealed evaluation")
        failures = gate(validation, model["config"]) + gate(holdout, model["config"])
        supported = not failures
        status = {"display_supported": supported,
                  "status": "Retrospective baseline passed fixed gates" if supported else "Research only: fixed gates failed",
                  "failures": failures, "frozen_at": freeze["frozen_at"],
                  "model_sha256": freeze["model_sha256"], "train_seasons": model["train_seasons"],
                  "validation": validation, "holdout": {k: holdout[k] for k in ["coverage", "scores", "uncertainty"]}}
        return status, model if supported else None
    except (OSError, ValueError, KeyError, TypeError) as error:
        status["detail"] = str(error)
        return status, None


def payload(cfg, window):
    method, model = opportunity_method(cfg)
    policy = settings()
    common = {"season": cfg["season"], "window": window, "window_note": WINDOW_NOTE,
              "definitions": {k: {"label": v[0], "definition": v[1]} for k, v in METRICS.items()},
              "quality_definitions": DEFINITIONS, "opportunity_method": method,
              "scoring": {k: policy[k] for k in ["yard_points", "touchdown_points", "reception_points"]},
              "forecast_url": cfg["forecast_url"], "stale_after_hours": cfg["stale_after_hours"]}
    try:
        data = json.loads((cfg["cache"] / "snapshot.json").read_text())
        if data["season"] != cfg["season"]:
            raise ValueError("Saved snapshot is for a different season")
        players = summarize(data["rows"], data["games"], window)
        for player in players:
            player["quality"] = summarize_quality(player, window, model)
        age = (datetime.now(UTC) - datetime.fromisoformat(data["updated_at"])).total_seconds()
        state = "stale" if age < 0 or age > cfg["stale_after_hours"] * 3600 else "available"
        common.update({k: data[k] for k in ("updated_at", "coverage", "sources")})
        common.update(players=players, state=state, age_hours=round(age / 3600, 1),
                      trends=weekly_trends(data["rows"], data["games"], data.get("schedule", [])))
    except (OSError, ValueError, KeyError, TypeError) as error:
        common.update(players=[], trends={}, state="unavailable", sources={}, coverage={},
                      updated_at=None, error=f"Usage snapshot unavailable: {error}")
    try:
        common["refresh"] = json.loads((cfg["cache"] / "refresh.json").read_text())
    except (OSError, ValueError):
        common["refresh"] = {"status": "not run"}
    return common


def response(path, cfg=None):
    parsed = urlsplit(path)
    assets = {"/fantasy": ("fantasy.html", "text/html; charset=utf-8"),
              "/fantasy.js": ("fantasy.js", "text/javascript; charset=utf-8"),
              "/receiving.js": ("receiving.js", "text/javascript; charset=utf-8"),
              "/receiving-grade.js": ("receiving-grade.js", "text/javascript; charset=utf-8"),
              "/fantasy.css": ("fantasy.css", "text/css; charset=utf-8")}
    if parsed.path in assets:
        name, kind = assets[parsed.path]
        return (ROOT / "ops/viewer" / name).read_bytes(), kind, 200
    if parsed.path == "/api/fantasy/receiving":
        selected = cfg or configuration()
        sheet = saved_sheet(selected)
        outcomes = latest_personal_outcomes(selected)
        if outcomes is not None:
            sheet["outcomes"] = outcomes
        return json.dumps(sheet, allow_nan=False).encode(), "application/json", 200
    if parsed.path == "/api/fantasy/receiving-grade":
        capture = parse_qs(parsed.query).get("capture", [None])[0]
        return json.dumps(saved_scorecard(cfg or configuration(), capture), allow_nan=False).encode(), "application/json", 200
    if parsed.path != "/api/fantasy":
        return None
    window = parse_qs(parsed.query).get("window", ["last3"])[0]
    if window not in {"last", "last3", "season"}:
        return b'{"error":"Invalid window"}', "application/json", 400
    return json.dumps(payload(cfg or configuration(), window), allow_nan=False).encode(), "application/json", 200


def main():
    cfg = configuration()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", "/fantasy")
                self.end_headers()
                return
            result = (b'{"status":"ok","service":"fantasy-preview"}', "application/json", 200) if self.path == "/health" else response(self.path, cfg)
            content, kind, code = result or (b"Not found", "text/plain", 404)
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

    print(f"Player Lab: http://{cfg['bind']}:{cfg['port']}/fantasy", flush=True)
    ThreadingHTTPServer((cfg["bind"], cfg["port"]), Handler).serve_forever()


if __name__ == "__main__":
    main()
