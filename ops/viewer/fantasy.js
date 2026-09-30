"use strict";
const $ = (id) => document.getElementById(id);
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const metricKeys = [
  "targets",
  "target_share",
  "carries",
  "carry_share",
  "snap_share",
  "air_yards",
  "red_zone",
];
const share = (k) => k.endsWith("_share");
const available = (v) => typeof v === "number" && Number.isFinite(v);
const value = (k, v) =>
  available(v)
    ? share(k)
      ? (100 * v).toFixed(1) + "%"
      : v.toLocaleString("en-US", { maximumFractionDigits: 1 })
    : "Unavailable";
const time = (t) =>
  t && Number.isFinite(Date.parse(t))
    ? new Intl.DateTimeFormat("en-US", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "America/Chicago",
      }).format(new Date(t)) + " CT"
    : "Not provided";
let snapshot = null,
  chosen = ["", ""],
  requestVersion = 0;

function cell(k, metric) {
  const m = metric || { value: null, covered_games: 0, expected_games: 0 };
  return `<strong class="${available(m.value) ? "" : "unavailable"}">${value(k, m.value)}</strong><span class="cell-note">${m.covered_games} / ${m.expected_games} games</span>`;
}

function options() {
  const html =
    '<option value="">Choose a player</option>' +
    snapshot.players
      .map(
        (p) =>
          `<option value="${esc(p.player_id)}">${esc(p.name)} · ${esc(p.team)} ${esc(p.position)}</option>`,
      )
      .join("");
  ["player-a", "player-b"].forEach((id, i) => {
    $(id).innerHTML = html;
    if (!snapshot.players.some((p) => p.player_id === chosen[i]))
      chosen[i] = "";
    $(id).value = chosen[i];
  });
}

function renderBoard() {
  const query = $("search").value.trim().toLowerCase(),
    position = $("position").value,
    metric = $("metric").value;
  const rows = snapshot.players.filter(
    (p) =>
      (position === "all" || p.position === position) &&
      `${p.name} ${p.team}`.toLowerCase().includes(query),
  );
  rows.sort((a, b) => {
    const x = a.metrics[metric].value,
      y = b.metrics[metric].value;
    return (
      Number(available(y)) - Number(available(x)) ||
      (available(x) && available(y) ? y - x : 0) ||
      a.name.localeCompare(b.name)
    );
  });
  $("count").textContent =
    `${rows.length} players · ${rows.filter((p) => available(p.metrics[metric].value)).length} with complete ${snapshot.definitions[metric].label.toLowerCase()}`;
  $("leaderboard").querySelector("thead").innerHTML =
    `<tr><th scope="col">Player / sample</th>${metricKeys.map((k) => `<th scope="col" ${k === metric ? 'aria-sort="descending"' : ""} title="${esc(snapshot.definitions[k].definition)}">${esc(snapshot.definitions[k].label)}</th>`).join("")}</tr>`;
  $("leaderboard").querySelector("tbody").innerHTML = rows
    .map(
      (p) =>
        `<tr><td><span class="player-name">${esc(p.name)}</span><span class="player-meta">${esc(p.team)} · ${esc(p.position)} · ${p.observed_games}/${p.expected_games} observed</span><button class="choose" data-player="${esc(p.player_id)}" aria-pressed="${chosen.includes(p.player_id)}" aria-label="${chosen.includes(p.player_id) ? "Remove" : "Compare"} ${esc(p.name)}">${chosen.includes(p.player_id) ? "Selected" : "Compare"}</button></td>${metricKeys.map((k) => `<td>${cell(k, p.metrics[k])}</td>`).join("")}</tr>`,
    )
    .join("");
  $("empty").hidden = rows.length > 0;
  $("empty").textContent = snapshot.players.length
    ? "No players match these filters. Try another name, team, or position."
    : "No verified current-season players are available. Source status and refresh instructions appear below.";
  $("leaderboard")
    .querySelectorAll("[data-player]")
    .forEach(
      (button) =>
        (button.onclick = () => {
          const id = button.dataset.player,
            at = chosen.indexOf(id);
          if (at >= 0) chosen[at] = "";
          else if (!chosen[0]) chosen[0] = id;
          else if (!chosen[1]) chosen[1] = id;
          else chosen[1] = id;
          ["player-a", "player-b"].forEach(
            (key, i) => ($(key).value = chosen[i]),
          );
          renderBoard();
          renderComparison();
        }),
    );
}

function renderComparison() {
  const players = chosen.map((id) =>
    snapshot.players.find((p) => p.player_id === id),
  );
  if (players.some((p) => !p)) {
    $("comparison").innerHTML =
      `<p>${players.filter(Boolean).length ? "One player selected. Choose a second player to compare." : "Choose any two WRs or RBs. Both use the viewing window above."}</p>`;
    return;
  }
  const header = `<tr><th scope="col">Metric</th>${players.map((p) => `<th scope="col">${esc(p.name)}<span class="player-meta">${esc(p.team)} ${esc(p.position)} · Weeks ${p.weeks.join(", ")}<br>${p.observed_games}/${p.expected_games} observed games</span></th>`).join("")}</tr>`;
  const body = metricKeys
    .map((k) => {
      const [a, b] = players.map((p) => p.metrics[k].value);
      return `<tr><th scope="row">${esc(snapshot.definitions[k].label)}</th>${players.map((p, i) => `<td class="${available(a) && available(b) && (i === 0 ? a > b : b > a) ? "higher" : ""}">${cell(k, p.metrics[k])}${share(k) && available(p.metrics[k].value) ? `<span class="cell-note">${p.metrics[k].numerator} / ${p.metrics[k].denominator}</span>` : ""}</td>`).join("")}</tr>`;
    })
    .join("");
  $("comparison").innerHTML =
    `<div class="table-wrap"><table class="compare-table"><thead>${header}</thead><tbody>${body}</tbody></table></div><p class="caption">Teal marks the larger observed value, not a recommendation. Shares use summed numerators and denominators shown above.</p><div class="game-details">${players.map((p) => `<details><summary>${esc(p.name)}: game sample</summary><p class="caption">${esc(p.window_note)}</p><ul>${p.games.map((g) => `<li>Week ${g.week} · ${esc(g.date)} · ${esc(g.teams.join(" at "))}: ${g.observed ? `${value("targets", g.usage.targets)} targets, ${value("carries", g.usage.carries)} carries` : "No player observation; unavailable"}</li>`).join("")}</ul></details>`).join("")}</div>`;
}

function renderEvidence() {
  $("season").textContent = `${snapshot.season} regular season · Player Lab`;
  $("forecasts").href = snapshot.forecast_url;
  $("updated").textContent = `Snapshot built ${time(snapshot.updated_at)}`;
  $("window-note").textContent = snapshot.window_note;
  const warnings = [];
  if (snapshot.state === "unavailable")
    warnings.push(
      "Current-season usage is unavailable. Run the fantasy refresh command below to capture sources.",
    );
  if (snapshot.state === "stale")
    warnings.push(
      `Saved usage is stale (${snapshot.age_hours} hours old; freshness limit ${snapshot.stale_after_hours} hours). Values retain their original timestamps.`,
    );
  if (snapshot.refresh.status === "failed")
    warnings.push(
      `Last refresh failed: ${snapshot.refresh.error}. Saved data was not marked fresh.`,
    );
  const missing = Object.values(snapshot.sources).filter(
    (s) => s.status !== "available",
  );
  if (missing.length)
    warnings.push(
      `Unavailable sources: ${missing.map((s) => s.name).join(", ")}. Affected metrics remain unavailable.`,
    );
  $("status").innerHTML = warnings.length
    ? `<div class="notice">${warnings.map((w) => `<p>${esc(w)}</p>`).join("")}</div>`
    : "";
  const c = snapshot.coverage;
  $("coverage").innerHTML =
    `<p class="coverage-line">${c.completed_games ?? 0} completed games · Weeks ${(c.weeks || []).join(", ") || "unavailable"} · ${c.observed_players ?? 0} observed WR/RB players · ${c.player_game_rows ?? 0} player-game rows.</p><p class="coverage-line">Counts describe the captured sample, not a full roster. A missing row cannot distinguish injury, inactivity, or missing provider coverage. Byes contribute neither numerator nor denominator.</p><p class="coverage-line"><strong>Routes unavailable.</strong> No route shares or targets-per-route are inferred from snaps.</p>`;
  const currentSources = Object.keys(snapshot.sources).length
    ? snapshot.sources
    : snapshot.refresh.sources || {};
  $("sources").innerHTML =
    `<p>Last refresh attempt: ${time(snapshot.refresh.attempted_at)} · ${esc(snapshot.refresh.status)}. Reload reads the saved snapshot; it does not fetch providers. Refresh sources locally with <code>.venv-fantasy/bin/python -m fantasy.sources</code>.</p><p>Provider modification is the file timestamp, not a player-level update time. End-of-game-to-publication latency cannot be measured from these files. nflverse normally refreshes stats nightly and snaps every six hours; corrections may follow.</p>${Object.entries(
      currentSources,
    )
      .map(
        ([name, s]) =>
          `<article class="source"><h3>${esc(name)} <span class="tag">${esc(s.status)}</span></h3><p>Captured: ${time(s.captured_at)} · Provider modified: ${time(s.provider_modified_at)}</p><p>${s.source_rows ?? "Unavailable"} source rows before current-season filtering${s.error ? " · " + esc(s.error) : ""}</p>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noreferrer">Source file</a>` : ""}</article>`,
      )
      .join(
        "",
      )}<p>Missing cells among ${c.player_game_rows ?? 0} observed player-game rows: ${
      Object.entries(c.null_counts || {})
        .map(([k, v]) => `${esc(k.replaceAll("_", " "))}: ${v}`)
        .join("; ") || "unavailable"
    }. Missing player-game rows are additional gaps, shown in window coverage.</p><p><a href="https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html" target="_blank" rel="noreferrer">Provider update schedule</a></p>`;
  $("definitions").innerHTML = Object.values(snapshot.definitions)
    .map((d) => `<dt>${esc(d.label)}</dt><dd>${esc(d.definition)}</dd>`)
    .join("");
}

async function load() {
  const version = ++requestVersion;
  // A newly selected window must never render data from the previous request.
  snapshot = null;
  $("leaderboard").querySelector("tbody").innerHTML = "";
  $("comparison").textContent = "Loading this window…";
  $("count").textContent = "";
  $("empty").hidden = true;
  $("reload").disabled = true;
  try {
    const response = await fetch(
      "/api/fantasy?window=" + encodeURIComponent($("window").value),
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    if (version !== requestVersion) return;
    snapshot = result;
    renderEvidence();
    options();
    renderBoard();
    renderComparison();
  } catch (error) {
    if (version !== requestVersion) return;
    $("status").innerHTML =
      `<div class="notice" role="alert">Unable to load this window: ${esc(error.message)}. Reload saved data to retry.</div>`;
    $("leaderboard").querySelector("tbody").innerHTML = "";
    $("comparison").textContent =
      "Comparison unavailable until this window loads.";
    $("count").textContent = "";
  } finally {
    if (version === requestVersion) $("reload").disabled = false;
  }
}
$("window").onchange = load;
$("reload").onclick = load;
["position", "metric"].forEach(
  (id) =>
    ($(id).onchange = () => {
      if (snapshot) renderBoard();
    }),
);
$("search").oninput = () => {
  if (snapshot) renderBoard();
};
["player-a", "player-b"].forEach(
  (id, i) =>
    ($(id).onchange = () => {
      chosen[i] = $(id).value;
      if (chosen[i] && chosen[i] === chosen[1 - i]) {
        chosen[1 - i] = "";
        $("player-" + (i === 0 ? "b" : "a")).value = "";
      }
      if (snapshot) {
        renderBoard();
        renderComparison();
      }
    }),
);
$("clear").onclick = () => {
  chosen = ["", ""];
  if (snapshot) {
    options();
    renderBoard();
    renderComparison();
  }
};
load();
