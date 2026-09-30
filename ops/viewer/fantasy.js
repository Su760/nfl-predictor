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

const watchlistKey = "nfl-player-lab.watchlist.v1";
let watched = new Set(),
  watchlistError = "";

function readWatchlist() {
  watchlistError = "";
  let raw;
  try {
    raw = localStorage.getItem(watchlistKey);
  } catch {
    watchlistError =
      "Browser storage is unavailable. Watchlist changes cannot be saved.";
    return false;
  }
  try {
    const saved =
      raw === null ? { version: 1, player_ids: [] } : JSON.parse(raw);
    if (
      saved?.version !== 1 ||
      !Array.isArray(saved.player_ids) ||
      !saved.player_ids.every(
        (id) => typeof id === "string" && /^00-\d{7}$/.test(id),
      )
    )
      throw new Error("Invalid watchlist");
    watched = new Set(saved.player_ids);
    return true;
  } catch {
    watchlistError =
      "Saved watchlist could not be read and was not overwritten. Clear watchlist to reset it.";
    return false;
  }
}

function refreshWatchlist() {
  renderWatchlist();
  if (snapshot) renderBoard();
}

function toggleWatch(id) {
  if (!readWatchlist()) {
    refreshWatchlist();
    return;
  }
  const next = new Set(watched);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  try {
    localStorage.setItem(
      watchlistKey,
      JSON.stringify({ version: 1, player_ids: [...next] }),
    );
    watched = next;
  } catch {
    watchlistError =
      "Browser storage is unavailable or full. This change was not saved.";
  }
  refreshWatchlist();
}

function bindWatchButtons(container) {
  container.querySelectorAll("[data-watch]").forEach((button) => {
    button.onclick = () => toggleWatch(button.dataset.watch);
  });
}

function renderWatchlist() {
  $("watchlist-status").textContent =
    watchlistError || `${watched.size} saved in this browser only.`;
  $("watchlist-status").className = watchlistError
    ? "notice storage-error"
    : "caption";
  $("watchlist-members").innerHTML = [...watched]
    .map((id) => {
      const player = snapshot?.players.find((p) => p.player_id === id);
      const label = player
        ? `${player.name} · ${player.team} ${player.position}`
        : `Unavailable player ${id}`;
      return `<li><span>${esc(label)}</span><button data-watch="${esc(id)}" aria-label="Remove ${esc(label)} from watchlist">Remove</button></li>`;
    })
    .join("");
  bindWatchButtons($("watchlist-members"));
}

function trendOptions() {
  const selected = $("trend-player").value;
  $("trend-player").innerHTML =
    '<option value="">Choose a player</option>' +
    snapshot.players
      .map(
        (p) =>
          `<option value="${esc(p.player_id)}">${esc(p.name)} · ${esc(p.team)} ${esc(p.position)}</option>`,
      )
      .join("");
  if (snapshot.players.some((p) => p.player_id === selected))
    $("trend-player").value = selected;
}

function renderTrends() {
  if (!snapshot) return;
  const id = $("trend-player").value,
    player = snapshot.players.find((p) => p.player_id === id);
  const trend = snapshot.trends?.[id];
  if (!player || !trend?.periods.length) {
    $("trends").textContent = player
      ? "Weekly usage unavailable for this player."
      : "Choose a player to see weekly usage and disjoint week-to-week changes.";
    return;
  }
  const keys = ["target_share", "carry_share", "snap_share"];
  $("trends").innerHTML =
    `<h3>${esc(player.name)} · ${esc(trend.team)}</h3><div class="table-wrap" tabindex="0" role="region" aria-label="Scrollable weekly usage"><table class="trend-table"><thead><tr><th scope="col">Week / game sample</th>${keys.map((k) => `<th scope="col">${esc(snapshot.definitions[k].label)}</th>`).join("")}</tr></thead><tbody>${trend.periods
      .map(
        (period) =>
          `<tr><th scope="row">Week ${period.week}<span class="player-meta">${period.prior_week ? `vs Week ${period.prior_week}` : "No prior season week"}<br>${period.games.map((g) => `${esc(g.date)} · ${esc(g.teams.join(" at "))}`).join("<br>") || "No game in captured schedule"}<br>${period.observed_games}/${period.expected_games} observed games<br>${esc(period.status)}</span></th>${keys
            .map((k) => {
              const m = period.metrics[k];
              const delta = available(m.delta_pp)
                ? `${m.delta_pp > 0 ? "+" : ""}${m.delta_pp.toFixed(1)} pp`
                : "Change unavailable";
              return `<td>${cell(k, m)}<span class="cell-note">${value("count", m.opportunities)} / ${value("count", m.team_opportunities)} ${k === "snap_share" ? "offensive snaps" : k === "target_share" ? "targets" : "carries"} (player / team)</span><span class="trend-change">${delta}</span>${available(m.delta_pp) ? "" : `<span class="trend-reason">${esc(m.delta_reason)}</span>`}</td>`;
            })
            .join("")}</tr>`,
      )
      .join(
        "",
      )}</tbody></table></div><p class="caption">Each change is this NFL week minus the immediately preceding NFL week, in percentage points. Games never overlap. Missing games, byes and incomplete paired counts make the affected change unavailable; older appearances never replace them. Counts are summed within each period, not averaged shares. Offensive snaps are not routes.</p>`;
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
      `${p.name} ${p.team}`.toLowerCase().includes(query) &&
      (!$("watchlist-only").checked || watched.has(p.player_id)),
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
        `<tr><td><span class="player-name">${esc(p.name)}</span><span class="player-meta">${esc(p.team)} · ${esc(p.position)} · ${p.observed_games}/${p.expected_games} observed</span><div class="player-actions"><button class="choose" data-player="${esc(p.player_id)}" aria-pressed="${chosen.includes(p.player_id)}" aria-label="${chosen.includes(p.player_id) ? "Remove" : "Compare"} ${esc(p.name)}">${chosen.includes(p.player_id) ? "Selected" : "Compare"}</button><button data-trend="${esc(p.player_id)}" aria-label="Weekly trends for ${esc(p.name)}">Trends</button><button data-watch="${esc(p.player_id)}" aria-pressed="${watched.has(p.player_id)}" aria-label="${watched.has(p.player_id) ? "Unwatch" : "Watch"} ${esc(p.name)}">${watched.has(p.player_id) ? "Unwatch" : "Watch"}</button></div></td>${metricKeys.map((k) => `<td>${cell(k, p.metrics[k])}</td>`).join("")}</tr>`,
    )
    .join("");
  $("empty").hidden = rows.length > 0;
  $("empty").textContent = snapshot.players.length
    ? "No players match these filters. Try another name, team, or position."
    : "No verified current-season players are available. Source status and refresh instructions appear below.";
  if ($("watchlist-only").checked && !rows.length)
    $("empty").textContent =
      "No watchlisted players match these filters. Your saved list remains above.";
  bindWatchButtons($("leaderboard"));
  $("leaderboard")
    .querySelectorAll("[data-trend]")
    .forEach(
      (button) =>
        (button.onclick = () => {
          $("trend-player").value = button.dataset.trend;
          renderTrends();
          $("trends-title").scrollIntoView({ block: "start" });
        }),
    );
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
      `<p>${players.filter(Boolean).length ? "One player selected. Choose a second player to compare." : "Choose any two WRs, RBs or TEs. Both use the viewing window above."}</p>`;
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
    `<p class="coverage-line">${c.completed_games ?? 0} completed games · Weeks ${(c.weeks || []).join(", ") || "unavailable"} · ${c.observed_players ?? 0} observed WR/RB/TE players · ${c.player_game_rows ?? 0} player-game rows.</p><p class="coverage-line">Counts describe the captured sample, not a full roster. A missing row cannot distinguish injury, inactivity, or missing provider coverage. Byes contribute neither numerator nor denominator.</p><p class="coverage-line"><strong>Routes unavailable.</strong> No route shares or targets-per-route are inferred from snaps.</p>`;
  if (c.positions) {
    $("coverage").innerHTML +=
      `<p class="coverage-line">Position coverage uses source row labels; leaderboard filters use each player's latest observed position. A player with changing source labels can appear in multiple coverage groups.</p><ul class="position-coverage">${Object.entries(
        c.positions,
      )
        .map(
          ([position, coverage]) =>
            `<li><strong>${esc(position)}</strong>: ${coverage.players} IDs across ${coverage.player_game_rows} observations. Missing counts: ${Object.entries(
              coverage.null_counts,
            )
              .map(
                ([key, count]) => `${esc(key.replaceAll("_", " "))} ${count}`,
              )
              .join("; ")}.</li>`,
        )
        .join("")}</ul>`;
  }
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
  $("trends").textContent = "Loading weekly usage…";
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
    trendOptions();
    renderWatchlist();
    renderTrends();
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
    $("trends").textContent = "Weekly usage unavailable until data loads.";
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
$("trend-player").onchange = renderTrends;
$("watchlist-only").onchange = () => {
  if (snapshot) renderBoard();
};
$("watchlist-clear").onclick = () => {
  try {
    localStorage.removeItem(watchlistKey);
    watched = new Set();
    watchlistError = "";
  } catch {
    watchlistError =
      "Browser storage is unavailable. The watchlist could not be cleared.";
  }
  refreshWatchlist();
};
window.addEventListener("storage", (event) => {
  if (event.key === watchlistKey || event.key === null) {
    readWatchlist();
    refreshWatchlist();
  }
});
readWatchlist();
renderWatchlist();
load();
