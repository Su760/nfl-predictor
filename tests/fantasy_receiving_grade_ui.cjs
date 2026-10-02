const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const base = process.env.FANTASY_PREVIEW_URL || "http://127.0.0.1:8520";
const key = "nfl-player-lab.predictions.v1";
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    const page = await context.newPage(),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const real = await (
      await page.request.get(base + "/api/fantasy/receiving-grade")
    ).json();
    assert.equal(real.state, "incomplete");
    const primary = real.versions.find((v) => v.primary);
    assert.equal(primary.groups.ALL.forecasted, 273);
    assert.equal(primary.groups.ALL.pending, 273);
    assert.equal(primary.groups.ALL.models.model.targets.mae, null);
    await page.goto(base + "/fantasy");
    await page.waitForFunction(() =>
      document
        .querySelector("#grading-coverage")
        .textContent.includes("273 pending"),
    );
    assert.equal(
      await page.locator("#grading-version").inputValue(),
      real.primary_id,
    );
    assert.ok(
      (await page.locator("#grading-table").innerText()).includes(
        "Unavailable",
      ),
    );
    const calls = JSON.stringify({
      entries: [
        { revision: 1, player_id: "stable-id", values: { targets: 0 } },
      ],
    });
    await page.evaluate(([k, v]) => localStorage.setItem(k, v), [key, calls]);
    const fixture = structuredClone(real);
    fixture.stale = true;
    fixture.captures = [
      {
        file: "new",
        capture_sequence: 2,
        source_revision: 2,
        outcome_captured_at: "2026-10-07T12:00:00Z",
      },
      {
        file: "old",
        capture_sequence: 1,
        source_revision: 1,
        outcome_captured_at: "2026-10-06T12:00:00Z",
      },
    ];
    const v = fixture.versions[0];
    v.groups = { ALL: structuredClone(v.groups.ALL) };
    Object.assign(v.groups.ALL, {
      observed: 1,
      pending: 270,
      missing: 1,
      invalid: 1,
    });
    for (const model of ["model", "rolling"])
      for (const m of ["targets", "receptions", "receiving_yards"])
        Object.assign(v.groups.ALL.models[model][m], {
          n: 1,
          mae: model === "model" ? 2 : 4,
          rmse: model === "model" ? 2 : 4,
          bias: 2,
          interval_n: model === "model" ? 1 : 0,
          interval_missing: model === "model" ? 0 : 1,
          interval_coverage: model === "model" ? 1 : null,
          interval_width: model === "model" ? 4 : null,
        });
    v.records = [
      {
        name: "Zero fixture",
        player_id: "stable-id",
        game_id: "g",
        position: "WR",
        tier: "low",
        horizon: "24–<48h",
        status: "observed",
        actual: { targets: 0, receptions: 0, receiving_yards: 0 },
        reason: "Numeric published outcome",
      },
      {
        name: "Missing fixture",
        position: "TE",
        tier: "low",
        horizon: "≥72h",
        status: "missing",
        actual: null,
        reason: "No outcome row; DNP/no-stat/unknown",
      },
    ];
    const other = structuredClone(v);
    other.id = "other";
    other.primary = false;
    other.groups.ALL.models.model.targets.mae = 99;
    fixture.versions = [v, other];
    let requested = "";
    await page.route("**/api/fantasy/receiving-grade*", (route) => {
      requested = route.request().url();
      const body = structuredClone(fixture);
      if (requested.includes("capture=old")) {
        body.source_revision = 1;
        body.capture_sequence = 1;
      }
      return route.fulfill({ json: body });
    });
    await page.click("#grading-reload");
    await page.waitForFunction(() =>
      document
        .querySelector("#grading-coverage")
        .textContent.includes("1/273 observed"),
    );
    assert.ok(
      (await page.locator("#grading-status").innerText()).includes(
        "Stale outcomes",
      ),
    );
    assert.ok(
      (await page.locator("#grading-table").innerText()).includes("100.00%"),
    );
    assert.ok(
      (await page.locator("#grading-table").innerText()).includes(
        "0 (1 unavailable)",
      ),
    );
    await page
      .getByText("Player-game outcomes and exclusions", { exact: true })
      .click();
    const records = await page.locator("#grading-records").innerText();
    assert.ok(records.includes("0.00 / 0.00 / 0.00"));
    assert.ok(records.includes("Unavailable / Unavailable / Unavailable"));
    await page.selectOption("#grading-version", "other");
    assert.ok(
      (await page.locator("#grading-coverage").innerText()).includes(
        "Separate archived version",
      ),
    );
    assert.ok(
      (await page.locator("#grading-table").innerText()).includes("99.00"),
    );
    await page.selectOption("#grading-capture", "old");
    await page.waitForFunction(
      () => document.querySelector("#grading-version").value !== "other",
    );
    assert.ok(requested.includes("capture=old"));
    assert.equal(
      await page.locator("#grading-version").inputValue(),
      real.primary_id,
    );
    await page
      .getByText("Outcome receipts and version history", { exact: true })
      .click();
    assert.ok(
      (await page.locator("#grading-provenance").innerText()).includes(
        "source revision 1",
      ),
    );
    await page.screenshot({
      path: "/tmp/fantasy-grade-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    const wrap = page.locator("#grading-table .table-wrap");
    assert.ok(await wrap.evaluate((e) => e.scrollWidth > e.clientWidth));
    await wrap.evaluate((e) => {
      e.scrollLeft = 400;
    });
    assert.ok(await wrap.evaluate((e) => e.scrollLeft > 0));
    await page.screenshot({
      path: "/tmp/fantasy-grade-mobile.png",
      fullPage: true,
    });
    await page.unroute("**/api/fantasy/receiving-grade*");
    await page.route("**/api/fantasy/receiving-grade*", (route) =>
      route.fulfill({
        json: { state: "unavailable", error: "checksum mismatch" },
      }),
    );
    await page.click("#grading-reload");
    await page.waitForFunction(() =>
      document
        .querySelector("#grading-status")
        .textContent.includes("checksum mismatch"),
    );
    assert.equal(await page.locator("#grading-table").innerText(), "");
    assert.equal(
      await page.evaluate((k) => localStorage.getItem(k), key),
      calls,
    );
    assert.deepEqual(errors, []);
    console.log(
      "Grading desktop/mobile: primary, paired metrics, zero/missing, interval denominators, separate versions, corrections, stale/error state and personal-call preservation passed",
    );
    await context.close();
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
