/* Synthetic cases are browser-only; the live sheet is checked against its real API. */
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const base = process.env.FANTASY_PREVIEW_URL || "http://127.0.0.1:8520";
const storageKey = "nfl-player-lab.predictions.v1";
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
      await page.request.get(base + "/api/fantasy/receiving")
    ).json();
    assert.notEqual(real.state, "unavailable", JSON.stringify(real));
    const row = real.rows.find(
      (r) => r.position === "TE" && Date.parse(r.kickoff) > Date.now(),
    );
    assert.ok(row);
    await page.goto(base + "/fantasy");
    await page.waitForFunction(
      () => document.querySelector("#prediction-player").options.length > 1,
    );
    await page.selectOption("#receiving-filter", "TE");
    const sheetText = await page.locator("#receiving-sheet").innerText();
    assert.ok(sheetText.includes(row.name));
    assert.ok(sheetText.includes(row.estimate.targets.toFixed(1)));
    assert.ok(sheetText.includes("availability is unverified"));
    assert.ok(sheetText.includes("hours before kickoff"));
    assert.equal(
      await page
        .locator(".receiving-table tbody td:last-child")
        .first()
        .evaluate((node) => getComputedStyle(node).whiteSpace),
      "normal",
    );
    await page
      .getByText("Retrospective benchmark and interval coverage", {
        exact: true,
      })
      .click();
    assert.ok(
      (await page.locator("#receiving-benchmark").innerText()).includes(
        "DNP/no-stat/unknown",
      ),
    );
    await page
      .getByText("Retrospective benchmark and interval coverage", {
        exact: true,
      })
      .click();
    await page.selectOption(
      "#prediction-player",
      row.game_id + "|" + row.player_id,
    );
    async function save(targets, catches, yards) {
      await page.fill("#prediction-targets", String(targets));
      await page.fill("#prediction-catches", String(catches));
      await page.fill("#prediction-yards", String(yards));
      await page.click("#prediction-save");
    }
    await save(7, 4, 50);
    await save(8, 5, 60);
    let saved = await page.evaluate(
      (k) => JSON.parse(localStorage.getItem(k)),
      storageKey,
    );
    assert.equal(saved.entries.length, 2);
    assert.equal(saved.entries[0].values.targets, 7);
    assert.equal(saved.entries[1].revision, 2);
    assert.ok(
      saved.entries.every(
        (v) => Date.parse(v.created_at) < Date.parse(v.kickoff),
      ),
    );
    await page.reload();
    await page.waitForFunction(() =>
      document.querySelector("#prediction-history").textContent.includes("v2"),
    );
    assert.ok(
      (await page.locator("#prediction-history").innerText()).includes(
        "Unavailable",
      ),
    );
    await page.selectOption(
      "#prediction-player",
      row.game_id + "|" + row.player_id,
    );
    await save(2, 3, 30);
    assert.ok(
      (await page.locator("#prediction-status").innerText()).includes(
        "0 ≤ catches ≤ targets",
      ),
    );
    assert.equal(
      await page.evaluate(
        (k) => JSON.parse(localStorage.getItem(k)).entries.length,
        storageKey,
      ),
      2,
    );
    await page.evaluate(() => {
      Storage.prototype.setItem = () => {
        throw Error("quota");
      };
    });
    await save(9, 5, 65);
    assert.ok(
      (await page.locator("#prediction-status").innerText()).includes(
        "not saved",
      ),
    );
    await page.reload();
    await page.waitForFunction(
      () => document.querySelector("#player-a").options.length > 2,
    );
    const usage = await (
      await page.request.get(base + "/api/fantasy?window=last3")
    ).json();
    const complete = usage.players
      .filter((p) => p.metrics.targets.value !== null && p.expected_games > 1)
      .slice(0, 2);
    await page.selectOption("#player-a", complete[0].player_id);
    await page.selectOption("#player-b", complete[1].player_id);
    const before = await page.locator("#comparison").innerText();
    await page.selectOption("#comparison-mode", "per-game");
    assert.ok(
      (await page.locator("#comparison").innerText()).includes(
        "Targets / game",
      ),
    );
    assert.notEqual(await page.locator("#comparison").innerText(), before);
    const missing = usage.players.find((p) => p.metrics.targets.value === null);
    await page.selectOption("#player-a", missing.player_id);
    assert.ok(
      (await page.locator("#comparison").innerText()).includes("Unavailable"),
    );
    await page.screenshot({
      path: "/tmp/fantasy-receiving-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.selectOption("#receiving-filter", "all");
    await page.selectOption(
      "#prediction-player",
      row.game_id + "|" + row.player_id,
    );
    await save(9, 6, 70);
    assert.equal(
      await page.evaluate(
        (k) => JSON.parse(localStorage.getItem(k)).entries.length,
        storageKey,
      ),
      3,
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    await page.screenshot({
      path: "/tmp/fantasy-receiving-mobile.png",
      fullPage: true,
    });

    // Published numeric zero and missing outcome cases are injected only into this browser.
    const fixture = structuredClone(real);
    fixture.outcomes = {
      source: { captured_at: new Date().toISOString() },
      rows: [
        {
          player_id: row.player_id,
          game_id: row.game_id,
          status: "observed",
          targets: 0,
          receptions: 0,
          receiving_yards: 0,
        },
      ],
    };
    await page.route("**/api/fantasy/receiving", (route) =>
      route.fulfill({ json: fixture }),
    );
    await page.click("#receiving-reload");
    await page.waitForFunction(() =>
      document
        .querySelector("#prediction-history")
        .textContent.includes("Call minus actual"),
    );
    assert.ok(
      (await page.locator("#prediction-history").innerText()).includes(
        "7.0 / 0.0 / 7.0",
      ),
    );
    // Advancing the browser clock locks form saves at the captured kickoff.
    await page.evaluate((kick) => {
      Date.now = () => Date.parse(kick) + 1;
    }, row.kickoff);
    await page.selectOption(
      "#prediction-player",
      row.game_id + "|" + row.player_id,
    );
    await save(10, 6, 80);
    assert.ok(
      (await page.locator("#prediction-status").innerText()).includes(
        "kickoff has passed",
      ),
    );
    assert.equal(
      await page.evaluate(
        (k) => JSON.parse(localStorage.getItem(k)).entries.length,
        storageKey,
      ),
      3,
    );
    await page.unroute("**/api/fantasy/receiving");
    await page.route("**/api/fantasy/receiving", (route) =>
      route.fulfill({
        json: { state: "unavailable", rows: [], error: "source failure" },
      }),
    );
    await page.click("#receiving-reload");
    await page.waitForFunction(() =>
      document
        .querySelector("#receiving-status")
        .textContent.includes("source failure"),
    );
    assert.ok(
      !(await page.locator("#receiving-sheet").innerText()).includes(row.name),
    );
    assert.ok(
      (await page.locator("#prediction-history").innerText()).includes("v1"),
    );
    await page.evaluate((k) => localStorage.setItem(k, "corrupt"), storageKey);
    await page.reload();
    assert.ok(
      (await page.locator("#prediction-status").innerText()).includes(
        "not overwritten",
      ),
    );
    assert.equal(
      await page.evaluate((k) => localStorage.getItem(k), storageKey),
      "corrupt",
    );
    const blocked = await browser.newContext({
      viewport: { width: 390, height: 844 },
    });
    await blocked.addInitScript(() => {
      Storage.prototype.getItem = () => {
        throw Error("blocked");
      };
    });
    const blockedPage = await blocked.newPage();
    await blockedPage.goto(base + "/fantasy");
    assert.ok(
      (await blockedPage.locator("#prediction-status").innerText()).includes(
        "blocked",
      ),
    );
    assert.deepEqual(errors, []);
    await blocked.close();
    await context.close();
    console.log(
      "Receiving desktop/mobile, real values, comparison toggle, append-only reload, missing/zero, kickoff locks, corruption and quota checks passed",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
