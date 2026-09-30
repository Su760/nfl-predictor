/* Run with Playwright available via NODE_PATH; all writes stay in /tmp. */
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const base = process.env.FANTASY_PREVIEW_URL || "http://127.0.0.1:8520";

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(base + "/fantasy");
    await page.waitForSelector("#leaderboard tbody tr");
    const data = await (
      await page.request.get(base + "/api/fantasy?window=last3")
    ).json();
    assert.equal(data.season, 2026);
    assert.ok(data.players.length > 0);
    const wr = data.players.filter((p) => p.position === "WR");
    assert.equal(
      await page.locator("#leaderboard tbody tr").count(),
      wr.length,
    );
    const lead = [...wr]
      .filter((p) => p.metrics.targets.value !== null)
      .sort((a, b) => b.metrics.targets.value - a.metrics.targets.value)[0];
    assert.ok(
      (
        await page.locator("#leaderboard tbody tr").first().innerText()
      ).includes(lead.name),
    );
    await page.selectOption("#position", "RB");
    assert.equal(
      await page.locator("#leaderboard tbody tr").count(),
      data.players.filter((p) => p.position === "RB").length,
    );
    await page.selectOption("#metric", "carry_share");
    await page.selectOption("#position", "all");
    await page.fill("#search", lead.name);
    assert.equal(await page.locator("#leaderboard tbody tr").count(), 1);
    await page.fill("#search", "No such player ZZZ");
    assert.equal(await page.locator("#leaderboard tbody tr").count(), 0);
    assert.ok(await page.locator("#empty").isVisible());
    await page.fill("#search", "");
    const pair = data.players
      .filter((p) => p.observed_games === 3 && p.metrics.targets.value !== null)
      .slice(0, 2);
    assert.equal(pair.length, 2);
    await page.selectOption("#player-a", pair[0].player_id);
    await page.selectOption("#player-b", pair[1].player_id);
    assert.equal(await page.locator(".compare-table tbody tr").count(), 7);
    assert.ok(
      (await page.locator("#comparison").innerText()).includes(pair[0].name),
    );
    await page.selectOption("#window", "last");
    await page.waitForFunction(() =>
      document
        .querySelector(".compare-table")
        ?.textContent.includes("1/1 observed"),
    );
    await page.selectOption("#window", "season");
    await page.waitForFunction(() =>
      document
        .querySelector(".compare-table")
        ?.textContent.includes("3/3 observed"),
    );
    await page.screenshot({
      path: "/tmp/fantasy-player-lab-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: "/tmp/fantasy-player-lab-mobile.png",
      fullPage: true,
    });
    await page.selectOption("#player-b", pair[0].player_id);
    assert.equal(await page.locator("#player-a").inputValue(), "");
    await page.click("#clear");
    assert.equal(await page.locator(".compare-table").count(), 0);
    await page.setViewportSize({ width: 1440, height: 1000 });

    // Real TE trends and browser-local watchlist lifecycle.
    const tes = data.players.filter((p) => p.position === "TE");
    assert.ok(tes.length > 0, "verified current-season TEs must be ingested");
    await page.selectOption("#position", "TE");
    assert.equal(
      await page.locator("#leaderboard tbody tr").count(),
      tes.length,
    );
    const te = tes.find((p) =>
      data.trends[p.player_id].periods.some(
        (w) => w.metrics.target_share.delta_pp !== null,
      ),
    );
    await page.selectOption("#trend-player", te.player_id);
    const periods = data.trends[te.player_id].periods;
    assert.equal(
      await page.locator(".trend-table tbody tr").count(),
      periods.length,
    );
    for (let i = 0; i < periods.length; i++) {
      const w = periods[i],
        text = await page.locator(".trend-table tbody tr").nth(i).innerText();
      assert.ok(text.includes("Week " + w.week));
      for (const key of ["target_share", "carry_share", "snap_share"]) {
        const m = w.metrics[key];
        if (m.delta_pp !== null)
          assert.ok(
            text.includes(
              (m.delta_pp > 0 ? "+" : "") + m.delta_pp.toFixed(1) + " pp",
            ),
          );
        assert.ok(
          text.includes(m.covered_games + " / " + m.expected_games + " games"),
        );
      }
    }
    await page.selectOption("#player-a", te.player_id);
    await page.selectOption(
      "#player-b",
      tes.find((p) => p.player_id !== te.player_id).player_id,
    );
    assert.ok(
      (await page.locator("#comparison").innerText()).includes(te.name),
    );
    await page.locator(`#leaderboard [data-watch="${te.player_id}"]`).click();
    assert.equal(
      await page
        .locator(`#watchlist-members [data-watch="${te.player_id}"]`)
        .count(),
      1,
    );
    const storageKey = "nfl-player-lab.watchlist.v1";
    assert.deepEqual(
      await page.evaluate(
        (key) => JSON.parse(localStorage.getItem(key)).player_ids,
        storageKey,
      ),
      [te.player_id],
    );
    await page.reload();
    await page.waitForSelector("#leaderboard tbody tr");
    assert.equal(
      await page
        .locator(`#watchlist-members [data-watch="${te.player_id}"]`)
        .count(),
      1,
    );
    await page.selectOption("#position", "all");
    await page.check("#watchlist-only");
    assert.equal(await page.locator("#leaderboard tbody tr").count(), 1);
    const peer = await page.context().newPage();
    await peer.goto(base + "/fantasy");
    await peer.waitForSelector("#leaderboard tbody tr");
    await peer
      .locator(`#watchlist-members [data-watch="${te.player_id}"]`)
      .click();
    await page.waitForFunction(
      () =>
        document.querySelectorAll("#watchlist-members [data-watch]").length ===
        0,
    );
    assert.equal(await page.locator("#leaderboard tbody tr").count(), 0);
    await peer.close();
    await page.uncheck("#watchlist-only");
    await page.selectOption("#trend-player", te.player_id);
    await page.screenshot({
      path: "/tmp/fantasy-trends-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.selectOption("#position", "TE");
    await page.locator(`#leaderboard [data-watch="${te.player_id}"]`).click();
    await page.check("#watchlist-only");
    assert.equal(await page.locator("#leaderboard tbody tr").count(), 1);
    await page.locator("#leaderboard [data-trend]").click();
    assert.equal(
      await page.locator("#trend-player").inputValue(),
      te.player_id,
    );
    await page.screenshot({
      path: "/tmp/fantasy-trends-mobile.png",
      fullPage: true,
    });
    await page
      .locator(`#watchlist-members [data-watch="${te.player_id}"]`)
      .click();
    await page.uncheck("#watchlist-only");
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.evaluate(
      (key) =>
        localStorage.setItem(
          key,
          JSON.stringify({ version: 1, player_ids: ["00-9999999"] }),
        ),
      storageKey,
    );
    await page.reload();
    await page.waitForSelector("#leaderboard tbody tr");
    assert.ok(
      (await page.locator("#watchlist-members").innerText()).includes(
        "Unavailable player",
      ),
    );
    await page.locator('#watchlist-members [data-watch="00-9999999"]').click();
    await page.evaluate(
      (key) => localStorage.setItem(key, "{corrupt"),
      storageKey,
    );
    await page.reload();
    await page.waitForSelector("#leaderboard tbody tr");
    assert.ok(
      (await page.locator("#watchlist-status").innerText()).includes(
        "not overwritten",
      ),
    );
    await page.locator("#leaderboard [data-watch]").first().click();
    assert.equal(
      await page.evaluate((key) => localStorage.getItem(key), storageKey),
      "{corrupt",
    );
    await page.click("#watchlist-clear");
    assert.equal(
      await page.evaluate((key) => localStorage.getItem(key), storageKey),
      null,
    );
    const blocked = await browser.newPage();
    await blocked.addInitScript(() =>
      Object.defineProperty(window, "localStorage", {
        get() {
          throw new DOMException("Blocked", "SecurityError");
        },
      }),
    );
    await blocked.goto(base + "/fantasy");
    await blocked.waitForSelector("#leaderboard tbody tr");
    await blocked.locator("#leaderboard [data-watch]").first().click();
    assert.ok(
      (await blocked.locator("#watchlist-status").innerText()).includes(
        "unavailable",
      ),
    );
    assert.equal(
      await blocked.locator("#watchlist-members [data-watch]").count(),
      0,
    );
    await blocked.close();
    const full = await browser.newPage();
    await full.addInitScript(() => {
      Storage.prototype.setItem = function () {
        throw new DOMException("Full", "QuotaExceededError");
      };
    });
    await full.goto(base + "/fantasy");
    await full.waitForSelector("#leaderboard tbody tr");
    await full.locator("#leaderboard [data-watch]").first().click();
    assert.ok(
      (await full.locator("#watchlist-status").innerText()).includes(
        "not saved",
      ),
    );
    assert.equal(
      await full.locator("#watchlist-members [data-watch]").count(),
      0,
    );
    await full.close();
    await page.selectOption("#position", "all");

    // Synthetic responses exercise UI gaps; never written into live source cache.
    const missing = structuredClone(data);
    missing.state = "stale";
    missing.age_hours = 49;
    missing.refresh = {
      status: "failed",
      error: "HTTP 503 <script>",
      attempted_at: "2026-09-30T00:00:00Z",
    };
    missing.players = missing.players.slice(0, 2);
    for (const p of missing.players)
      for (const m of Object.values(p.metrics)) {
        m.value = null;
        m.covered_games = 0;
        m.expected_games = 3;
      }
    missing.players[0].name = "<img src=x onerror=alert(1)>";
    const missingPeriods = missing.trends[missing.players[0].player_id].periods;
    for (const period of missingPeriods) {
      period.observed_games = 0;
      period.status = "Player observation missing";
      for (const metric of Object.values(period.metrics))
        Object.assign(metric, {
          value: null,
          opportunities: null,
          team_opportunities: null,
          numerator: null,
          denominator: null,
          delta_pp: null,
          covered_games: 0,
          delta_reason: "Prior or current week unavailable",
        });
    }
    missingPeriods[1].status = "No scheduled game (bye or schedule gap)";
    missingPeriods[1].games = [];
    missingPeriods[1].game_ids = [];
    missingPeriods[1].expected_games = 0;
    for (const metric of Object.values(missingPeriods[1].metrics))
      metric.expected_games = 0;
    let response = missing;
    await page.route("**/api/fantasy*", (route) =>
      response === "fail"
        ? route.fulfill({ status: 503, body: "Unavailable" })
        : route.fulfill({ json: response }),
    );
    await page.click("#reload");
    await page.waitForFunction(() =>
      document
        .querySelector("#status")
        .textContent.includes("Last refresh failed"),
    );
    assert.equal(await page.locator("#leaderboard tbody tr").count(), 2);
    assert.ok(
      (await page.locator("#leaderboard tbody").innerText()).includes(
        "Unavailable",
      ),
    );
    assert.equal(await page.locator("#leaderboard img").count(), 0);
    assert.equal(await page.locator("#status script").count(), 0);
    await page.selectOption("#trend-player", missing.players[0].player_id);
    const missingTrend = await page.locator("#trends").innerText();
    assert.ok(missingTrend.includes("bye or schedule gap"));
    assert.ok(missingTrend.includes("Change unavailable"));
    assert.ok(missingTrend.includes("0 / 0 games"));
    assert.ok(missingTrend.includes("Unavailable / Unavailable"));
    assert.ok(!missingTrend.includes("0.0%"));
    assert.equal(await page.locator("#trends img").count(), 0);
    await page.selectOption("#player-a", missing.players[0].player_id);
    await page.selectOption("#player-b", missing.players[1].player_id);
    assert.ok(
      (await page.locator("#comparison").innerText()).includes("0 / 3 games"),
    );
    assert.ok(
      !(await page.locator("#comparison").innerText()).includes("0.0%"),
    );
    await page.screenshot({
      path: "/tmp/fantasy-player-lab-missing.png",
      fullPage: true,
    });
    response = "fail";
    await page.selectOption("#window", "last");
    await page.waitForSelector('#status [role="alert"]');
    await page.selectOption("#position", "WR");
    await page.fill("#search", "");
    assert.equal(
      await page.locator("#leaderboard tbody tr").count(),
      0,
      "filters must not restore the previous window after a failed request",
    );
    await page.selectOption("#position", "all");
    response = {
      ...missing,
      players: [],
      state: "unavailable",
      coverage: {},
      updated_at: null,
    };
    await page.click("#reload");
    await page.waitForFunction(() =>
      document.querySelector("#empty").textContent.includes("No verified"),
    );
    assert.equal(await page.locator("#leaderboard tbody tr").count(), 0);
    assert.equal(await page.locator(".compare-table").count(), 0);
    assert.ok(await page.locator("#empty").isVisible());
    assert.deepEqual(errors, []);
    console.log(
      "PASS: live WR/RB/TE, weekly counts/coverage/pp changes, comparisons, browser-local watchlist reload/cross-tab/mobile/remove/orphan/corrupt/blocked/quota checks, three windows, missing/stale/failed/empty data, escaping; zero JS errors",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
