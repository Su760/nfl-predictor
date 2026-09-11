"""Loopback-only viewer exposing summaries, never raw data or a filesystem browser."""

import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from week1_live import configuration

HTML = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NFL · Week 1</title>
<style>body{margin:0;background:#0d1724;color:#e7edf5;font:15px system-ui}main{max-width:1400px;margin:auto;padding:36px 24px}h1{font-size:38px;margin:8px 0}h2{font-size:18px}small,.muted{color:#aab9cb}.pill{display:inline-block;border:1px solid #66788f;border-radius:20px;padding:5px 12px;margin:8px 8px 0 0}.notice{background:#182a40;border-left:3px solid #58c6b0;padding:15px;margin:24px 0;line-height:1.6}table{width:100%;border-collapse:collapse;white-space:nowrap}th{text-align:left;color:#aab9cb;font-size:12px;text-transform:uppercase;letter-spacing:.06em;padding:14px 10px}td{border-top:1px solid #27394f;padding:17px 10px}tr:hover{background:#17283b}.good{color:#71e0b4}.warn{color:#ffd28c}.scroll{overflow:auto}a{color:#86c9ff}.foot{line-height:1.7;margin-top:24px;font-size:13px}</style>
<main><small>NFL PREDICTOR · 2026</small><h1>Week 1, on the record.</h1><div class="muted">Real generation times. Pregame predictions only. All displayed times are Central.</div>
<div class="notice"><b>Football baseline · unpromoted, uncalibrated Elo</b><br>Uses final game results through 2025 and the freshly retrieved 2026 schedule. No current injuries, lineups, weather or odds are included. The full V2 champion is blocked because its reviewed registry and input dataset are absent.</div>
<div id="summary">Loading current records…</div><p id="fresh" class="muted"></p><div class="scroll"><table><thead><tr><th>Matchup</th><th>Kickoff · Central</th><th>Predicted winner</th><th>Win probability</th><th>Generated · Central</th><th>Input captured · Central</th><th>Record</th><th>T−72h</th><th>T−60m</th></tr></thead><tbody id="rows"></tbody></table></div>
<div class="foot"><b>How to read this:</b> ON_DEMAND is a pregame forecast generated outside the fixed scheduled origins; it is never presented as T−72h or T−60m. MISSED means no verified prediction exists for that origin. Yesterday's game has no located pregame artifact and receives no retroactive prediction. Probabilities include a historical tie allowance.<p id="worker"></p><p>Local automatic runs require this Mac to stay awake and connected. Private cloud automation remains blocked until its repository, trusted code SHA and zero-paid-use allowance are configured. No wagering or paid API calls.</p><a href="/api/week1">Actual prediction JSON</a> · <a href="https://www.nfl.com/schedules/2026/by-week/week-1" target="_blank" rel="noreferrer">Official schedule</a></div></main>
<script>
const format=v=>v?new Intl.DateTimeFormat('en-US',{timeZone:'America/Chicago',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit',timeZoneName:'short'}).format(new Date(v)):'—';
function cell(tr,value,cl){const e=document.createElement('td');e.textContent=value;if(cl)e.className=cl;tr.append(e)}
async function refresh(){try{const r=await fetch('/api/week1',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error);const rows=document.querySelector('#rows');rows.replaceChildren();let n=0;for(const g of d.games){const p=g.prediction;if(p)n++;const tr=document.createElement('tr');cell(tr,g.away+' @ '+g.home+(p?.neutral_site?' · neutral':''));cell(tr,format(g.kickoff));cell(tr,p?.predicted_winner||'—',p?'good':'warn');cell(tr,p?(100*p.win_probability).toFixed(1)+'%':'—');cell(tr,format(p?.generated_at));cell(tr,format(p?.capture?.captured_at));cell(tr,p?p.origin:g.status,p?'good':'warn');cell(tr,g.origins.T72,g.origins.T72==='MISSED'?'warn':'');cell(tr,g.origins.T60,g.origins.T60==='MISSED'?'warn':'');rows.append(tr)}document.querySelector('#summary').textContent=n+' pregame forecasts recorded · '+(d.games.length-n)+' missing / missed · $0 paid usage';document.querySelector('#fresh').textContent='Schedule/results captured '+format(d.capture.captured_at)+' · '+Math.max(0,Math.round((Date.now()-Date.parse(d.capture.captured_at))/60000))+' minutes ago · Historical results through '+d.history_through_season+'. Generation time is shown separately for each saved forecast.';document.querySelector('#worker').textContent='Worker: '+(d.worker?.status||'NOT RUNNING')+' · last heartbeat '+format(d.worker?.checked_at)+'.';}catch(e){document.querySelector('#summary').textContent='BLOCKED: '+e.message}}refresh();setInterval(refresh,30000);
</script></html>"""


def main():
    cfg, root = configuration()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                content, kind, code = HTML.encode(), "text/html; charset=utf-8", 200
            elif self.path == "/health":
                content, kind, code = b'{"status":"ok"}', "application/json", 200
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
            self.end_headers()
            self.wfile.write(content)

    if cfg["bind"] != "127.0.0.1":
        raise ValueError("PRIVATE_LOOPBACK_BIND_REQUIRED")
    print("Viewer: http://127.0.0.1:" + str(cfg["port"]), flush=True)
    ThreadingHTTPServer((cfg["bind"], cfg["port"]), Handler).serve_forever()


if __name__ == "__main__":
    main()
