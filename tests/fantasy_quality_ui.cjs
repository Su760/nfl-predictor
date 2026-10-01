/* Uses isolated Playwright browser storage; screenshots only in /tmp. */
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
    await page.waitForFunction(
      () => document.querySelector("#quality-player")?.options.length > 1,
    );
    const data = await (
      await page.request.get(base + "/api/fantasy?window=last3")
    ).json();
    for (const position of ["WR", "RB", "TE"]) {
      const p = data.players.find(
        (p) =>
          p.position === position && p.quality.points.ppr.actual.value !== null,
      );
      assert.ok(p);
      await page.selectOption("#quality-player", p.player_id);
      for (const scoring of ["standard", "half_ppr", "ppr"]) {
        await page.selectOption("#scoring", scoring);
        const text = await page.locator("#quality-view").innerText();
        assert.ok(
          text.includes(p.quality.points[scoring].actual.value.toFixed(2)),
        );
        assert.ok(text.includes("3 / 3 games"));
        if (
          data.opportunity_method.display_supported &&
          p.quality.points[scoring].expected.value !== null
        )
          assert.ok(
            text.includes(p.quality.points[scoring].expected.value.toFixed(2)),
          );
      }
    }
    const lateral = data.players.find((p) =>
      p.quality.games.some((g) => g.reason === "outcome_reconciliation"),
    );
    assert.ok(lateral);
    await page.selectOption("#quality-player", lateral.player_id);
    assert.ok(
      (
        await page.locator("#quality-view [data-expected]").first().innerText()
      ).includes("Unavailable"),
    );
    assert.ok(
      (await page.locator("#quality-view").innerText()).includes(
        lateral.quality.points.ppr.actual.value.toFixed(2),
      ),
    );
    const gap = data.players.find(
      (p) => p.quality.points.ppr.actual.covered_games === 0,
    );
    await page.selectOption("#quality-player", gap.player_id);
    assert.ok(
      (await page.locator("#quality-view").innerText()).includes("Unavailable"),
    );
    const te = data.players.find((p) => p.name === "Trey McBride");
    await page.selectOption("#quality-player", te.player_id);
    await page.locator("#quality-title").scrollIntoViewIfNeeded();
    await page.screenshot({
      path: "/tmp/fantasy-quality-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.selectOption("#scoring", "half_ppr");
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: "/tmp/fantasy-quality-mobile.png",
      fullPage: true,
    });
    await page
      .locator(".quality")
      .screenshot({ path: "/tmp/fantasy-quality-panel-mobile.png" });
    await page.selectOption("#window", "last");
    await page.waitForFunction(() =>
      document
        .querySelector("#quality-view")
        .textContent.includes("1 / 1 games"),
    );
    // Synthetic gaps affect only this browser, not the cache or evaluation artifacts.
    const missing = structuredClone(data);
    const p = missing.players.find((p) => p.player_id === te.player_id);
    for (const metric of Object.values(p.quality.metrics)) {
      metric.value = null;
      metric.covered_games = 0;
    }
    for (const score of Object.values(p.quality.points)) {
      score.actual.value = null;
      score.expected.value = null;
      score.difference = null;
    }
    missing.opportunity_method.display_supported = false;
    missing.opportunity_method.status = "Research only: fixed gates failed";
    await page.route("**/api/fantasy*", (route) =>
      route.fulfill({ json: missing }),
    );
    await page.click("#reload");
    await page.waitForFunction(() =>
      document
        .querySelector("#quality-method")
        .textContent.includes("Research only"),
    );
    let text = await page.locator("#quality-view").innerText();
    assert.ok(text.includes("Unavailable"));
    assert.ok(!text.includes("0.00"));
    assert.equal(
      await page.locator("#quality-view [data-expected]").count(),
      0,
    );
    await page.unroute("**/api/fantasy*");
    await page.route("**/api/fantasy*", (route) =>
      route.fulfill({ status: 503, body: "Unavailable" }),
    );
    await page.click("#reload");
    await page.waitForSelector('#status [role="alert"]');
    await page.selectOption("#scoring", "ppr");
    assert.ok(
      (await page.locator("#quality-view").innerText()).includes("unavailable"),
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS: WR/RB/TE quality, three scoring formats, expected-point gate, windows, missing/research/failed states, desktop/mobile, zero JS errors",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
