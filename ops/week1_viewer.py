"""Loopback-only viewer exposing summaries, never raw data or a filesystem browser."""

import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from week1_live import configuration

HTML = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NFL Forecasts · Season ledger</title>\n<style>body{margin:0;background:#0d1724;color:#e7edf5;font:15px system-ui}main{max-width:1500px;margin:auto;padding:30px 22px}h1{font-size:34px;margin:10px 0}small,.muted{color:#b4c2d4}.notice{background:#192d43;border-left:3px solid #62d7ba;padding:15px;line-height:1.6;margin:20px 0}.cards{display:flex;flex-wrap:wrap;gap:12px}.card{padding:16px;background:#17283b;border-radius:8px;min-width:170px}.card b{display:block;font-size:24px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:13px 9px;border-bottom:1px solid #2b3d51;vertical-align:top}th{color:#aab9cb;font-size:12px;text-transform:uppercase}select{background:#17283b;color:white;border:1px solid #66788f;padding:9px;margin:12px}a{color:#8ed4ff}details{margin:5px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.foot{line-height:1.6;margin:24px 0}.warn{color:#ffd28c}.good{color:#76e5ba}svg{max-width:350px;height:75px}button{background:#203c55;color:white;padding:9px;border:1px solid #66788f;border-radius:5px}</style>\n<main><small>NFL · IMMUTABLE FORECAST LEDGER</small><h1>Forecasts, with a record.</h1><div class="muted">Central Time · Every revision retained · Final results only</div>\n<div class="notice">Current production: <b>season Elo fallback, uncalibrated</b>. Current injuries, expected QBs and weather are shown as evidence; they do not change probabilities until an adjustment is validated. Original Week 1 Elo forecasts keep their identity. <span id="v2"></span></div>\n<div id="health">Loading live state…</div><label>Slate <select id="week"><option value="1">Week 1</option></select></label><label>Scorecard <select id="horizon"><option value="official">Latest valid pregame</option><option>T72</option><option>T60</option><option>FINAL</option></select></label><button id="reload">Refresh</button>\n<div id="cards" class="cards"></div><p id="policy" class="muted"></p><div class="scroll"><table><thead><tr><th>Matchup / kickoff</th><th>Current forecast</th><th>Outcome / coverage</th><th>Inputs / health</th><th>History / origins</th></tr></thead><tbody id="games"></tbody></table></div>\n<details class="foot"><summary>Source check details, model comparisons and calibration</summary><pre id="sources"></pre><pre id="metrics"></pre></details>\n<details class="foot"><summary>Weekly review: wins, misses and input gaps</summary><pre id="analysis"></pre></details>\n<details class="foot" open><summary>Season and playoff probabilities · Elo sensitivity simulation</summary><p id="sim-status"></p><div class="scroll"><table><thead><tr><th>Team</th><th>Playoffs</th><th>Division</th><th>No. 1 seed</th><th>Conference</th><th>Super Bowl</th><th>SB movement</th></tr></thead><tbody id="sim-teams"></tbody></table></div><details><summary>Dated simulation history and limitations</summary><pre id="sim-history"></pre></details></details><p class="foot">Official scoring selects the latest valid pregame production forecast, never the best-performing revision. T−72h, T−60m and final pregame snapshots are scored separately. Ties are excluded from winner accuracy and included in three-outcome Brier score and log loss. Forecast coverage includes missed games. Earlier games whose kickoff preceded the scoring-policy freeze are flagged retrospective-policy.<br>Local automation requires this Mac awake and connected. No paid APIs or betting execution.<br><a href="/api/season">Full season JSON</a> · <a href="/api/week1">Original Week 1 JSON</a></p></main>\n<script>\nlet data;const el=id=>document.getElementById(id),fmt=v=>v?new Intl.DateTimeFormat(\'en-US\',{timeZone:\'America/Chicago\',month:\'short\',day:\'numeric\',hour:\'numeric\',minute:\'2-digit\',second:\'2-digit\',timeZoneName:\'short\'}).format(new Date(v)):\'—\';const pct=x=>x==null?\'—\':(x*100).toFixed(1)+\'%\', num=x=>x==null?\'—\':x.toFixed(3);\nfunction cell(tr,t){const e=document.createElement(\'td\');e.textContent=t;tr.append(e);return e}function detail(parent,title,value){const d=document.createElement(\'details\'),s=document.createElement(\'summary\'),p=document.createElement(\'pre\');s.textContent=title;p.textContent=typeof value===\'string\'?value:JSON.stringify(value,null,2);d.append(s,p);parent.append(d)}\nfunction render(){if(!data)return;let w=el(\'week\').value,h=el(\'horizon\').value;const gs=data.games.filter(g=>w===\'all\'||String(g.week)===w),ids=new Set(gs.map(g=>g.game_id)),sc=data.scorecards;let card=h===\'official\'?(w===\'all\'?sc.summary.season:sc.summary.weekly[w]):(w===\'all\'?sc.horizons[h]:(sc.weekly_horizons?.[h]?.[w]));el(\'cards\').replaceChildren();if(card)for(const [title,value]of [[\'Correct / scored\',(card.correct??0)+\' / \'+card.winner_accuracy_denominator],[\'Accuracy\',pct(card.straight_up_accuracy)],[\'Coverage\',card.forecasted+\' / \'+card.games],[\'Pending / missed\',(card.pending??card.unresolved)+\' / \'+(card.missed_coverage??0)],[\'Brier / log loss\',num(card.multiclass_brier)+\' / \'+num(card.multinomial_log_loss)]]){let c=document.createElement(\'div\');c.className=\'card\';c.textContent=title;let b=document.createElement(\'b\');b.textContent=value;c.append(b);el(\'cards\').append(c)}else el(\'cards\').textContent=\'Horizon totals available in source/metric details.\';\nel(\'policy\').textContent=\'Policy frozen \'+fmt(data.scoring_policy.effective_at)+\'. \'+gs.filter(g=>g.kickoff&&Date.parse(g.kickoff)<=Date.parse(data.scoring_policy.effective_at)).length+\' games in this view started before policy freeze.\';el(\'games\').replaceChildren();for(const g of gs){let tr=document.createElement(\'tr\'),p=g.prediction,s=sc.games.find(x=>x.game_id===g.game_id),o=g.outcomes.at(-1);cell(tr,g.away+\' @ \'+g.home+\'\\n\'+fmt(g.kickoff)+(g.neutral_site?\' · neutral\':\'\'));cell(tr,p?p.predicted_winner+\' \'+pct(p.win_probability)+\'\\n\'+p.model_version+\'\\nGenerated \'+fmt(p.generated_at):g.forecast_status);cell(tr,(o?.status===\'FINAL\'?g.away+\' \'+o.away_score+\' — \'+g.home+\' \'+o.home_score+\' · FINAL\':g.status)+\'\\n\'+(s?.coverage||\'\')+(s?.retrospective_policy?\' · retrospective policy\':\'\'));let inputs=cell(tr,\'\');for(const [key,v]of Object.entries(g.inputs||{})){let d=document.createElement(\'div\');d.textContent=key+\': \'+v.status;inputs.append(d)}detail(inputs,\'Current source evidence\',g.inputs);let hist=cell(tr,Object.entries(g.origins).map(([k,v])=>k+\': \'+v).join(\' · \'));const rs=[...g.predictions].sort((a,b)=>a.generated_at.localeCompare(b.generated_at));if(rs.length>1){const ns=\'http://www.w3.org/2000/svg\',svg=document.createElementNS(ns,\'svg\');svg.setAttribute(\'viewBox\',\'0 0 300 75\');svg.setAttribute(\'aria-label\',\'Home win probability across forecast revisions\');let line=document.createElementNS(ns,\'polyline\');line.setAttribute(\'points\',rs.map((r,i)=>(5+i*290/(rs.length-1))+\',\'+(70-r.p_home*65)).join(\' \'));line.setAttribute(\'fill\',\'none\');line.setAttribute(\'stroke\',\'#76e5ba\');line.setAttribute(\'stroke-width\',\'2\');svg.append(line);hist.append(svg)}detail(hist,rs.length+\' saved revisions · home probability movement\',rs.map(r=>({generated:fmt(r.generated_at),origin:r.origin,model:r.model_version||r.model_label,p_home:r.p_home,p_away:r.p_away,p_tie:r.p_tie,reasons:r.reasons,inputs:r.inputs||r.capture,revision_id:r.revision_id,code:r.code_sha})));detail(hist,\'Schedule and official-result history\',{schedule:g.schedule_history,outcomes:g.outcomes});el(\'games\').append(tr)}el(\'sources\').textContent=JSON.stringify(data.sources,null,2);el(\'metrics\').textContent=JSON.stringify({season:sc.summary.season,horizons:sc.horizons,models:sc.model_breakdowns,matched:sc.matched_elo_comparisons},null,2);el(\'analysis\').textContent=JSON.stringify(w===\'all\'?sc.weekly_error_analysis:sc.weekly_error_analysis[w],null,2);el(\'v2\').textContent=data.full_v2_status;\nconst sim=data.simulation||{status:\'NOT_RUN\'},prior=(data.simulation_history||[]).at(-1);el(\'sim-status\').textContent=sim.status+\' · \'+fmt(sim.cutoff)+\' · \'+(sim.samples||0)+\' draws · \'+(sim.blocked_reason||\'Uncalibrated Elo sensitivity, not validated V2; no live-score conditioning.\');el(\'sim-teams\').replaceChildren();for(const [team,p]of Object.entries(sim.team_probabilities||{}).sort((a,b)=>b[1].super_bowl-a[1].super_bowl)){let tr=document.createElement(\'tr\');cell(tr,team);for(const k of [\'playoffs\',\'division\',\'one_seed\',\'conference\',\'super_bowl\'])cell(tr,pct(p[k]));const old=prior?.team_probabilities?.[team]?.super_bowl;cell(tr,old==null?\'—\':((p.super_bowl-old)*100).toFixed(2)+\' pp\');el(\'sim-teams\').append(tr)}el(\'sim-history\').textContent=JSON.stringify({limits:sim.limits,snapshots:[...(data.simulation_history||[]),sim].map(s=>({cutoff:s.cutoff,status:s.status,id:s.snapshot_id,model:s.model_state_sha256}))},null,2);\nel(\'health\').textContent=\'Worker \'+data.worker.status+\' · heartbeat \'+fmt(data.worker.checked_at)+\' | Last successful source check \'+fmt(data.last_successful_source_check)+\' | Last forecast \'+fmt(data.last_forecast_generation)+\' | Next run \'+fmt(data.next_scheduled_run);}\nasync function refresh(){try{let r=await fetch(\'/api/season\',{cache:\'no-store\'});if(!r.ok)throw Error(await r.text());data=await r.json();let selection=el(\'week\').value;el(\'week\').replaceChildren();for(let [v,t]of [[\'all\',\'Season\'],...[...new Set(data.games.map(g=>g.week))].sort((a,b)=>a-b).map(w=>[String(w),\'Week \'+w])]){let o=document.createElement(\'option\');o.value=v;o.textContent=t;el(\'week\').append(o)}el(\'week\').value=selection;render()}catch(e){el(\'health\').textContent=\'FAILED / STALE: \'+e.message}}el(\'week\').onchange=render;el(\'horizon\').onchange=render;el(\'reload\').onclick=refresh;refresh();setInterval(refresh,30000);\n</script></html>\n'


def main():
    cfg, root = configuration()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                content, kind, code = HTML.encode(), "text/html; charset=utf-8", 200
            elif self.path == "/health":
                content, kind, code = b'{"status":"ok"}', "application/json", 200
            elif self.path == "/api/season":
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
            self.end_headers()
            self.wfile.write(content)

    if cfg["bind"] != "127.0.0.1":
        raise ValueError("PRIVATE_LOOPBACK_BIND_REQUIRED")
    print("Viewer: http://127.0.0.1:" + str(cfg["port"]), flush=True)
    ThreadingHTTPServer((cfg["bind"], cfg["port"]), Handler).serve_forever()


if __name__ == "__main__":
    main()
