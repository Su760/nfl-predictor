"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  const escape = (x) =>
    String(x ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const numeric = (v) => typeof v === "number" && Number.isFinite(v);
  const fmt = (v) => (numeric(v) ? v.toFixed(1) : "Unavailable");
  const stamp = (v) =>
    Number.isFinite(Date.parse(v))
      ? new Date(v).toLocaleString("en-US", {
          timeZone: "America/Chicago",
          dateStyle: "medium",
          timeStyle: "short",
        }) + " CT"
      : "Unavailable";
  const metrics = ["targets", "receptions", "receiving_yards"];
  const labels = {
    targets: "Targets",
    receptions: "Catches",
    receiving_yards: "Receiving yards",
  };
  const key = "nfl-player-lab.predictions.v1";
  let sheet = null,
    versions = [],
    storageError = "",
    loadVersion = 0;
  const identity = (row) => row.game_id + "|" + row.player_id;

  function readVersions() {
    try {
      const raw = localStorage.getItem(key);
      const saved =
        raw === null ? { version: 1, entries: [] } : JSON.parse(raw);
      if (
        saved?.version !== 1 ||
        !Array.isArray(saved.entries) ||
        !saved.entries.every(
          (r) =>
            typeof r.id === "string" &&
            /^00-\d{7}$/.test(r.player_id) &&
            typeof r.game_id === "string" &&
            Number.isFinite(Date.parse(r.created_at)) &&
            Number.isFinite(Date.parse(r.kickoff)) &&
            Date.parse(r.created_at) < Date.parse(r.kickoff) &&
            Number.isInteger(r.revision) &&
            r.revision > 0 &&
            metrics.every((m) => numeric(r.values?.[m])) &&
            r.values.targets >= r.values.receptions &&
            r.values.receptions >= 0,
        ) ||
        new Set(saved.entries.map((r) => r.id)).size !== saved.entries.length
      )
        throw Error("Invalid saved versions");
      versions = saved.entries;
      storageError = "";
      return true;
    } catch {
      storageError =
        "Browser storage is blocked or saved predictions are unreadable. Existing data was not overwritten; no new version can be saved.";
      return false;
    }
  }

  function history() {
    el("prediction-status").textContent =
      storageError ||
      `${versions.length} saved versions in this browser only. Concurrent tab saves use the browser's last write; export/back up browser data to retain it.`;
    if (!versions.length) {
      el("prediction-history").textContent =
        "No personal predictions saved yet.";
      return;
    }
    const outcomes = new Map(
      (sheet?.outcomes?.rows || []).map((r) => [identity(r), r]),
    );
    el("prediction-history").innerHTML =
      `<div class="table-wrap"><table><thead><tr><th>Player / version</th><th>Saved before kickoff</th>${metrics.map((m) => `<th>${labels[m]} · call / actual / difference</th>`).join("")}</tr></thead><tbody>${versions
        .slice()
        .reverse()
        .map((v) => {
          const actual = outcomes.get(identity(v));
          return `<tr><th scope="row">${escape(v.name)} · v${v.revision}<span class="cell-note">${escape(v.game_id)}</span></th><td>${escape(stamp(v.created_at))}<span class="cell-note">Kickoff ${escape(stamp(v.kickoff))}; device-clock record</span></td>${metrics
            .map((m) => {
              const known = actual?.status === "observed" && numeric(actual[m]);
              return `<td>${fmt(v.values[m])} / ${known ? fmt(actual[m]) : "Unavailable"} / ${known ? fmt(v.values[m] - actual[m]) : "Unavailable"}<span class="cell-note">${known ? "Call minus actual" : "Awaiting numeric outcome; absence is not zero or confirmed DNP"}</span></td>`;
            })
            .join("")}</tr>`;
        })
        .join(
          "",
        )}</tbody></table></div><p class="caption">Actual source capture: ${escape(stamp(sheet?.outcomes?.source?.captured_at))}. All versions remain visible; later actual corrections do not change saved calls.</p>`;
  }

  function render() {
    const selected = el("prediction-player").value;
    const rows = sheet?.rows || [];
    const future = rows.filter((r) => Date.parse(r.kickoff) > Date.now());
    el("prediction-player").innerHTML =
      '<option value="">Choose a projected player</option>' +
      future
        .map(
          (r) =>
            `<option value="${escape(identity(r))}">${escape(r.name)} · ${escape(r.team)} vs ${escape(r.opponent)} · Week ${r.week}</option>`,
        )
        .join("");
    if (future.some((r) => identity(r) === selected))
      el("prediction-player").value = selected;
    el("prediction-save").disabled = !future.length || !!storageError;
    const filter = el("receiving-filter").value;
    const visible = future.filter(
      (r) => filter === "all" || r.position === filter,
    );
    el("receiving-sheet").innerHTML = visible.length
      ? `<div class="table-wrap" tabindex="0" role="region" aria-label="Scrollable upcoming receiving projections"><table class="receiving-table"><thead><tr><th>Player / upcoming game</th>${metrics.map((m) => `<th>${labels[m]} estimate · 80% range</th>`).join("")}<th>Prior sample / assumptions</th></tr></thead><tbody>${visible.map((r) => `<tr><th scope="row">${escape(r.name)}<span class="player-meta">${escape(r.team)} ${escape(r.position)} vs ${escape(r.opponent)}<br>Week ${r.week} · ${escape(stamp(r.kickoff))}</span></th>${metrics.map((m) => `<td><strong>${fmt(r.estimate[m])}</strong><span class="cell-note">${r.interval[m] ? `${fmt(r.interval[m].low)}–${fmt(r.interval[m].high)}` : "Range unavailable"}</span><span class="cell-note">${r.interval[m] ? `${r.interval[m].calibration_n} calibration rows (${escape(r.interval[m].calibration_group)})` : "Insufficient calibration"}</span></td>`).join("")}<td><span class="cell-note">${r.sample.observed_games}/${r.sample.expected_games} prior team games · ${r.sample.targets} targets / ${r.sample.team_targets} known team targets; ${r.sample.receptions} catches; ${r.sample.receiving_yards} yards<br>Cutoff ${escape(stamp(r.cutoff))} (${fmt(r.lead_hours)} hours before kickoff)</span><p class="assumption">${escape(r.assumption)}</p></td></tr>`).join("")}</tbody></table></div>`
      : "No upcoming supported projections in the saved archive for this filter. Kicked-off games are not shown as upcoming.";
    history();
  }

  function method() {
    el("receiving-method").innerHTML =
      `<p>Last four prior completed team games; team attempts/targets shrunk toward 2019–2022 priors. Targets use shrunk player allocation; catches use shrunk catch probability; yards use shrunk yards per catch. 2023 calibrates marginal 80% ranges; 2024–2025 is one retrospective comparison, not untouched or proven publication-time evidence. No 2026 fitting. Benchmark horizon is 24 hours before kickoff; these archives use generation time and can have different horizons.</p><p>All observed pass catchers, including RB/FB/QB/other positions, consume allocated targets even though only WR/TE are displayed. Allocations only scale down; unknown historical coverage and participants outside candidates retain unallocated targets. Assumes last observed participation continues, without injury probabilities or confirmed starters.</p><p>Ranges are marginal historical error bands, not joint guarantees. Samples are observed prior rows; missing games are unknown and not zero. New players without same-season history are outside this sheet.</p><div class="table-wrap"><table><thead><tr><th>Team / game</th><th>Attempts</th><th>Credited targets</th><th>Allocated / unallocated targets</th><th>Historical coverage / allocation positions</th></tr></thead><tbody>${(sheet.teams || []).map((t) => `<tr><th scope="row">${escape(t.team)} · ${escape(t.game_id)}</th><td>${fmt(t.attempts)}</td><td>${fmt(t.targets)}</td><td>${fmt(t.allocated_targets)} / ${fmt(t.unallocated_targets)}</td><td>${numeric(t.historical_target_coverage) ? (t.historical_target_coverage * 100).toFixed(1) + "%" : escape(t.status)} · ${escape((t.allocation_positions || []).join(", "))}</td></tr>`).join("")}</tbody></table></div><p>Source times describe local retrieval and HTTP file modification, not verified publication.</p>${Object.entries(
        sheet.sources || {},
      )
        .map(
          ([name, s]) =>
            `<p>${escape(name)} retrieved ${escape(stamp(s.captured_at))}; provider modified ${escape(stamp(s.provider_modified_at))}.</p>`,
        )
        .join("")}`;
    const groups = Object.entries(sheet.benchmark?.groups || {});
    el("receiving-benchmark").innerHTML =
      `<p>Experimental when fixed criteria fail. Benchmarks pair identical prior-only candidates and numeric outcomes. No outcome row = DNP/no-stat/unknown, excluded from errors; confirmed DNP counts unavailable. Bias = prediction minus actual. Ranges were calibrated on 2023; width is in metric units. Tiers are prior rolling targets: low &lt;3, medium 3–&lt;6, high ≥6.</p><p>${escape((sheet.gates || []).join("; ") || "Fixed numerical criteria passed; prospective evidence still pending.")}</p><div class="table-wrap"><table><thead><tr><th>Group / metric</th><th>Scored / forecasts</th><th>Missing / invalid</th><th>Model MAE / RMSE / bias</th><th>Rolling MAE / RMSE / bias</th><th>Model interval coverage / width</th><th>Rolling interval coverage / width</th></tr></thead><tbody>${groups
        .flatMap(([group, g]) =>
          metrics.map((m) => {
            const a = g.models.model[m],
              b = g.models.rolling[m];
            return `<tr><th scope="row">${escape(group)} · ${labels[m]}</th><td>${g.scored} / ${g.forecasted}</td><td>${g.missing_outcome} / ${g.invalid_outcome}</td><td>${fmt(a.mae)} / ${fmt(a.rmse)} / ${fmt(a.bias)}</td><td>${fmt(b.mae)} / ${fmt(b.rmse)} / ${fmt(b.bias)}</td><td>${numeric(a.interval_coverage) ? (100 * a.interval_coverage).toFixed(1) + "%" : "Unavailable"} / ${fmt(a.interval_width)}</td><td>${numeric(b.interval_coverage) ? (100 * b.interval_coverage).toFixed(1) + "%" : "Unavailable"} / ${fmt(b.interval_width)}</td></tr>`;
          }),
        )
        .join("")}</tbody></table></div>`;
  }

  async function load() {
    const version = ++loadVersion;
    sheet = null;
    el("receiving-sheet").textContent = "Loading archived projections…";
    el("receiving-method").textContent = "";
    el("receiving-benchmark").textContent = "";
    el("prediction-save").disabled = true;
    el("prediction-player").innerHTML =
      '<option value="">Choose a projected player</option>';
    history();
    try {
      const response = await fetch("/api/fantasy/receiving", {
        cache: "no-store",
      });
      if (!response.ok) throw Error(`HTTP ${response.status}`);
      const result = await response.json();
      if (version !== loadVersion) return;
      if (result.state === "unavailable")
        throw Error(result.error || "Archive unavailable");
      sheet = result;
      el("receiving-status").textContent =
        `${sheet.experimental ? "Experimental" : "Baseline; prospective evidence pending"} · Week ${sheet.week} · archived ${stamp(sheet.generated_at)} · data weeks ${(sheet.data_weeks || []).join(", ")}. ${sheet.state === "stale" ? "Saved projections are stale; a new manual archive is required." : "Reload reads the archive; it does not generate forecasts."}`;
      method();
      render();
    } catch (error) {
      if (version !== loadVersion) return;
      el("receiving-status").textContent =
        `Projections unavailable: ${error.message}. Generate a receiving archive locally; saved personal calls are retained.`;
      el("receiving-sheet").textContent =
        "No supported projection numbers available.";
    }
  }

  el("my-prediction-form").onsubmit = (event) => {
    event.preventDefault();
    if (!readVersions()) {
      history();
      return;
    }
    const row = sheet?.rows.find(
      (r) => identity(r) === el("prediction-player").value,
    );
    const values = {
      targets: Number(el("prediction-targets").value),
      receptions: Number(el("prediction-catches").value),
      receiving_yards: Number(el("prediction-yards").value),
    };
    if (
      !row ||
      !Number.isFinite(Date.parse(row.kickoff)) ||
      Date.now() >= Date.parse(row.kickoff)
    ) {
      el("prediction-status").textContent =
        "No available player/game or kickoff has passed. Earlier calls are preserved; no late version saved.";
      return;
    }
    if (
      !metrics.every((m) => numeric(values[m])) ||
      values.receptions < 0 ||
      values.targets < values.receptions
    ) {
      el("prediction-status").textContent =
        "Use finite values and 0 ≤ catches ≤ targets. No version saved.";
      return;
    }
    const version = {
      id: crypto.randomUUID(),
      player_id: row.player_id,
      game_id: row.game_id,
      name: row.name,
      kickoff: row.kickoff,
      created_at: new Date().toISOString(),
      revision:
        Math.max(
          0,
          ...versions
            .filter((v) => identity(v) === identity(row))
            .map((v) => v.revision),
        ) + 1,
      values,
      model_archive_at: sheet.generated_at,
    };
    try {
      const next = [...versions, version];
      localStorage.setItem(key, JSON.stringify({ version: 1, entries: next }));
      versions = next;
      history();
      el("prediction-status").textContent =
        `Saved v${version.revision} before captured kickoff. All earlier versions retained.`;
    } catch {
      el("prediction-status").textContent =
        "Browser storage is blocked or full. New version was not saved; earlier calls remain unchanged.";
    }
  };
  el("receiving-filter").onchange = render;
  el("receiving-reload").onclick = load;
  window.addEventListener("storage", (event) => {
    if (event.key === key || event.key === null) {
      readVersions();
      render();
    }
  });
  readVersions();
  history();
  load();
})();
