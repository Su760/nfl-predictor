# Player Lab milestone 1

Local preview: <http://127.0.0.1:8520/fantasy>, branch `codex/fantasy-player-lab`.
Historical WR/RB usage only: targets, carries, receiving air yards, target/carry/snap
shares and verified red-zone opportunities. Three viewing windows, searchable and
sortable leaderboards, two-player comparisons, metric definitions, sample counts,
source coverage and timestamps. ESPN sync, projections/rankings, trades, chat and
sports betting remain deferred. The September 30 closeout authorizes a selective
milestone commit and normal push to `origin/codex/fantasy-player-lab`. No merge or
deployment is authorized; the worktree and its unrelated edits are preserved.

## Isolated operation

Run from `/Users/supashramesha/Desktop/nfl-fantasy`:

```sh
uv venv .venv-fantasy --python 3.11
uv pip install --cache-dir .fantasy-cache/uv --python .venv-fantasy/bin/python -r fantasy/requirements.txt
.venv-fantasy/bin/python -m fantasy.sources
.venv-fantasy/bin/python ops/week1_viewer.py --fantasy
```

The last command enters the fantasy server before importing any forecast configuration.
It binds `127.0.0.1:8520`; it neither starts nor restarts a forecast worker. The page
and API also have integration routes in the existing viewer. The fantasy preview's
NFL link points to the existing viewer on port 8510, configured in `configs/fantasy.toml`.
That viewer was not restarted, so its running navigation need not yet show Player Lab.

All settings and URLs are in `configs/fantasy.toml`. `FANTASY_CONFIG` may select another
TOML file; its cache must resolve inside this worktree and bind must be loopback.
No dependencies are installed into the NFL environment. `.venv-fantasy/` and
`.fantasy-cache/` are ignored. Raw files are content-addressed and receipts retain
SHA-256, source URL, capture time and HTTP Last-Modified. `snapshot.json` and
`refresh.json` are atomically replaced. No private NFL archive is read or written.

Source refresh is manual. “Reload saved data” reads the snapshot, without downloading
providers. A required-source failure preserves the previous snapshot and records the
failed attempt. An optional-source failure produces unavailable affected metrics;
old bytes are not silently reused. Snapshot age over the configured 24 hours is
labeled stale. Snapshot freshness measures local capture age, not provider latency.

## Calculation contract

- Last game: latest observed team's most recent eligible completed game.
- Last three: that team's latest up-to-three eligible completed games.
- Season: all that team's eligible completed regular-season games.
- Byes do not consume a game. Missed games are not replaced by earlier appearances.
  Missing rows cannot distinguish inactivity/injury from missing provider data and
  are never zero-filled. Published numeric zeros remain zeros.
- Full-window totals and shares require complete metric coverage. Each cell shows
  covered / expected games; an incomplete result stays unavailable. Short early-season
  history uses its actual sample. Comparison samples and weeks are shown separately.
- Target share is summed player targets / summed team targets. Carry share uses all
  team carries, including QBs. Multi-game shares never average weekly percentages.
- Snap share uses summed offensive snaps / summed team offensive snaps. PFR IDs join
  through a unique GSIS↔PFR mapping. A team denominator is accepted only if a single
  integer agrees with every team's player count and its rounded two-decimal PFR
  fraction. Ambiguous denominators/IDs remain unavailable. Snaps are never routes.
- Air yards are credited receiving air yards on targets, including incompletions;
  negative values remain valid.
- Red-zone opportunities are credited targets + carries starting at or inside the
  opponent's 20. Deleted/no-play/two-point plays are excluded. Complete PBP must have
  `END GAME`, unique play IDs, known opportunity yardlines, and player target/carry
  counts exactly matching the box-score row. Only then can a zero be established.
- The schedule lacks an explicit final flag. Only scored games dated before the
  current UTC date are eligible; same-day/live games are excluded. This is a daily
  historical usage view, not a live score feed.
- Team is latest observed team, not a verified current roster. Detected transfers
  withhold full-season values without dated roster history; short windows use the
  latest observed team. Players absent from both stats and matched snap records are
  outside the catalog. No transfer is present in the audited WR/RB box-score sample.

## Verified 2026 source coverage

Snapshot built **2026-09-30 18:39:22 UTC (13:39:22 CDT)**. Actual retrieval/capture
times span **18:39:20.125–18:39:22.566 UTC (13:39:20–22 CDT)** that day. These are
local retrieval times, not source publication times. The HTTP Last-Modified values
below describe provider file modification; an exact publication timestamp is not
supplied. Current-season files were downloaded directly; no prior-season or fixture
fallback is used. Closeout inspected these saved bytes without refreshing them.

Latest represented completed game: **2026_03_PHI_CHI**, Philadelphia at Chicago,
September 28, 2026, final **PHI 7–CHI 27**. The snapshot has 17 WR/RB observations
for that game. The source schedule plus completed PBP establish the represented game;
the snapshot build time does not imply any later game is included.

| Source                | Captured rows | Provider file modification (UTC) |
| --------------------- | ------------: | -------------------------------- |
| Weekly player stats   |         3,339 | Sep 30 16:25:46                  |
| Weekly team stats     |            96 | Sep 30 16:25:48                  |
| Play-by-play          |         8,311 | Sep 30 16:15:15                  |
| PFR snap counts       |         4,488 | Sep 29 11:01:26                  |
| Player ID registry    |        24,834 | Sep 29 14:32:37                  |
| Schedule, all seasons |         7,548 | Not supplied                     |

Normalized coverage: **48 completed games, Weeks 1–3; 313 WR/RB players; 801
player-game observations**, against 272 scheduled regular-season games. There are
731 WR/RB box-score rows with targets/carries/air yards populated and 70 additional
snap-only observations. Those 70 keep box-score usage and red-zone counts unavailable.
Three rows lack matched snap evidence, all Cody White (`00-0035891`), Weeks 1–3.
The registry's PFR ID for him is blank. Snap records named Cody White exist under
`WhitCo05` (11, 7 and 15 offensive snaps), but no verified ID bridge connects them.
The app correctly declines a name-only match and shows snap share unavailable.
The 70 snap-only observations span 54 players; they are additional player-game
observations, not 70 different players. Their verified snaps can be shown while
targets/carries/air yards/red-zone opportunities and related shares remain null.
Snapshot null rates: targets/carries/air yards/red-zone 70/801 (8.74%) each;
snaps/team snap denominators 3/801 (0.37%) each. These rates exclude entirely absent
player-game observations. Full three-game coverage: 185 players for box-score and
red-zone metrics; 218 for snap share. Other players remain visible with coverage gaps.

| Metric family                                       | Populated player-game observations | Complete last-game players | Complete last-three / season players |
| --------------------------------------------------- | ---------------------------------: | -------------------------: | -----------------------------------: |
| Targets, carries, air yards, red-zone opportunities |                     731 / 801 each |             244 / 313 each |                       185 / 313 each |
| Target share and carry share (paired counts)        |                     731 / 801 each |             244 / 313 each |                       185 / 313 each |
| Snap share (paired counts)                          |                          798 / 801 |                  268 / 313 |                            218 / 313 |

Last-three and season happen to cover the same games at this three-week snapshot;
they are not independent comparison periods. Neither is treated as a change baseline.

Routes and route-based metrics are unavailable. Exact game-end-to-publication latency
cannot be inferred from HTTP file modification/capture timestamps; no such latency
is claimed. Provider schedules describe intended cadence, not guaranteed availability.
The older `player_stats/player_stats_2026.csv` URL returned HTTP 404; the current
`stats_player/stats_player_week_2026.parquet` asset is used instead.

Definitions and schedules were checked against the provider's
[player stats documentation](https://nflreadr.nflverse.com/reference/load_player_stats.html),
[snap dictionary](https://nflreadr.nflverse.com/articles/dictionary_snap_counts.html),
[PBP dictionary](https://nflreadr.nflverse.com/articles/dictionary_pbp.html), and
[availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).
Source URLs and receipts are also visible in the app.

## Validation

```sh
.venv-fantasy/bin/python -m pytest tests/test_fantasy_usage.py tests/test_fantasy_sources.py tests/test_fantasy_server.py tests/test_week1_viewer.py -q -o cache_dir=.fantasy-cache/pytest
.venv-fantasy/bin/ruff check --no-cache fantasy tests/test_fantasy_usage.py tests/test_fantasy_sources.py tests/test_fantasy_server.py ops/week1_viewer.py
node --check ops/viewer/fantasy.js
NODE_PATH=/Users/supashramesha/.agents/skills/gstack/node_modules node tests/fantasy_ui.cjs
git diff --check
```

37 focused Python tests passed, including the existing viewer checks. Browser checks
passed at 1440px and 390px: WR/RB filters, ordering/search, all windows, two-player
selection and clearing, duplicate-player prevention, null/stale/failed/empty source
responses, HTML escaping, failed-window recovery, no page overflow and zero JS errors.
Playwright is used from the existing local browser tooling; set `NODE_PATH` to another
installation if needed. Synthetic responses are injected only into the test browser,
never persisted as live data. Screenshots are under `/tmp/fantasy-player-lab-*.png`.

Independent raw-file arithmetic checked Chris Olave targets **36/124 = 29.032%** and
Bijan Robinson carries **66/104 = 63.462%**, matching the app. Parser failure during
initial real ingestion was traced to three non-player records with null IDs; filtering
to WR/RB before identity validation fixed it after a failing regression. Browser
regression reproduced stale data reappearing after a failed window request; clearing
the old snapshot at request start fixed it. Both regressions now pass.

Initial network/server/browser runs were denied by sandbox permissions and were
rerun with approved access. PyArrow emitted nonfatal sandbox CPU-info warnings during
read-only audits. No full forecasting suite, worker cycle, model fitting or production
restart was run; focused checks are the validation boundary for this milestone.

## September 30 closeout

Revalidated the healthy preview, all six saved source hashes, actual retrieval times,
latest represented game and the complete missing-data sets. The 37 focused Python
tests, lint, JavaScript syntax and existing desktop/mobile browser suite passed again.
A live browser audit additionally verified **all 313 rows and 863 unavailable metric
cells**, including coverage labels and Cody White/Brenen Thompson in comparison.
No missing value was rendered as zero and no code fix was necessary in this closeout.
No data refresh, forecast worker, forecast configuration/model/archive edit or restart
was performed. The previously running preview was reused.

The roadmap in `tasks/todo.md` now specifies weekly usage trends, TE support and a local
watchlist next; disjoint weekly/three-game comparison periods with explicit samples,
coverage and summed share denominators; opportunity quality and scoring-specific
expected points then; and ESPN context, waivers/trades and evaluated weekly/rest-of-season
projections later. None of those future features is implemented here.

Remaining data limitations (not closeout blockers): missing usage in snap-only records,
the Cody White ID bridge, routes, dated roster history and exact provider publication
latency. Any agent stop-hook warning is separate tooling status; no stop-hook failure
was observed in the validation commands and it is not evidence of a Player Lab failure.

### Unrelated edits preserved outside the milestone commit

Read-only formatting normalization found the following 21 whole-file diffs equivalent
to HEAD. JSON files were also compared as parsed values. They remain unstaged:

```text
.github/workflows/nfl-v2-dispatch.yml
.vscode/settings.json
CLAUDE.md
README.md
deploy/private-data-repo/.github/workflows/capture-and-forecast.yml
deploy/private-data-repo/.github/workflows/private-due-check.yml
deploy/private-data-repo/.github/workflows/settle-and-report.yml
deploy/private-data-repo/config/dispatch-windows-2026.json
deploy/schedules/dispatch-windows-2026.json
docs/runbooks/2026-09-15-week1-audit.md
docs/runbooks/2026-09-16-cutoff-native.md
docs/runbooks/2026-09-16-elo-epa-residual.md
docs/runbooks/2026-09-16-football-insights.md
docs/runbooks/season-live.md
docs/superpowers/plans/2026-09-10-season-forecasting.md
features_schema.json
ops/viewer/app.js
ops/viewer/insights.js
ops/viewer/styles.css
tasks/lessons.md
tests/fixtures/odds/nfl_h2h.json
```

Two mixed files retain formatting-only remainders: `ops/viewer/index.html` and
`tasks/todo.md`. Only the Player Lab navigation link and fantasy plan/roadmap section
are selected into the index, reconstructed on their HEAD versions. Working copies
retain all formatting. Total expected uncommitted tracked files after closeout: **23**.
Environment, source bytes, receipts, cache, screenshots and local data are excluded.
