"use strict";
const metrics = [
  ["playoffs", "Playoff %"],
  ["division", "Division %"],
  ["one_seed", "No. 1 seed %"],
  ["conference", "Conference title %"],
  ["super_bowl", "Super Bowl win %"],
];
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const percent = (value) =>
  typeof value === "number" && Number.isFinite(value)
    ? value > 0 && value < 0.0005
      ? "<0.1%"
      : value >= 0.9995 && value < 1
        ? ">99.9%"
        : `${(value * 100).toFixed(1)}%`
    : "—";
const centralTime = (value) =>
  value && Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Chicago",
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(new Date(value))
    : "Unavailable";
const record = (team) =>
  `${team.wins}–${team.losses}${team.ties ? `–${team.ties}` : ""}`;
const winningPercent = (team) => {
  const played = team.wins + team.losses + team.ties;
  return played ? (team.wins + team.ties / 2) / played : 0;
};
function orderedTeams(
  data,
  conference = "all",
  sort = "super_bowl",
  direction = "desc",
) {
  return (data.teams || [])
    .filter((t) => conference === "all" || t.conference === conference)
    .slice()
    .sort((a, b) => {
      const av =
        sort === "record"
          ? winningPercent(a)
          : sort === "team"
            ? a.name
            : (a.probabilities?.[sort] ?? -1);
      const bv =
        sort === "record"
          ? winningPercent(b)
          : sort === "team"
            ? b.name
            : (b.probabilities?.[sort] ?? -1);
      const comparison =
        typeof av === "string" ? av.localeCompare(bv) : av - bv;
      return (
        comparison * (direction === "asc" ? 1 : -1) ||
        a.name.localeCompare(b.name)
      );
    });
}
function tableHtml(data, conference, sort, direction) {
  const favorites = new Set((data.favorite || []).map((t) => t.team));
  const rows = orderedTeams(data, conference, sort, direction);
  return `<div class="playoff-scroll" tabindex="0" role="region" aria-label="Team probabilities; scroll horizontally for all columns"><table class="playoff-table">
    <caption>${rows.length} teams. Records through ${escapeHtml(centralTime(data.view_updated_at))}. ${data.snapshot?.status === "COMPLETE" ? `Probability snapshot: ${escapeHtml(centralTime(data.snapshot.cutoff))}${data.status === "STALE" ? "; stale snapshot" : ""}.` : "Probabilities unavailable until every requested draw completes."} Simulated 0% and 100% do not mean mathematical elimination or clinching.</caption>
    <thead><tr><th scope="col">Team</th><th scope="col">Current record</th>${metrics.map(([, label]) => `<th scope="col">${label}</th>`).join("")}</tr></thead>
    <tbody>${rows
      .map(
        (t) =>
          `<tr class="${favorites.has(t.team) ? "favorite-row" : ""}"><th scope="row"><a href="/teams/${escapeHtml(t.team)}">${escapeHtml(t.name)}</a><small>${escapeHtml(t.division)}${favorites.has(t.team) ? " · Model favorite" : ""}</small></th><td>${record(t)}</td>${metrics
            .map(([key]) => {
              const value = t.probabilities?.[key];
              const width =
                typeof value === "number"
                  ? Math.max(0, Math.min(100, value * 100))
                  : 0;
              return `<td class="probability-cell" style="--probability:${width}%">${percent(value)}</td>`;
            })
            .join("")}</tr>`,
      )
      .join("")}</tbody></table></div>`;
}
function pageHtml(data) {
  const snapshot = data.snapshot;
  const complete = snapshot?.status === "COMPLETE";
  const samples = snapshot?.samples ?? data.configured_samples;
  const count =
    typeof samples === "number"
      ? samples.toLocaleString("en-US")
      : "Unavailable";
  const statusText =
    {
      COMPLETE: "Current snapshot",
      STALE: "Stale snapshot",
      BLOCKED: "Simulation blocked",
      ABSENT: "No saved simulation",
    }[data.status] || "Unavailable";
  const favorites = data.favorite || [];
  const reason = data.reason
    ? `<p class="playoff-error"><strong>Reason:</strong> ${escapeHtml(data.reason)}</p>`
    : "";
  const freshness = (data.freshness_reasons || [])
    .map((r) => `<li>${escapeHtml(r)}</li>`)
    .join("");
  const distribution = complete
    ? orderedTeams(data, "all", "super_bowl", "desc")
    : [];
  return `<div class="playoff-heading"><h1>Playoff Picture</h1><span class="playoff-status ${data.status === "COMPLETE" ? "" : "warning"}">${statusText}</span></div>
    <p>Every team's path to January, using the saved season Elo model.</p>
    ${data.status !== "COMPLETE" ? `<div class="notice" role="status">${reason}${freshness ? `<ul>${freshness}</ul>` : ""}<p>${data.status === "STALE" ? "These dated probabilities do not reflect the current season snapshot." : "No complete probability distribution is available for this run."} Reloading this page only reads saved data.</p></div>` : ""}
    <dl class="playoff-meta"><div><dt>Probability snapshot</dt><dd>${escapeHtml(centralTime(snapshot?.cutoff))}</dd></div><div><dt>Simulations</dt><dd>${count} ${complete ? "completed draws" : "requested draws"}</dd></div><div><dt>Current record snapshot</dt><dd>${escapeHtml(centralTime(data.view_updated_at))}</dd></div></dl>
    <p class="playoff-favorite">${complete && favorites.length ? `${data.status === "STALE" ? "Saved snapshot" : "Model"} favorite${favorites.length > 1 ? "s" : ""}: <strong>${favorites.map((t) => escapeHtml(t.name)).join(", ")}</strong> · ${percent(favorites[0].probabilities?.super_bowl ?? snapshot.team_probabilities[favorites[0].team].super_bowl)} to win the Super Bowl.` : "Model favorite unavailable while probabilities are blocked or absent."}</p>
    ${complete ? `<div class="playoff-distribution" role="img" aria-label="Super Bowl win probability distribution across all 32 teams">${distribution.map((t) => `<span style="width:${t.probabilities.super_bowl * 100}%" title="${escapeHtml(t.name)}: ${percent(t.probabilities.super_bowl)}"></span>`).join("")}</div><p class="muted">The table retains the full distribution. Conference filters do not change the model favorite.</p>` : ""}
    <div class="playoff-controls"><label>Conference<select id="conference"><option value="all">All 32 teams</option><option value="AFC">AFC</option><option value="NFC">NFC</option></select></label><label>Sort by<select id="sort">${metrics
      .slice()
      .reverse()
      .map(([key, label]) => `<option value="${key}">${label}</option>`)
      .join(
        "",
      )}<option value="record">Current record</option><option value="team">Team name</option></select></label><label>Order<select id="direction"><option value="desc">Highest first</option><option value="asc">Lowest first</option></select></label></div>
    <div id="team-table"></div>
    <section class="playoff-assumptions" aria-labelledby="assumptions"><h2 id="assumptions">How to read this picture</h2><ul>
      <li>Production Elo baseline, uncalibrated. Strength stays fixed within each simulated season. The ${escapeHtml(snapshot?.rating_sd ?? data.configured_rating_sd ?? "configured")}-point strength-sensitivity setting is uncalibrated; it is not a fitted uncertainty estimate.</li>
      <li>Completed regular-season scores are preserved (${snapshot?.preserved_final_games ?? "unknown"} in this simulation; ${data.current_final_games ?? "unknown"} in the current record snapshot). Future scores use pooled historical results; future injuries, transactions and weather are unknown. Live scores do not condition these simulations.</li>
      <li>Four division winners and three wild cards per conference; the No. 1 seed receives a bye. Higher seeds host, rounds reseed, and the Super Bowl is neutral. Postseason games have a winner.</li>
      <li>A missing required net-touchdown tiebreak blocks the entire run. No requested draws are discarded and no tiebreaker rules are weakened.</li>
      <li>${complete ? `Maximum Monte Carlo standard error: ${percent(snapshot.maximum_monte_carlo_standard_error)} (${count} draws).` : "Monte Carlo sampling error is unavailable for an incomplete run."} Monte Carlo sampling error measures random draw noise, not model accuracy.</li>
      <li>Simulated 0% means no successes in these draws; 100% means success in every draw. Neither establishes mathematical elimination or clinching.</li>
    </ul><details><summary>Snapshot details and refresh instructions</summary>
      <p>Refresh outside the forecast worker after a healthy source cycle. The page reads saved artifacts and never starts simulations. Inline live-worker simulation remains disabled.</p><pre>${escapeHtml(data.refresh_command || "See the season simulation runbook.")}</pre>
      <p class="playoff-provenance">Snapshot ID: ${escapeHtml(snapshot?.snapshot_id || "Unavailable")}<br>Model ID: ${escapeHtml(snapshot?.model_state_sha256 || "Unavailable")}<br>Current model ID: ${escapeHtml(data.current_model_id || "Unavailable")}<br>Seed: ${escapeHtml(snapshot?.seed ?? "Unavailable")}<br>Assessed: ${escapeHtml(centralTime(data.assessed_at))}</p>
      <p><a href="https://www.nfl.com/standings/tie-breaking-procedures" rel="noreferrer">Official NFL tiebreak procedures</a></p>
    </details></section>`;
}
if (typeof document !== "undefined") {
  const root = document.getElementById("playoffs");
  let savedData;
  const selection = {
    conference: "all",
    sort: "super_bowl",
    direction: "desc",
  };
  function renderTable() {
    document.getElementById("team-table").innerHTML = tableHtml(
      savedData,
      selection.conference,
      selection.sort,
      selection.direction,
    );
  }
  async function loadPicture() {
    const button = document.getElementById("refresh");
    button.disabled = true;
    try {
      const response = await fetch("/api/playoffs", { cache: "no-store" });
      if (!response.ok)
        throw new Error(`Saved picture request failed (${response.status}).`);
      savedData = await response.json();
      root.innerHTML = pageHtml(savedData);
      for (const id of ["conference", "sort", "direction"]) {
        const element = document.getElementById(id);
        element.value = selection[id];
        element.onchange = () => {
          selection[id] = element.value;
          renderTable();
        };
      }
      renderTable();
    } catch (error) {
      root.innerHTML = `<h1>Playoff Picture</h1><div class="notice" role="alert">${escapeHtml(error.message)} Reload saved data to retry.</div>`;
    } finally {
      button.disabled = false;
    }
  }
  document.getElementById("refresh").onclick = loadPicture;
  loadPicture();
}
if (typeof module !== "undefined")
  module.exports = { orderedTeams, tableHtml, pageHtml, percent };
