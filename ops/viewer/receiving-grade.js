/* Read-only prospective scorecards; prediction versions remain browser-local. */
(() => {
  const el = (id) => document.getElementById(id);
  const escape = (value) =>
    String(value ?? "Unavailable").replace(
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
  const number = (value, percent = false) =>
    typeof value === "number" && Number.isFinite(value)
      ? (value * (percent ? 100 : 1)).toFixed(2) + (percent ? "%" : "")
      : "Unavailable";
  const metrics = ["targets", "receptions", "receiving_yards"];
  let card;
  function render() {
    const version = card.versions.find(
      (v) => v.id === el("grading-version").value,
    );
    if (!version) return;
    const total = version.groups.ALL;
    el("grading-coverage").textContent =
      `${version.primary ? "Primary Week 4" : "Separate archived version"}: ${total.observed}/${total.forecasted} observed; ${total.pending} pending; ${total.missing} missing (DNP/no-stat/unknown); ${total.invalid} invalid outcomes; ${total.invalid_forecast} invalid paired estimates. ${version.complete ? "Complete" : "Incomplete grading"}. Bias = estimate minus actual. Usage tiers are archived prior-game tiers; horizons run from input cutoff to kickoff.`;
    let rows = "";
    for (const [group, summary] of Object.entries(version.groups)) {
      for (const metric of metrics) {
        for (const model of ["model", "rolling"]) {
          const s = summary.models[model][metric];
          rows += `<tr><td>${escape(group)}</td><td>${escape(metric)}</td><td>${model}</td><td>${summary.observed}/${summary.forecasted}</td><td>${summary.pending} / ${summary.missing} / ${summary.invalid} / ${summary.invalid_forecast}</td><td>${s.n}</td><td>${number(s.mae)}</td><td>${number(s.rmse)}</td><td>${number(s.bias)}</td><td>${s.interval_n} (${s.interval_missing} unavailable)</td><td>${number(s.interval_coverage, true)}</td><td>${number(s.interval_width)}</td></tr>`;
        }
      }
    }
    el("grading-table").innerHTML =
      `<div class="table-wrap" tabindex="0"><table class="grading-table"><thead><tr>${["Group", "Metric", "Estimate", "Observed / saved", "Pending / missing / invalid / invalid estimate", "Paired n", "MAE", "RMSE", "Bias", "Interval n", "Coverage", "Mean width"].map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
    el("grading-records").innerHTML =
      `<div class="table-wrap" tabindex="0"><table><thead><tr><th>Player / game</th><th>Position / tier / horizon</th><th>State</th><th>Actual targets / catches / yards</th><th>Reason</th></tr></thead><tbody>${version.records.map((r) => `<tr><td>${escape(r.name)} · ${escape(r.player_id)} · ${escape(r.game_id)}</td><td>${escape(r.position)} / ${escape(r.tier)} / ${escape(r.horizon)}</td><td>${escape(r.status)}</td><td>${metrics.map((m) => number(r.actual?.[m])).join(" / ")}</td><td>${escape(r.reason)}</td></tr>`).join("")}</tbody></table></div>`;
    el("grading-provenance").innerHTML =
      `<p>Archive SHA256: ${escape(version.archive_sha256)}. Model: ${escape(version.model_sha256)}. Method: ${escape(version.method_sha256)}. Forecast saved: ${escape(version.generated_at)}.</p><p>Outcome retrieval: ${escape(card.outcome_captured_at)}. Graded: ${escape(card.graded_at)}. Capture sequence ${card.capture_sequence}; source revision ${card.source_revision}. ${card.source_bytes_changed ? "Source bytes changed since prior capture; both versions retained." : "No source correction detected in this capture."} Outcome capture SHA256: ${escape(card.outcome_capture.sha256)}.</p>${Object.entries(
        card.sources,
      )
        .map(
          ([name, r]) =>
            `<p>${escape(name)}: SHA256 ${escape(r.sha256)}; retrieved ${escape(r.captured_at)}; HTTP Last-Modified (not exact publication time) ${escape(r.provider_modified_at)} (distinct from retrieval).</p>`,
        )
        .join(
          "",
        )}<p>Identical archive copies: ${version.locations.map(escape).join(", ")}. Rejected archives: ${escape(JSON.stringify(card.rejected_versions))}. Source completion requires numeric scores and an earlier Eastern calendar day. Missing rows do not establish a DNP and are excluded from paired errors. No historical experiment or forecasts are run here.</p>`;
  }
  async function load(capture) {
    el("grading-status").textContent = "Reading saved scorecard…";
    try {
      const response = await fetch(
        "/api/fantasy/receiving-grade" +
          (capture ? "?capture=" + encodeURIComponent(capture) : ""),
      );
      if (!response.ok) throw Error(`HTTP ${response.status}`);
      card = await response.json();
      if (card.state === "unavailable")
        throw Error(card.error || "No saved scorecard");
      el("grading-status").textContent =
        `${card.complete ? "Complete" : "Incomplete grading"} · ${card.season} Week ${card.week} · outcomes retrieved ${card.outcome_captured_at}${card.stale ? " · Stale outcomes: refresh with the grading command" : ""}. Primary version is fixed; other versions are never selected by accuracy.`;
      el("grading-capture").innerHTML = card.captures
        .map(
          (c) =>
            `<option value="${escape(c.file)}">Capture ${c.capture_sequence}, revision ${c.source_revision} · ${escape(c.outcome_captured_at)}</option>`,
        )
        .join("");
      if (capture) el("grading-capture").value = capture;
      el("grading-version").innerHTML = card.versions
        .map(
          (v) =>
            `<option value="${escape(v.id)}">${v.primary ? "Primary: first-prospective (273)" : "Separate version"} · ${escape(v.generated_at)} · ${escape(v.id.slice(0, 12))}</option>`,
        )
        .join("");
      el("grading-version").value = card.primary_id;
      render();
    } catch (error) {
      el("grading-status").textContent =
        "Scorecard unavailable: " + error.message;
      for (const id of [
        "grading-capture",
        "grading-version",
        "grading-coverage",
        "grading-table",
        "grading-provenance",
        "grading-records",
      ])
        el(id).replaceChildren();
    }
  }
  el("grading-reload").addEventListener("click", () => load());
  el("grading-capture").addEventListener("change", () =>
    load(el("grading-capture").value),
  );
  el("grading-version").addEventListener("change", render);
  load();
})();
