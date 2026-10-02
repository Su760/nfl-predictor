# Season simulation

Run only after the season worker has produced a healthy `view.json`:

```sh
uv run python ops/season_simulation.py --config configs/season_simulation.toml
```

The CLI reads the private season view and its exact content-addressed historical CSV,
verifies the CSV and Elo policy hashes, and writes an immutable private
`season/simulations/<sha256>.json`. Exit 0 means all requested samples completed;
exit 2 means BLOCKED and `team_probabilities` is null. Repeating the same inputs and
seed yields the same result. There is no network or paid service in this command.
No forecast is backdated. Cutoff is the view's observed update timestamp.

`simulate(view, cfg)` accepts `cfg.history_rows` and `cfg.elo` injected by the CLI;
the TOML contains the 32-team alignment, sample count, seed and sensitivity scale.
The output includes view/config/model/source hashes, sample count, seed, preserved
final count, limitations and Monte Carlo sampling error (not model accuracy).
`conference` means conference champion; `super_bowl` means Super Bowl winner.

## Model and uncertainty

Every draw independently perturbs each current Elo rating by a zero-mean normal
with configured standard deviation 65 Elo points. This is an explicitly
**uncalibrated sensitivity distribution**, not a learned posterior. Strength is
fixed within a draw. Game outcomes use existing Elo home/neutral probabilities
and the historical regular-season tie layer. The postseason uses decisive Elo
probabilities with no tie outcome. Future injury/lineup/weather changes are unknown;
current injuries are not projected as season-long known facts. Live games without
final results are simulated without conditioning on live score, so this is not an
in-play model.

Scores are sampled from historical regular-season score pairs from 2016 through
the season before the target season, conditional on decisive result versus tie;
the higher score goes to the already sampled winner. This preserves coherent
observed score pairs, but is a pooled score model, not a matchup-specific scoring
or total model. Current-season final scores stay fixed. Current/future-season CSV
scores never enter that score pool. No touchdown count is inferred from points.

If a standings tie reaches net touchdowns without complete touchdown counts,
**the entire run is BLOCKED** with the tied teams listed. No draws are dropped,
no early coin toss is substituted, and no numerical probabilities are published.
This may conservatively block on a lower division rank needed to establish the
persistent within-division order. Supply verified touchdown totals through an
explicit future data-contract change; do not relax this guard to get a result.

## Rules verification (2026-09-10)

The implementation follows the ordered criteria and restart rules in the
[NFL tiebreak procedures](https://www.nfl.com/standings/tie-breaking-procedures).

The [2026 schedule announcement](https://nfl-ops-prod-umbraco-author.azurewebsites.net/news-updates/the-game/2026-nfl-schedule-announced/)
confirms 272 games, 17 games per club, seven qualifiers per conference and the
first seed's bye. The announcement's indexed NFL source was readable; its direct
page returned 403. The current
[NFL division standings](https://www.nfl.com/standings/division/2026/REG)
verify the alignment; the repository uses `LA` for the Rams.

The [official NFL Record & Fact Book](https://static.clubs.nfl.com/image/upload/patriots/fvc6qgwyqlztq1muztpi.pdf)
defines division champions as seeds 1–4, wildcards 5–7, first-round pairings
2–7, 3–6 and 4–5, and the divisional high-versus-low pairing. Higher seeds host
within their conference. The Super Bowl is modeled as neutral-site.

[2026 rulebook](https://static.www.nfl.com/image/upload/fl_attachment/league/tqivdkzt9mu6wdgsh1ku.pdf),
Rule 16 §1 Article 4, requires postseason overtime to continue until a winner;
regular-season ties remain possible. Games in the simulator therefore cannot tie
in the postseason.

## Verification

```sh
uv run pytest -q -p no:cacheprovider tests/test_season_simulation.py
uv run ruff check ops/season_simulation.py tests/test_season_simulation.py
```

Tests cover fractional tied records, weighted opponent strength, elimination
restart, multi-club sweep, four common games, competition points rankings,
late missing-input blocking, coin only after known criteria, reseeding, home and
neutral context, postseason no ties, final preservation, future-input exclusion,
reproducibility and uncertainty. Completed probability totals must be 14 playoff
berths, eight division winners, two first seeds, two conference winners and one
Super Bowl winner. This validates implementation consistency, not forecast calibration.

## Refresh and Playoff Picture viewer

Simulation is **standalone/manual**. Checked-in `configs/season_live.toml`
keeps `simulation_enabled = false`; the forecast/shadow collection cycle
does not run draws. A healthy viewer does not imply fresh simulation output.
Neither opening Playoff Picture nor its reload button starts a simulation.

From the V2 repository, after a healthy normal source cycle:

```bash
uv run python ops/season_simulation.py --config configs/season_simulation.toml
# Equivalent with the already installed project environment:
.venv/bin/python ops/season_simulation.py --config configs/season_simulation.toml
```

The command verifies the exact captured history CSV and frozen Elo policy,
uses `view.json` as its cutoff, and preserves completed final scores. Keep
all configured 2,000 draws. Measure wall time before considering more samples;
do not refit or retune the strength sensitivity. Exit 2 is a valid BLOCKED
artifact, with no probability distribution. Do not discard failing draws,
invent touchdown counts, or advance to an earlier coin toss to get output.
If an unchanged blocked snapshot is reused, rerunning cannot resolve its
evidence limitation. A verified new data/rules contract is separate work.

The loopback viewer serves `/playoffs` and **read-only** `/api/playoffs`. The
page includes all 32 current records and five probabilities, conference
filters, sorting, the model favorite and the full Super Bowl distribution.
It distinguishes the current record cutoff from the probability cutoff.
`COMPLETE` means every requested draw finished; `STALE` retains a labeled
complete historical distribution; `BLOCKED` shows the exact saved reason
and requested draw count without percentages; `ABSENT` asks for a standalone
refresh. A new blocked run never falls back silently to older probabilities.
Invalid artifact hashes or distributions also block publication.

Freshness uses the **same evidence signature as the simulator**: configured
settings, history hash, simulator/Elo source hashes, model snapshot ID, and
material inputs plus schedule/results. Schedule identity includes teams,
neutral venue, kickoff and version. Unchanged material evidence reuses its
hash-verified immutable artifact even when a heartbeat updates view time.
A stale current source check is separately flagged, using the live config's
existing maximum capture age. No worker settings or service setup changes
are required for refresh. Artifact files remain private outside Git.

For an isolated local preview without restarting the production viewer:

```bash
.venv/bin/python ops/week1_viewer.py --port 8511
# Open http://127.0.0.1:8511/playoffs
```

The currently running production viewer must load the new endpoint code
in a separately authorized activation; this milestone does not restart
its service, merge or deploy. Reloading its old process does not load Python
changes. Do not enable inline draws to make Playoff Picture appear.

## October 2, 2026 artifact/readiness checkpoint

Before refresh, the latest saved artifact was `a1a52dbd963203e674370d0948620641693e860f15dd736968220f0b124ce272`:
complete 2,000 draws at **September 11 15:45:52 CDT**, preserving only two
finals. It was stale against the October 2 view: 272 regular-season games,
17 per team, 49 finals, changed history/model IDs. All six earlier immutable
artifacts were retained; the earlier claim of automatic refresh and visible
probability history was outdated. History/weekly movement remains roadmap work.

The standalone refresh finished **all 2,000 draws in 4.50 seconds wall time**
against the verified **October 2 17:07:27 CDT** view, preserving 49 finals.
Frozen policy hash and model-state identity were verified before running;
no forecast, model, source/shadow record or worker was changed. New artifact:

`ca2ea4aa0d964ce02128775edf87a61c624097d4f57d3fc05fa96b20fba5c3fb`

It is COMPLETE for that evidence. Global probability totals are 14 playoff
qualifiers, eight division winners, two first seeds, two conference champions
and one Super Bowl winner; conference/division totals and integer draw counts
also validate. Buffalo is the saved favorite at **17.7% Super Bowl win**,
with all other teams retained. Maximum MC standard error is **1.118 percentage
points**, describing sampling noise rather than model accuracy. Simulated
0%/100% never implies mathematical elimination/clinching; near-extreme values
are displayed distinctly instead of rounding a non-certain outcome to 100%.

This dated checkpoint is not a live freshness guarantee. After the next normal
source cycle, inspect `/api/playoffs`; refresh standalone if the page says
STALE. Missing net-touchdown data can still block any future run, including
when only lower division rankings need it. No guards were relaxed.

Preserve the separate October 2 **19:10 CDT** verification of actual loaded
T60-correction code after the normal source cycle, and October 4 **07:45 CDT**
IND–WAS receipt check after the **07:20–07:40 CDT** T60 window (kickoff 08:30).
This UI milestone neither forces those cycles nor waits for those windows.

Focused verification:

```bash
.venv/bin/pytest -q -p no:cacheprovider tests/test_season_simulation.py tests/test_season_playoffs.py tests/test_week1_viewer.py
.venv/bin/ruff check ops/season_simulation.py ops/season_playoffs.py ops/week1_viewer.py tests/test_season_playoffs.py
```

Desktop/mobile preview checks cover all-team rendering, AFC/NFC filters,
sorting, sticky team labels, horizontal table scrolling, status/assumptions,
read-only reload, and absence of browser errors. Independent rules review
confirms existing postseason reseeding/home/neutral behavior and fail-closed
tiebreak guards. No historical evaluations or broad test suite are required.
