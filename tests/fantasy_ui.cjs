/* Run with Playwright available via NODE_PATH; all writes stay in /tmp. */
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const base = process.env.FANTASY_PREVIEW_URL || "http://127.0.0.1:8520";

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
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
      "PASS: live WR/RB, sorting, search, three windows, comparison, duplicate selection, desktop/mobile, null/stale/failed/empty data, escaping; zero JS errors",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
