"use strict";
const root = document.querySelector("#points");
const number = (x) => (Number.isFinite(x) ? x.toFixed(1) : "Unavailable");
const signed = (x) =>
  Number.isFinite(x) ? `${x > 0 ? "+" : ""}${x.toFixed(1)}` : "Unavailable";
const when = (x) =>
  x
    ? new Date(x).toLocaleString("en-US", {
        timeZone: "America/Chicago",
        timeZoneName: "short",
      })
    : "Unavailable";
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function targetPanel(name, spec, spread) {
  const panel = node("section");
  panel.append(
    node(
      "h3",
      name === "margin"
        ? "Home margin / implied spread"
        : "Combined final points",
    ),
  );
  panel.append(
    node(
      "p",
      name === "margin" ? signed(spec.candidate) : number(spec.candidate),
      "points-value",
    ),
  );
  if (spread)
    panel.append(
      node(
        "p",
        `${spread.favorite} ${signed(spread.favorite_spread)} · home spread ${signed(spread.home_spread)}`,
      ),
    );
  panel.append(
    node(
      "p",
      `80% interval: ${number(spec.interval[0])} to ${number(spec.interval[1])}`,
    ),
  );
  panel.append(
    node(
      "p",
      `Baseline ${number(spec.baseline)} · difference ${signed(spec.candidate - spec.baseline)}`,
    ),
  );
  panel.append(
    node(
      "p",
      `Baseline 80% interval: ${number(spec.baseline_interval[0])} to ${number(spec.baseline_interval[1])}`,
      "points-meta",
    ),
  );
  panel.append(
    node(
      "p",
      spec.tracking_eligible
        ? "Gate passed · eligible for experimental tracking"
        : "Gate failed · research only, excluded from tracking",
      spec.tracking_eligible ? "points-meta" : "points-alert",
    ),
  );
  return panel;
}
function render(data) {
  root.replaceChildren(node("h1", "Experimental spread & total"));
  const note = node("section", undefined, "points-note");
  note.append(
    node(
      "p",
      "Home margin +3 means an implied home spread of −3. Intervals are nominal 80%; they are not cover or over probabilities.",
    ),
  );
  note.append(
    node(
      "p",
      "This is a frozen predictive experiment. A passed engineering gate does not establish betting value. Interim results are descriptive; no tuning against this cohort.",
    ),
  );
  root.append(note);
  const status = node("section", undefined, "points-status");
  status.append(
    node(
      "strong",
      data.status === "STALE"
        ? "STALE DATA — saved forecasts only"
        : data.status === "UNAVAILABLE"
          ? "Experimental forecasts unavailable"
          : "Saved experimental forecasts",
    ),
  );
  if (data.version) {
    status.append(
      node(
        "p",
        `${data.version} · ${data.archived_games} / ${data.cohort_limit} games archived`,
      ),
    );
    status.append(
      node(
        "p",
        `Margin gate ${data.gates.margin} · total gate ${data.gates.total}`,
      ),
    );
    status.append(
      node(
        "p",
        `Sheet saved ${when(data.generated_at)} · source ${when(data.source_timestamp)}`,
        "points-meta",
      ),
    );
  }
  if (data.reason) status.append(node("p", data.reason.replaceAll("_", " ")));
  if (data.grading) {
    const counts = Object.entries(data.grading.targets)
      .map(([name, r]) => `${name}: ${r.candidate?.n || 0} graded`)
      .join(" · ");
    status.append(node("p", `Descriptive interim scores · ${counts}`));
  }
  root.append(status);
  if (!data.games?.length)
    root.append(node("p", "No eligible upcoming games in the saved sheet."));
  for (const game of data.games || []) {
    const card = node("article", undefined, "points-game");
    card.append(
      node(
        "h2",
        `${game.away_name || game.away} @ ${game.home_name || game.home}`,
      ),
    );
    card.append(
      node(
        "p",
        `${when(game.kickoff)}${game.neutral_site ? " · neutral venue" : ""}`,
      ),
    );
    if (!game.targets) {
      card.append(
        node(
          "p",
          `${game.status}: ${(game.reason || "Unavailable").replaceAll("_", " ")}`,
          "points-alert",
        ),
      );
      if (game.baseline)
        card.append(
          node(
            "p",
            `Baseline margin ${signed(game.baseline.margin)} · total ${number(game.baseline.total)}`,
          ),
        );
    } else {
      if (game.status !== "AVAILABLE" && game.status !== "SAVED")
        card.append(node("p", game.status, "points-alert"));
      const grid = node("div", undefined, "points-grid");
      grid.append(
        targetPanel("margin", game.targets.margin, game.spread),
        targetPanel("total", game.targets.total),
      );
      card.append(grid);
      card.append(
        node(
          "p",
          `${game.version} · forecast ${when(game.generated_at)} · data cutoff ${when(game.data_cutoff)}`,
          "points-meta",
        ),
      );
      card.append(
        node(
          "p",
          `Archive: ${(game.archive_status || "saved").replaceAll("_", " ")}`,
          "points-meta",
        ),
      );
      const details = node("details");
      details.append(node("summary", "Separate market evidence"));
      if (game.market.status === "AVAILABLE") {
        for (const q of game.market.quotes)
          details.append(
            node(
              "p",
              `${q.sportsbook} / ${q.provider}: ${q.target} ${q.side} ${signed(q.line)}, odds ${signed(q.odds)} · market ${when(q.market_timestamp)} · retrieved ${when(q.retrieval_timestamp)}`,
            ),
          );
      } else
        details.append(
          node(
            "p",
            "Timestamped pregame prices unavailable. No value or profitability estimate.",
          ),
        );
      card.append(details);
    }
    root.append(card);
  }
}
async function load() {
  try {
    const response = await fetch("/api/experimental", { cache: "no-store" });
    if (!response.ok) throw new Error("Unavailable");
    render(await response.json());
  } catch {
    render({
      status: "UNAVAILABLE",
      reason: "Saved experimental sheet could not be loaded",
      games: [],
    });
  }
}
document.querySelector("#refresh").addEventListener("click", load);
load();
