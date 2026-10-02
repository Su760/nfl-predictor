# Continuous season forecasting

## ATL–GB prediction postmortem — 2026-09-26

Conclusion: the archived 72.50% GB forecast reproduces exactly. No probability, team/home,
score-sign, missing-prior-game or fallback-rating defect was found. This is a limitation of
results-only Elo, not evidence that Atlanta's true pregame probability was 55–60% (the user's
estimate). No production configuration, forecast, model artifact or worker was changed.

### Original prediction and evidence

Game `2026_03_ATL_GB` was Atlanta **away**, Green Bay **home**, non-neutral Lambeau Field,
kickoff September 24 at 19:15 CDT (`2026-09-25T00:15Z`). Times below are CDT; all receipt
times equal publication times. Probabilities are raw three-outcome probabilities, not rounded
two-way shares. `R` below means the private `~/nfl-predictor-live-data/season` directory.

| Saved record | Generated / published September CDT | GB home | ATL away | Tie | Revision prefix |
| --- | --- | ---: | ---: | ---: | --- |
| Official production UPDATE | 24 19:02:02.911082 / 19:02:02.912242 | 0.7249982719814994 | 0.2710244552912279 | 0.003977272727272727 | `6f5cfab3` |
| Production T72 | 21 19:09:49.826994 / 19:09:49.828232 | 0.7249982719814994 | 0.2710244552912279 | 0.003977272727272727 | `0bbec7d5` |
| Calibration T72 (research) | 21 19:09:57.001356 / 19:09:57.002565 | 0.6691819230107625 | 0.32684080426196477 | 0.003977272727272727 | `89859933` |
| Calibration latest UPDATE (research) | 24 19:03:02.836993 / 19:03:02.838226 | 0.6691819230107625 | 0.32684080426196477 | 0.003977272727272727 | `6e1ab5f2` |
| QB residual latest UPDATE (research) | 24 19:03:06.991288 / 19:03:06.992522 | 0.7477383363041245 | 0.24828439096860275 | 0.003977272727272727 | `8413c92b` |

Official selection is the latest valid pre-kickoff production revision, **not** a T60 or FINAL
substitute. Both production horizon records are absent/missed. QB T72 is absent; its original
production input snapshot records `NO_OFFICIAL_INJURY_REPORT_FOR_GAME_WEEK`, an unsupported
QB input (`QB_INJURY_EVIDENCE_UNAVAILABLE` in the existing QB guard). Later UPDATE records
do not repair those horizons. This audit does not re-investigate the delivery interruption.

Exact immutable references (each forecast has sibling `.receipt.json` and `.evidence.json`;
`all_records` verifies record/receipt/proof hashes and original durability before deadline):

- Official: `R/forecasts/2026_03_ATL_GB/6f5cfab3fd0a2a90923d3bf4a0ce23e666299d5e3ba46d3e407228fcf6f8f3a0.json`.
  Input snapshot is the embedded `inputs` object. Version `elo-season-v1`, fallback role,
  source code `fc32d122f04f3212d96ee81ce9473a50d5de4c05`, clean when published.
- Official model: `R/models/f205f5e696b2e6f653eaede1cf1b591d1a802e908fcd0e723c66ea2b7225d10f.json`;
  state `a65fea1916019bdaa201846f65bedbcc13748d08d472d00c0a97db4cf3b15dd1`.
- T72: `R/forecasts/2026_03_ATL_GB/0bbec7d5dd0ba31a977f059fd8277caa7ed0d872a3e7462ab32345e07c63e8c1.json`;
  model `f577e6af00d3412601a1609e654fa05732d8b6bbee9ca4d0e9476b433677e391`.
- Calibration under `R/shadow/elo-calibration-fa15c047/forecasts/2026_03_ATL_GB/`:
  `898599335818c83b686808ccef942e6f941abf0224e48dff6c40163e058ab926.json` (T72),
  `6e1ab5f26c1782bae0eb740438cd82c1f5bd49aa370c76917a731cf17aa35bad.json` (UPDATE).
  Model/version `elo-calibration-fa15c047`, artifact
  `fa15c0472a2b6c101b28d063a029789a214bcc9a422c4bf327ba39eb4c5329d7`.
- QB under `R/shadow/qb-residual-cdeab16a/forecasts/2026_03_ATL_GB/`:
  `8413c92b65d2eaad4bd61630a0f26008bedb1e16db8637ccee7f537cf10e67d4.json`.
  Version `qb-residual-cdeab16a`, frozen definition
  `cdeab16a5aa58af520a07bcfd78cd6ebf20a54463a6d2809c89a65f89ad1d03b`, pregame state
  `R/shadow/artifacts/1295f0f01f3990eb7c793b0c33d5d3dc0ede40fabf43d4211ca8a98d8884a989.json`.
Both latest shadows pair explicitly to official revision `6f5cfab3…`.

Atlanta subsequently won 35–14; the result was first observed September 24 at 22:20:12.313836
CDT, after kickoff. It was not in the saved model. Official winner score is 0/1, three-class
sum-of-squared-errors Brier 1.057044 (range 0–2), natural-log loss 1.305546. One loss does not
identify a true probability or justify changing parameters. T72 has the same scores but is
not an independent game; T60/FINAL have no forecast to score.

### Exact Elo replay

The original pregame CSV capture is
`R/captures/d8ed9e656ab38bd6d8cde537441c3e8e148b9c27195e57adc6f8dac0f9247bdc.json`,
captured September 24 at 19:01:57.933123 CDT, with raw CSV
`R/raw/4d60156005cbf4efa255321a3a5f8f936cfb8950eeaac0f9fce5cca276a759a4.csv`.
Its 2,761 completed 2016–2025 regular-season **and playoff** games reproduce history hash
`8716e3229aa98e791534cf4434e066346799cafc8129c004550e7b9f75e5e7ef`.
This is the history actually available before this 2026 forecast, not a claim that historical
result publication timestamps were preserved for each earlier season.

| Rating stage | ATL | GB | GB minus ATL |
| --- | ---: | ---: | ---: |
| End of 2025, replayed | 1460.847152 | 1541.946775 | 81.099623 |
| After offseason: `1505 + (rating−1505) × 2/3` | 1475.564768 | 1529.631183 | 54.066415 |
| After Week 1 | 1460.921813 | 1508.176557 | 47.254745 |
| After Week 2 / saved pregame | 1413.657982 | 1519.589378 | 105.931396 |

Offseason adjustments were ATL **+14.717616**, GB **−12.315592**: shrinkage actually reduced
GB's inherited advantage. All four current-season updates were already available before T72:

| Game, away–home score | Home/away ratings before update | Expected home win share | MOV multiplier | Audited team's change | FINAL first observed CDT |
| --- | --- | ---: | ---: | ---: | --- |
| ATL 13–PIT 20 | PIT 1512.076917 / ATL 1475.564768 | 0.6420679325 | 2.0454936473 | ATL −14.642955 | Sep 13 15:18:32.426541 |
| GB 22–MIN 39 | MIN 1553.275550 / GB 1529.631183 | 0.6248716407 | 2.8596379717 | GB −21.454626 | Sep 13 18:52:16.579092 |
| CAR 34–ATL 3 | ATL 1460.921813 / CAR 1406.747178 | 0.6650819398 | 3.5532336931 | ATL −47.263831 | Sep 20 15:11:15.192124 |
| GB 20–NYJ 17 | NYJ 1396.060991 / GB 1508.176557 | 0.4326078568 | 1.3190722986 | GB +11.412821 | Sep 20 15:39:52.565602 |

Per `src/nfl_predictor/ratings/elo.py`, expected home share is
`q = 1/(1+10^(−(home_rating−away_rating+home_advantage)/400))`.
`delta_home = 20 × ln(max(abs(home_score−away_score),1)+1)
× 2.2/(2.2 + winner_rating_gap×0.001) × (actual_home−q)`;
away change is its negative. Winner gap uses the **winner's** rating minus the loser's,
without home advantage. Ties use actual_home=0.5. Atlanta's upset loss to lower-rated CAR
has winner gap −54.174635 and margin 31: a large, correctly signed −47.263831 update.

The final raw gap is **105.931396**, giving 64.789293% GB without home advantage. Lambeau's
fixed **+65** points makes the gap **170.931396**, yielding conditional non-tie GB share
**0.727893302160547**. The historical regular-season tie layer reserves **0.003977272727272727**;
`p_home=(1−p_tie)q`, `p_away=(1−p_tie)(1−q)`. This exactly gives 72.499827% / 27.102446%.
Home advantage adds about **8.00 percentage points** to the non-tie share; the tie layer removes
0.289503 points from GB. Atlanta lost 61.906787 rating points across Weeks 1–2 versus GB's net
10.041805 loss. The CAR blowout is the largest new contributor, not offseason retention.

Checks: 32 distinct Weeks 1–2 results, explicit FINAL versions captured/observed before the
forecast, kickoff-ordered updates (saved order preserved for simultaneous games), all 32 teams
present (no initial-rating fallback), and correct venue/home/away mappings. ESPN `LAR` is
normalized to canonical `LA`. The replay matches **all 32 saved ratings exactly**, not just
ATL/GB. No target-game result is included. T72 contains 31 finals, versus 32 in the official
model: the later NYG–LA result changes neither ATL nor GB, explaining identical probabilities.
The historical CSV and scoreboard were captured roughly five seconds before production
generation; unchanged probabilities are not evidence of a stale rating snapshot.

### Pregame information: used, unused, missing

- **Used by production:** frozen rating/tie policy, 2016–2025 final results, eligible 2026
  finalized results and home/neutral designation. No injury, QB, weather or market adjustment.
- **Available but unused by production:** nflverse depth chart captured September 24
  19:01:59.233152 CDT, provider update 07:42:08 CDT. Expected starters were Michael Penix Jr.
  (ATL) and Jordan Love (GB); that archived depth feed was not official starter confirmation.
  NFL injuries captured 19:01:58.744927 CDT listed Penix/Tua full participation; GB's
  Aaron Banks, Warren Brinson,
  Jayden Reed and Zach Bako-Bewele out, and Anthony Campbell/Javon Hargrave questionable;
  ATL's Samson Ebukam out and Billy Bowman Jr. questionable. Injury provider publication time
  is unknown; this is capture-time evidence. ESPN weather was 63°F/partly cloudy, with no
  verified provider issue time. None of these facts demonstrates a particular probability shift.
- **Missing from the archived model inputs:** verified official pregame inactives; original
  issue times for injuries/weather; independent starter confirmation. At T72, a game-week
  official injury report was also missing. Later sources are not substituted for those gaps.
- **Valid pregame QB estimate:** the preserved shadow was **GB 74.77%, ATL 24.83%**, not an
  Atlanta pick. Penix's shrunk EPA/attempt 0.071386 minus ATL reference 0.077082 gives
  residual −0.005696; Love's 0.143066 minus GB reference 0.128974 gives +0.014093. Frozen
  coefficient 5.989058 times home-minus-away residual 0.019789 gives **+0.118518 logit** for GB.
  Its state was captured before publication, includes all 32 prior games and no target game;
  the 2026 supplemental stats were last modified September 24 at 09:13:55 CDT. This is a
  heavily shrunk QB-versus-team-reference estimate, not a counterfactual for replacing the QBs
  responsible for recent team results. Historical QB training used retrospective starter IDs
  (existing evidence-grade-C limitation); the live expected-QB evidence here was pregame.
- **Context that supports a hypothesis, not a diagnosis:** the pregame ESPN passing leaders
  for ATL were IDs `2972515` and `5344782`, mapped by the same archived depth chart to Cooper
  Rush (22/39, 229 yards, 1 TD, 4 INT) and Jack Strand (8/15, 59 yards, 1 INT). Love's line was
  37/71, 532 yards, 4 TD, 1 INT. Thus the expected ATL QB differed from these prior-season-to-date
  passing contributors. This does not establish each game's starter or a causal QB-only loss.

**Dated primary-source check (performed after the game):** The [Falcons' September 21
4:29 p.m. ET announcement](https://www.atlantafalcons.com/news/michael-penix-jr-starting-qb-thursday-night-football-vs-packers)
named Penix the Week 3 starter and said Cooper Rush had started both earlier games. Its
[5:41 p.m. ET unofficial depth chart](https://www.atlantafalcons.com/news/atlanta-falcons-week-3-depth-chart-vs-green-bay-packers)
corroborated the change. Both page dates precede the T72 forecast (8:09 p.m. ET September 21).
These pages were not original runtime receipts, so their current content proves what the club
dated as pregame information, not what the worker captured at T72. The preserved UPDATE QB
shadow did use the listed Penix and still raised GB to 74.77%; a second long-run QB residual
would duplicate that already-tested idea.

Green Bay's [September 20, 6:05 p.m. site-dated recap](https://www.packers.com/news/game-recap-5-takeaways-from-packers-overtime-victory-over-jets-week-2-2026)
reported Zach Bako-Bewele's knee injury before T72. The [September 23, 3 p.m. site-dated injury
report](https://www.packers.com/news/packers-rule-out-four-list-two-questionable-vs-falcons-week-3-injury-report-2026)
ruled out Bako-Bewele and guard Aaron Banks before the official UPDATE but after T72. The
UPDATE injury capture recorded both as out; Elo ignored them. No preserved pregame line
performance measure or validated line-to-win-probability effect establishes how much this
should have moved the forecast. Atlanta had allowed 20 and 34 points in Weeks 1–2, and
the [Falcons placed starting CB A.J. Terrell on injured reserve September 22 at 12:58 p.m.
ET](https://www.atlantafalcons.com/news/falcons-place-aj-terrell-jr-on-injured-reserve).
Those facts do not establish a strong-defense adjustment. The saved insights PBP capture
predates Week 2; postgame defensive praise is excluded from this pregame audit.

The [Falcons' September 24 inactives page](https://www.atlantafalcons.com/news/atlanta-falcons-week-3-inactives-green-bay-packers)
and [Packers' September 24 inactives page](https://www.packers.com/news/packers-falcons-week-3-inactives-sept-24-2026)
carry pregame publication labels. More decisively, the original runtime captured the
[NFL single-game inactives article](https://www.nfl.com/news/week-3-thursday-night-inactives-atlanta-falcons-at-green-bay-packers)
before the UPDATE but rejected its description format, as traced below. The saved
normalized input remained `MISSING`; neither later web pages nor a parser repair can
retroactively change the original forecast. No "due for a win" feature follows from
Atlanta's 0–2 record.

**Archived market comparison:** pregame ESPN event `401872948` in
`R/raw/a007eb88113382c6cb3ef57b7c72ed61134521750d70b722792d57949d54988c.json`, authenticated by
`R/captures/6315a91bc4ddf4ebbdfa4dee0fdb950ee6e0daa22172089e2e41d04f8715bec0.json`.
The 2026 component was captured September 24 at **19:01:58.143949 CDT**, merged at
19:01:58.312184; target status was still `pre`. Embedded DraftKings moneylines were GB **−238**,
ATL **+195**, with spread GB −4.5. Raw implied probabilities are 70.4142%/33.8983%; dividing by
their sum gives **67.5031% GB / 32.4969% ATL**, conditional two-way, not a three-outcome quote.
Elo's comparable non-tie share is 72.7893%; calibration is 67.1854%. The JSON calls these fields
`moneyline.*.close`, but they were already saved before kickoff: treat them only as that captured
quote, **not independently verified closing odds**. Provider quote-publication time is absent;
the source's `open` fields are not an independently timestamped opening observation. These
incidental archived quotes were not used by any model. They do not support ATL 55–60%.

### Does overconfidence recur?

Reused, without fitting or searching:
`~/nfl-predictor-live-data/probability/reports/4983130017770c3b1c0c77075586e328d49528158135773074805bbf3fa08aef.json`.
These are saved **T60 regular-season, conditional non-tie home** reliability bins, not winner
accuracy for all favorites. Ties are excluded from these bins. Adjacent bins were merged below
30 observations, explaining the broad 2024 band. Intervals are saved 95% Wilson intervals for
observed frequency, not confidence intervals for this individual game's true probability.

| Production period / probability band | Home wins / games | Mean predicted | Observed | 95% Wilson |
| --- | ---: | ---: | ---: | --- |
| 2018–2023 / 70–80% | 197/268 | 74.61% | 73.51% | 67.92–78.43% |
| 2024 / 70–100% (merged) | 60/76 | 79.02% | 78.95% | 68.50–86.60% |
| 2025 / 70–80% | 30/48 | 74.43% | 62.50% | 48.36–74.78% |

The 2025 gap suggests overconfidence but is imprecise; it is not consistent evidence of a
universal 70–80% failure. The development 60–70% band also overpredicts (362 games, predicted
64.91%, observed 57.73%, interval 52.59–62.72%). No saved subgroup analysis establishes that
QB-return games specifically caused these gaps. Calibrated 60–70% bins were 31/48 in 2024
(mean 65.02%, observed 64.58%, interval 50.44–76.57%) and 30/46 in 2025 (mean 65.10%, observed
65.22%, interval 50.77–77.32%). These bins contain different games and are not paired evidence.
On all 272 identical 2025 games, calibration's saved log-loss delta was −0.005491, with paired
season/week-bootstrap interval **−0.019781 to +0.007184**. No convincing promotion evidence.
All these previously examined seasons remain development evidence; no untouched holdout claim.

### Disposition and one next experiment

**Confirmed defect:** none in the probability path. A separate inactives parsing defect was
found and repaired prospectively below. The known missing T60/FINAL records remain
missing; unchanged. **Limitations:** results-only Elo applies the full blowout update regardless
of who played QB, static home advantage, sparse calibration evidence, and QB shadow reference
is not a recent-starter replacement adjustment. **Unknown:** true matchup probability, causal
injury/QB contributions, and provider issue times where absent.

Next experiment proposal only: a single, preregistered **expected-QB change versus the QB mix
in recent completed games** residual, without changing Elo. Hypothesis: results-only Elo carries
forward QB-specific poor results when the expected QB changes, while the existing long-run
team reference misses that change. First require verifiable pre-cutoff expected-QB and prior
participation evidence; do not substitute target actual starters. Freeze eligibility and one
feature before fitting; estimate on earlier seasons only and evaluate the next season on matched
T60 games against both unchanged Elo and the existing QB residual, reporting missing coverage.
Primary criterion: lower paired log loss with a season/week-block 95% interval below zero;
also report Brier, accuracy, sample sizes and all-eligible-game performance. Exclude this
already-inspected ATL–GB game from selection, label historical results research, and require
separate prospective confirmation before any versioned challenger/promotion. No grid, retention
search, EPA search or live change is authorized by this recommendation.

### Bounded next step: QB-change data feasibility — September 26

The existing QB residual is the first comparator, not a new model to rebuild. On its
identical 2025 T60 subset it covered 203/272 games: Elo log loss 0.671820, Brier 0.458856,
winner accuracy 61.88% (202 decisive); QB residual 0.701457, 0.483894, 60.40%.
The paired QB-minus-Elo log-loss difference was +0.029636 with saved 95% season/week
bootstrap interval [+0.004604,+0.052566]. These previously inspected results argue
against promoting that residual. They do not directly test a *change from recent QB
participants*, which is the one distinct hypothesis supported by the Penix/Rush evidence.

No valid offline comparison for that feature was run. The historical QB training file uses
eventual target starters (`ops/season_qb.py`), while the inspected archives lack original
T60 expected-starter identities with provider issue/update time, capture time, raw hash,
and game-week availability evidence. A later web publication label or eventual starter
cannot replace those receipts. The rebuilt player data contain prior-game QB IDs,
attempts and passing EPA, but not a complete historical as-of join proving those rows
and relevant final results were available at each target T60 cutoff. Both teams' injury
and inactive status, cutoff and kickoff, team/venue identity, Elo reference and eventual
outcome must be joined on the same game; missing fields must reduce coverage visibly.

The smallest **prospective collection change** is a research-only immutable T60 evidence
row per game, derived from sources the worker already fetches: each team's listed QB ID,
alternates and status plus depth/injury/inactives capture and provider times/hashes; the
last completed games' QB IDs, pass attempts and team pass-attempt totals, with final-observed
times and stats capture/source hashes; the paired Elo revision, kickoff and explicit missing
reasons. `ops/season_sources.py` already retains depth/injury captures, `ops/season_qb.py`
retains raw per-game QB stats and receipt components, and `ops/season_shadow.py` retains
prospective game-input snapshots. A derived cutoff join needs no new vendor or live model
change. It must be saved at the actual cutoff, never reconstructed from later source
states. Freeze one participation contrast and eligibility before collecting a test set;
only then fit chronologically and compare on identical covered games against Elo and the
existing QB residual, reporting all-game coverage, proper scores, accuracy and paired
uncertainty. ATL–GB and previously inspected seasons remain development evidence.

### Isolated inactives capture repair — September 27

The immutable NFL inactives landing capture
`R/raw/7df3ebd5cf142e055bce6a4ecfdd10715ce2ca2310ae9e88dee5e4d77225b6d8.html`
linked the ATL–GB article at 19:01:58.862293 CDT. The worker fetched the article at
19:01:59.461974 CDT; its saved raw SHA is
`d71127cf7e480f3d2d3c7271c80e6118069370d565e18eeb005b745014c3162a`.
The article's structured `datePublished` and `dateModified` were both 17:50:21.461 CDT,
before the 19:15 kickoff. Its headline had the full `Atlanta Falcons at Green Bay Packers`
matchup, but its description used `Falcons-Packers`. The original parser required
the full matchup in **both** fields, raised `OFFICIAL_INACTIVES_TEAMS_MISMATCH`, and caused
the saved input to read `NO_VERIFIED_PREGAME_OFFICIAL_INACTIVES_FOR_GAME`. Replacing only the
description in memory allowed the unchanged remainder of the parser to produce both team
sections. This is a source-normalization defect; Elo never used inactives, so it did not
cause GB's 72.50% probability.

The narrow fix still requires the full away-at-home headline and accepts an ordered
`awayNickname-homeNickname` description. A reversed short pair remains rejected. The new
regression failed on the old parser and passed after the fix; 21 focused tests passed with
one unavailable optional real-capture fixture skipped. The full suite passed 1,314 tests
with that same skip. A read-only replay of the exact saved article now returns 11 rows
across ATL and GB with the original publication and capture times. Ruff on the changed
Python files and `git diff --check` pass. Archived forecasts, receipts, model versions,
scoring policy and the running comparison are unchanged; no expired record was republished.

### Prospective QB-change evidence and preregistration — September 28

The new research collector is enabled separately in `configs/season_live.toml`. At a real
T60 run it appends a content-addressed record under private
`R/shadow/qb-change-evidence/`; it runs after the ordinary forecast publications and
does not change their probabilities, selection or scorecards. A frozen private
`R/shadow/qb-change-policy.json` records the first collection time. The collector requires
the existing ±10-minute T60 window, a future scheduled kickoff, the current season, and
the existing receipt validator for each linked T60 forecast. Expired games are never
replayed into this archive. Missing forecasts, source states and unavailable prior games
stay explicit.

Each record retains both teams' **listed rank-1 expected QB** GSIS IDs and alternatives
from the exact depth capture, its capture time, provider update time, source hash and URL.
Provider publication time is `null` when unavailable; an update timestamp is not relabeled
as publication. `confirmed_starter_id` remains `null`. Injury and verified inactives
snapshots include capture and provider times separately, relevant expected-QB rows,
uncertainty and missing reasons. Official inactives article publication time is taken from
its retained player rows when available. Prior current-season completed games retain game
ID/date/kickoff, latest final-observed time and version, every QB with positive pass
attempts, and total QB attempts, bound to the archived stats bytes, capture components and
state artifact. A missing or retracted final cannot enter the participation history.
The row links saved official, calibration and QB T60 revision IDs, model versions and
definitions; unavailable links remain `MISSING`. The stats provider's HTTP Last-Modified
is retained in the component receipts but is not claimed to be a publication time.

**One preregistered comparison, recorded September 28 before collection:**
Use future regular-season games captured after the private collection start for which
the official Elo and existing QB shadow have valid T60 forecasts paired to the same
immutable Elo revision. Both teams need verifiable expected QB IDs, available injury and
inactive evidence without an expected-QB conflict, and three prior current-season games
with final-observed times and complete QB attempt rows before the capture. A **QB change**
means either expected QB differs from that team's highest-attempt passer across its three
most recent completed games; all other eligible games form the unchanged group. The
primary metric is the difference between groups in mean paired three-outcome log loss
(`QB shadow − Elo`); a negative difference means QB information helped especially when
the expected passer changed. Report the two within-group paired differences, Brier,
winner accuracy, ties, a paired week-block uncertainty interval, and the full scheduled
game denominator with delivered, missed, excluded and reason counts. Calibration is a
separate descriptive reference. ATL–GB, all inspected historical seasons and Sunday's
settled games are outside this prospective collection; no coefficient is fitted from
them. Do not fit a new coefficient until at least 100 prospectively eligible changed-QB
games across two seasons support chronological training and a later untouched test.
Any later adjustment uses the expected QB's deviation from the recent team QB mixture,
then fits only residual outcome information after the frozen Elo logit, so the team's
recent results already reflected in Elo are not added again. Any such model needs a new
review and version; this collector does not promote one.

Read-only snapshot at 2026-09-28 11:24 CDT: worker `HEALTHY` (PID 5536), latest saved
view 10:48 CDT, next scheduled run 12:48 CDT. The QB-change evidence archive has zero
rows; the first future opportunity is PHI–CHI, kickoff 19:15 CDT with T60 window
18:05–18:25 CDT. PHI and CHI each have only two prior current-season games, so that
first collection row cannot enter the preregistered three-game comparison. A prior
saved QB-state artifact resolves both teams' game IDs and pass attempts, but the
latest view marks the current QB refresh `BLOCKED` after the free stats download timed
out. The collector will retain that missing reason if it remains blocked at T60.
Collection is configured and focused-tested, but a real T60 row has not yet been
observed. Expected-QB provider publication timestamps and a complete
historical as-of join remain unavailable. The live comparison policy and prior forecast
archives stay frozen.

The existing launchd worker was restarted once to load the flag; new PID 42137 reported
`HEALTHY` at 11:33 CDT with the same 12:48 CDT next run, and no source cycle was forced.
The focused source/QB/shadow/live/scoring suite passed 106 tests with one optional
real-capture fixture skipped; changed-file Ruff and `git diff --check` passed. The
optional scoped mypy attempt failed on the existing untyped `ops` import graph (599
errors across 11 modules, including the new module); no typecheck pass is claimed.

The same saved view has 14 Sunday September 27 games with final results. Official latest
forecasts delivered 14/14 and selected 11 winners; its T72/T60/FINAL slots delivered
13/14, 14/14 and 14/14. Calibration latest delivered 14/14 and selected 10 winners;
its T72/T60/FINAL slots delivered 13/14, 14/14 and 14/14. QB latest delivered 14/14
and selected 11 winners; its T72/T60/FINAL slots delivered 13/14, 13/14 and 13/14.
All delivered Sunday forecasts are settled; the common T72 miss is LA–DEN, and the QB
T60/FINAL miss is SEA–WAS. These outcomes did not enter a fit or policy change.

### Reproduce the numeric audit (read-only)

From the V2 root, run the following. It uses only original private artifacts and existing
helpers; no fetch, training, forecast publication or archive write. It deliberately fails
if the audited probability source/config changes. The saved source bundle verifies all
original source bytes; the current inactives parser is intentionally exempted from the
current-file comparison after the isolated repair above. Team aliases come from the frozen
config, and `week1_live.py` is checked by its recorded code hash.

```python
import csv, json, sys, tomllib
from datetime import datetime, UTC
from pathlib import Path
sys.path.insert(0, "ops")
from week1_live import build_model, canonical, digest, kickoff
from season_live import all_records
from season_scoring import _latest_predictions
from nfl_predictor.ratings.base import CompletedGame
from nfl_predictor.ratings.elo import EloRater
from nfl_predictor.models.tie import to_three_way

r = Path("~/nfl-predictor-live-data/season").expanduser()
def load(p):
    v = json.loads(p.read_bytes())
    assert digest(canonical(v)) == p.stem, p
    return v
records = all_records(r, "2026_03_ATL_GB")
f = next(x for x in records if x["revision_id"] ==
    "6f5cfab3fd0a2a90923d3bf4a0ce23e666299d5e3ba46d3e407228fcf6f8f3a0")
cut, k = map(datetime.fromisoformat, (f["generated_at"], f["kickoff"]))
slots = _latest_predictions({**f, "predictions": records}, k, k)
assert slots["official"] == f and slots["T60"] is None and slots["FINAL"] is None
m = load(r / "models" / (f["model_id"] + ".json"))
s = next(load(p) for p in (r / "source-versions").glob("*.json")
    if json.loads(p.read_text())["identity"]["source_sha256"] == f["source_sha256"])
assert digest(canonical({n: digest(t.encode()) for n, t in s["files"].items()})) == f["source_sha256"]
assert all(Path(n).read_text() == t for n, t in s["files"].items()
    if n != "ops/season_sources.py")
assert digest(Path("ops/week1_live.py").read_bytes()) == m["code_sha256"]
cfg = tomllib.loads(s["files"]["configs/season_live.toml"])
ALIASES = cfg["team_aliases"]
cap = load(r / "captures/d8ed9e656ab38bd6d8cde537441c3e8e148b9c27195e57adc6f8dac0f9247bdc.json")
p = r / "raw" / (cap["raw_sha256"] + ".csv")
assert digest(p.read_bytes()) == cap["raw_sha256"] and datetime.fromisoformat(cap["captured_at"]) < cut
rows = list(csv.DictReader(p.open()))
elo, proof = build_model(rows, cfg)
assert all(proof[x] == m[x] for x in ("history_sha256", "p_tie", "policy_sha256"))
hist = [CompletedGame(x["game_id"], int(x["season"]),
    cfg["team_aliases"].get(x["home_team"], x["home_team"]),
    cfg["team_aliases"].get(x["away_team"], x["away_team"]),
    int(x["home_score"]), int(x["away_score"]), x["location"] == "Neutral", kickoff(x))
    for x in rows if cfg["history_start"] <= int(x["season"]) <= cfg["history_end"]]
end = EloRater(**m["elo_policy"]).snapshot(hist, datetime(2026, 7, 1, tzinfo=UTC)).values
print("2025_END/2026_START", [(t, end[t], elo.rating(t)) for t in ("ATL", "GB")])
schedules = [load(p) for p in (r / "schedules").glob("*.json")]
outcomes = [load(p) for p in (r / "outcomes").glob("*.json")]
accepted = m["current_final_results"]
assert len(accepted) == len({a["game_id"] for a in accepted}) == 32
ordered, games = [], {}
for a in accepted:
    o = max((x for x in outcomes if x["game_id"] == a["game_id"]
        and datetime.fromisoformat(x["observed_at"]) <= cut), key=lambda x: (x["observed_at"], x["version"]))
    g = max((x for x in schedules if x["game_id"] == a["game_id"]
        and datetime.fromisoformat(x["observed_at"]) <= cut), key=lambda x: x["observed_at"])
    games[a["game_id"]] = g
    ordered.append(g["kickoff"])
    assert o["version"] == a["outcome_version"] and o["status"] == "FINAL"
    assert (o["home_score"], o["away_score"]) == (a["home_score"], a["away_score"])
    assert datetime.fromisoformat(o["source_capture_at"]) <= datetime.fromisoformat(o["observed_at"]) <= cut
    assert datetime.fromisoformat(g["kickoff"]) < cut
    assert g["home"] in elo.ratings and g["away"] in elo.ratings
    before = (elo.rating(g["home"]), elo.rating(g["away"]))
    elo.update(CompletedGame(a["game_id"], 2026, g["home"], g["away"],
        o["home_score"], o["away_score"], g["neutral_site"], datetime.fromisoformat(o["observed_at"])))
    if {"ATL", "GB"} & {g["home"], g["away"]}:
        print(a["game_id"], before, "home_delta", elo.rating(g["home"]) - before[0],
            "after", elo.rating(g["home"]), elo.rating(g["away"]), o["observed_at"])
assert ordered == sorted(ordered) and elo.ratings == m["ratings"]
assert digest(canonical({"ratings": elo.ratings, "p_tie": m["p_tie"],
    "policy": m["policy_sha256"], "results": accepted})) == f["model_state_sha256"]
assert (f["home"], f["away"], f["neutral_site"]) == ("GB", "ATL", False)
probs = to_three_way(elo.home_probability("GB", "ATL", False), m["p_tie"])
assert probs == tuple(f[x] for x in ("p_home", "p_away", "p_tie"))
cap = load(r / "captures/6315a91bc4ddf4ebbdfa4dee0fdb950ee6e0daa22172089e2e41d04f8715bec0.json")
p = r / "raw" / (cap["raw_sha256"] + ".json")
assert digest(p.read_bytes()) == cap["raw_sha256"] and datetime.fromisoformat(cap["captured_at"]) < cut
events = json.loads(p.read_bytes())["events"]
for a in accepted:
    g, matches = games[a["game_id"]], []
    for e in events:
        ts = {t["homeAway"]: t for t in e["competitions"][0]["competitors"]}
        teams = tuple(ALIASES.get(ts[h]["team"]["abbreviation"], ts[h]["team"]["abbreviation"])
            for h in ("home", "away"))
        if teams == (g["home"], g["away"]) and datetime.fromisoformat(e["date"]) == datetime.fromisoformat(g["kickoff"]):
            matches.append((e, ts))
    assert len(matches) == 1, a["game_id"]
    e, ts = matches[0]
    assert e["status"]["type"]["completed"]
    assert (int(ts["home"]["score"]), int(ts["away"]["score"])) == (a["home_score"], a["away_score"])
target = next(e for e in events if e["id"] == "401872948")
assert target["status"]["type"]["state"] == "pre"
odds = target["competitions"][0]["odds"][0]["moneyline"]
h, a = int(odds["home"]["close"]["odds"]), int(odds["away"]["close"]["odds"])
ih, ia = -h / (100-h), 100 / (a+100)
print("PREGAME_QUOTE", h, a, "NO_VIG", ih/(ih+ia), ia/(ih+ia))
print("PASS: all 32 saved ratings, result mappings, chronology and exact probabilities", probs)
```

Run `uv run python ops/season_live.py --once` to capture current free sources, reconcile
the schedule, preserve final outcomes/corrections, generate only eligible pregame revisions,
and rebuild scorecards. Run without `--once` for the season daemon. It has no Week 1 stop.
The existing viewer at http://127.0.0.1:8510/ reads `/api/season`. `/api/week1` retains the
original Week 1 snapshot API. No raw files or credentials are served.

## Honest model and data status

Production remains the explicitly labeled uncalibrated Elo fallback. It reuses V2's Elo
and tie components, reconstructs 2016–2025 ratings, and updates them with finalized current
season results observed before generation. Original Week 1 records are read unchanged.
Historical reconstruction does not establish historical point-in-time availability.
The full V2 registry, complete feature snapshots and reviewed artifacts remain a separate
dependency; a complicated challenger is not automatically promoted.

Official NFL injury reports, nflverse timestamped depth charts, ESPN game status and weather
are archived with actual retrieval times. Depth-chart QB1 is an expected starter, not official
confirmation. Missing, ambiguous, stale and unparsed sources must remain visible. Weather
without a provider issue time is provenance-limited. None of these optional signals gets an
invented probability adjustment; changes cause an evidence-bearing revision with unchanged
probability when the fitted model has no supported adjustment.

## Scheduling and immutability

Normal input checks occur every two hours. Near kickoff and while results are expected,
checks occur every five minutes. Targeted T72/T60 runs keep the existing ±10-minute windows;
the final pregame target is T−5m and publication must finish before kickoff. A late origin
is missed, never backdated. Kickoff/venue changes create a schedule version and invalidate
prior-version forecasts for official scoring while retaining their history. TBD, postponed,
missing-source and in-progress games cannot receive new pregame predictions.

A process lock prevents concurrent writers. Every revision stores model state, code base
SHA, dirty-worktree flag, exact source hash, dependency lock hash, input times and change
reasons. Source versions are archived privately, including precommit versions. Publication
requires a durable candidate, receipt and original before-deadline durability evidence.
Unreceipted/late candidates remain invisible. No forecast file is overwritten.

Private state defaults to `~/nfl-predictor-live-data/season` and may be overridden by
`NFL_SEASON_DATA_DIR`; repository paths are rejected. Configuration is in
`configs/season_live.toml`. Captures, outcome revisions, forecast revisions, source versions,
scorecards and scoring policy stay private. Public code never contains those datasets.

## Official scoring policy

The private `scoring-policy.json` is created once with the real effective time before new
forecasts. Games already started at that instant are explicitly retrospective-policy;
we do not claim the policy was declared before their outcomes. The official prediction is
the latest valid production-role forecast before kickoff matching the current schedule
version. Separate T72, T60 and FINAL scorecards select only their own origins. Selection
never depends on the outcome. Fallback and challenger models remain identifiable.

Winner accuracy excludes ties; three-outcome log loss and the sum-of-squared-errors Brier
score include ties. Missing forecasts remain in coverage denominators. Pending games and
missed forecasts are distinct. Results settle only on an explicit final source status with
valid scores; final scores can be corrected, and retractions return games to unresolved.
All source result revisions remain in the history. Pairwise comparisons use the same games
and horizons, never separate convenient samples.

Weekly analysis is computed from the selected pregame records and finalized results. It
includes wins, confident misses, calibration and source gaps. Game outcomes alone do not
establish a defect or an injury effect. Production models never rewrite or promote themselves.

## Hosting status

Local launchd is the current execution environment. This Mac must remain awake and online.
GitHub Actions is enabled for the public code repository, but no private NFL data repository
or deployed application exists. An unattended private runtime needs persistent private
storage, an authenticated dashboard and a verified zero-paid-use compute allowance; these
are not provided by the public code repository. No paid service is enabled as a workaround.

## Verified official inactives

The source adapter discovers NFL single-game news articles from the official inactives page,
requires both teams and matching game/week, and stores publication, modification and capture
times. Reports must be published within the configured 72-hour pregame window; future source
times and postkickoff body modifications are rejected. Newer verified reports take precedence
over link order. January games retain their NFL season identity. NFL-only curl redirects are
enforced. Missing/unpublished articles and unsupported roundup formats remain explicit gaps.
Reports first captured after kickoff cannot enter any earlier forecast. The Elo fallback does
not apply unsupported injury or inactive probability adjustments.

An end-to-end result regression verifies final → corrected final → retracted → restored final
across view-cache loss, preserving the exact pregame forecast and all four outcome versions.


## Readable dashboard and saved game analysis

The existing loopback viewer serves `/`, `/performance`, and `/games/<game_id>`;
`/api/games/<game_id>` returns the same saved analysis as the game page. Week selection
keeps every scheduled game in coverage. Missing pregame forecasts are distinct from
incorrect picks, pending outcomes, and ties. Large team probabilities retain the separate
tie mass; model identity and input limitations remain visible. Detailed execution and
capture records are expandable. The viewer does not publish private files or synthesize
analysis when someone opens a page.

With `analysis_enabled`, a new forecast embeds its explanation before the normal durable
publication and kickoff checks. Verified Elo explanations reproduce the saved ratings,
policy, venue adjustment and tie layer. They do not attribute changes to unused injuries,
QBs or weather. Older forecasts require hash-verified preserved model/policy artifacts;
reconstructed explanations carry their actual reconstruction time and never imply pregame
publication. Missing proof produces an unavailable explanation, not invented reasoning.
Forecast files and prior explanation records remain immutable.

With `postgame_enabled`, finalized games obtain free ESPN summary evidence through the
existing zero-cost source adapter. Event, teams, final score and outcome version must
match. Raw captures and normalized evidence stay private. Optional missing statistics
produce partial evidence; failures are visible. Successful source checks are separate
from the original evidence capture time. Repeated unchanged summaries reuse evidence;
corrected results or statistics retain new immutable versions. No synthetic results enter
this pipeline. Scoring plays support factual descriptions, not causal injury or luck claims.

Reviews include the frozen official prediction, its explanation reference, finalized result,
three-way probability scores and evidence limitations. Wins and losses receive the same
review process. A favorite losing does not alone establish a defect; the saved probability
of the realized outcome is shown. Score/margin expectations are unavailable for this Elo
model. Retracted results return to pending while preserving earlier review history.

Improvement entries preserve game/forecast/review references, source availability,
hypothesis, experiment dependencies, status, decision and rollback. Existing chronological
candidate evaluations appear separately with their model/data/code versions, folds, matched
results and rejection rationale. A game review never automatically changes a model.
Unstarted context experiments remain explicitly unstarted: they require validated original
pregame timestamps and predeclared untouched evaluation periods. Historical reconstructed
backtests never count as live forecasts.

The performance view reports accuracy and coverage separately, Brier/log loss with sample
sizes, calibration, model-version records and T72/T60/FINAL scorecards. Revision comparisons
pair each earlier horizon (and the earliest valid revision) with the official latest valid
pregame forecast on exactly the same finalized games. Deltas are latest minus earlier;
negative is better. Pending games and identical revisions are excluded. Later forecasts
are never selected according to the outcome, and the deltas are observational rather than
causal evidence. Week-level comparisons use their own matched sample.

Betting and playoff simulation work is deferred. `simulation_enabled = false` pauses new
simulation runs while preserving existing code and private snapshots. The season worker,
two-hour input checks and protected pregame snapshot windows remain active.


## Production probability audit (2026-09-12)

Production remains `elo-season-v1`. Its source is `ratings/elo.py`, built by
`ops/week1_live.py:build_model` and updated by `ops/season_live.py:model_for_current_results`.
Every team starts at1505; saved 2016–2025 history includes2,761 regular/postseason games.
At each offseason, ratings become1505 + (rating−1505)×2/3; the transition into2026 is
applied once after the completed historical seasons. Current2026 updates use only the
latest explicitly finalized outcome versions observed before generation. Corrections cause
reconstruction from preserved outcomes, never retrospective rewriting of old forecasts.

For each game, conditional home win probability is
`q = 1 / (1 + 10 ** (−(home_rating − away_rating + venue_points)/400))`.
`venue_points` is65 at home and0 at a neutral site. After a final result, let actual be1
for home win,0 for away win,0.5 for tie. The rating change is
`20 × ln(max(abs(score_margin),1)+1) × 2.2/(winner_rating_difference×0.001+2.2) × (actual−q)`.
The same change is added to the home rating and subtracted from the away rating.
`winner_rating_difference` uses the teams' unadjusted pregame rating difference, with its
sign reversed for an away win (the implementation retains the home difference on ties).
Margin affects later ratings, not a direct score-margin forecast.

The separate tie estimate uses only historical regular-season outcomes:
`p_tie = (ties+0.5)/(regular_games+1)`. The verified capture has10 ties in2,639 games,
yielding0.003977272727272727. Final probabilities are
`p_home=q×(1−p_tie)`, `p_away=(1−q)×(1−p_tie)`, and `p_tie`.
All Elo constants and the tie pseudo-count are fixed policy defaults; they were not fitted
by the production pipeline. Ratings and the tie-frequency estimate are learned from past
results. No production sigmoid/isotonic calibration is applied. The new research evaluation
does not retrospectively make those original constants tuned or validated.

Currently used: historical/current finalized scores, team identity, season transitions,
home/neutral venue state, and the historical tie allowance. Collected/displayed but not
used by production probabilities: QB depth charts, injuries, official inactives, weather,
and source freshness metadata. Those captures can cause an immutable context revision
without changing probabilities; a successful check alone does not imply a new forecast.
Missing source publication time stays missing. Prospective capture time proves what this
system had captured then, not when a publisher first made it available.

The previously inspected2025 period is now a known benchmark. New probability experiments
freeze their configuration before fitting; development ends2023, validation is2024, and
future2026 shadow forecasts saved after the experiment start provide prospective evidence.
Historical eventual starters cannot stand in for expected starters at T72/T60. QB research
using eventual starters is labeled retrospective and cannot qualify for promotion.

Shadow forecasts use separate private publication directories and the same durable kickoff
checks as production. Each retains its actual generation time, input captures, model/code
hashes and paired production reference. Independent scorecards select the latest valid
pregame shadow at each horizon and compare with that frozen reference on the same finalized
games. Missing-QB fallbacks remain explicit gaps rather than scored QB adjustments. Games
before the experiment start are excluded from shadow coverage, not backfilled. No automatic
promotion is enabled; rollback is the unchanged saved production Elo model.


### Measured probability experiments and first shadow activation

Configuration was frozen before fitting: development2018–2023, validation2024,
known benchmark2025, future2026 forecasts generated after the experiment start.
Historical final availability uses kickoff+24hours as an explicit research proxy;
it does not establish original publication time. Development results are fitting/
selection sample results. The known2025 results never select parameters or family.
All regular/postseason results update Elo; target metrics cover regular-season games.

At T60, the exact fixed production Elo formula and its selected sigmoid calibration:

| Period | Games | Production log loss | Calibrated log loss | Production Brier | Calibrated Brier |
|---|---:|---:|---:|---:|---:|
|2024 validation|272|0.619019|0.616397|0.426532|0.423060|
|2025 known benchmark|272|0.665142|0.659651|0.456275|0.451246|

Brier is the sum over all three outcomes. Winner accuracy excludes ties: known2025
production63.47%, calibrated64.21%. The paired2025 log-loss difference is−0.005491,
95% season/week block-bootstrap interval[−0.019781,+0.007184]; Brier difference−0.005029,
interval[−0.016119,+0.005295]. These intervals include zero: improvement is not established.
The27-setting development grid selected home45,K20,offseasonretention2/3; raw tuned
Elo has known2025 logloss0.660651. The production policy remains home65. Calibration
for tuned Elo was not selected on2024; a favorable2025 metric cannot override that decision.

The selected production-Elo conditional mapping is sigmoid(0.882501412638933×logit(q)
−0.15176353784172883), fit on1576 development non-ties. Original tie mass is preserved.
Artifactfa15c047… and report49831300… are private content-addressed files; exact original
sourceSHAa380fe56… and dependency versions are preserved in probability/source-versions.
`ops/season_probability.py evaluate --freeze <private-freeze.json>` is reproducible;
unchanged frozen input/config/source evaluations reuse an integrity-checked index.
Code changes produce distinct experiment identities. No model automatically promotes.

Actual first calibration shadow publication:2026-09-13T04:45:52Z,29upcoming games.
ATL@PIT productionPIT0.6395142531828811,ATL0.35650847408984615,tie0.003977272727272727;
shadowPIT0.5876441519299582,ATL0.40837857534276895,same tie. All573pre-existing production
files remained hash-identical. No shadow forecast was backfilled for already kicked-off games.
The calibration shadow is running locally with separate immutable histories and scorecards;
its finalized paired sample is currently zero. Historical evidence remains GradeC.

The EPA defect repair retains team plays independently of missing QB-only measurements.
All2593corrected rows now have nonzero rushingEPA differences and distinct offense/passEPA.
A separate immutable reconstruction produced265matched known2025 games: correctedEPA
logloss0.703729177,Brier0.491377680,accuracy55.68%; fitted Elo comparator0.661164337,
0.452788393,62.12%. The old defectiveEPA loss was0.694552582. Repairing the inputs did
not improve the model. This fitted-logistic comparator is not the exact production formula,
and its265-game sample must not be directly compared with the272-game experiment above.
Originaldataset/report/coverage remain unchanged. The improvement view marks the verified
bad dataset by content hash and loads corrected experiments separately. Corrected private
outputs are under research/repairs/epa-team-plays-v3/33c70c877…; no promotion is eligible.


QB residual research uses regular-season passing EPA per attempt, accumulated only from
finals available before each target cutoff. History begins2016, including warmup before
scored development games. Player estimates shrink toward the target team's historical
passing EPA reference; shrinkage strength is the median lagged attempt count in development.
The fitted coefficient adjusts the conditional home/away logit; the baseline tie mass stays
fixed. Tied games therefore contribute to probability scores but cannot fit that coefficient.
Team passing EPA is a proxy to reduce double-counting, not an exact QB decomposition of Elo.

Historical actual starter identities are explicitly retrospective GradeC research. They do
not establish what an expected-starter source said at T60. Original historical publication
receipts were not found in the inspected private archives. Current depth-chart publication
and capture times, official injury captures and available inactives are retained prospectively.
Missing identity/history, uncertain availability, stale state or failed sources cannot silently
become a normal QB shadow forecast. Rookies and limited-history QBs fail the frozen support
gates. Uncertain listed starters may have separate conditional scenarios from preserved depth
alternatives; no starter weights are invented, and those scenarios are excluded from scoring.
Expected starter OUT/inactive conflicts remain explicit. Saved feature values, source and
artifact hashes support reconstruction; current inputs never rewrite earlier explanations.

The superseded first QB fit allowed tied outcomes to alter its conditional coefficient. Its
private artifact840fc04…/reportc5f0cf… are retained for audit and must never be activated.
Corrected fitting has a separate freeze/artifact identity. No automatic promotion is permitted.


EPA failure audit: the current challenger selects exactly offense, defense, pass and rush
EPA differences (season_rebuild.py model indices11/14/17/20 in FEATURE_SCHEMA_V1).
It replaces the rating predictor; it is not an incremental Elo-plus-EPA model. It omits
explicit Elo difference and home/neutral columns. That omission is a future hypothesis,
not proof of why it lost. Opponent adjustment already exists: ridge fits use alpha10,
with policy-controlled prior blending and four-game EWMA support weights. The logistic
model standardizes estimator inputs and uses C1/max_iter2000; these policy defaults were
not optimized by this repair. Nonfinite selected features fail closed. Schema ordering
and target exclusion are explicit; historical prior games must precede the target date.
Identity calibration was selected for both corrected candidates by chronological folds.
Thus neither absent opponent adjustment nor an assumed calibration gain explains away
the observed loss. The data defect is established; remaining model weaknesses need a
new frozen development experiment and prospective evidence, not tuning on known2025.
The fitted Elo comparator also uses feature-policy Elo (initial1500/home0), unlike the
production policy (1505/home65), reinforcing why the separate exact-production replay is
required for the new probability comparisons.


Corrected QB experiment and matched comparison (same T60 games):

| Model | 2024 log loss (249 games) | Known2025 log loss (203 games) | Known2025 Brier | Accuracy (202 decisive games) |
|---|---:|---:|---:|---:|
| Exact production Elo | .609327 | .671820 | .458856 | 61.88% |
| Calibrated production Elo | .607556 | .667890 | .455012 | 63.86% |
| Tuned Elo | .603716 | .667252 | .454495 | 63.37% |
| Corrected QB residual | .644663 | .701457 | .483894 | 60.40% |

QB coverage is249/272 in2024 and203/272 in2025; exclusions remain recorded and are not
losses hidden from the comparison. The QB known2025 log-loss delta is+.029636,95% paired
season/week bootstrap interval[+.004604,+.052566]; Brier delta+.025038,[+.003165,+.045696].
Validation log-loss delta+.035337,[+.006997,+.064278]. These observed samples favor Elo.
Known2025 calibration intercept/slope: production−.158854/.921890, calibrated−.000336/1.044471,
tuned−.054618/.940572, QB−.051992/.607665. Bin counts and all paired intervals are saved in
matched comparison811801a6064ba095c3f7066f5ed0d047b6c411cdf511db319b291d067aff0fc2.
Calibration and tuned Elo intervals still include zero. These are inspected research periods,
not untouched tests. Future forecasts after the frozen experiment start remain prospective.

Corrected QB artifactedce0619… (fileSHAcdeab16a…) and reportff8c02be… use coefficient
5.989057953711544, fit on1393decisive development games; five development ties are excluded
from coefficient fitting, but retained in proper scoring. Total1850prepared rows include
validation and known-benchmark rows; they are not1850training games. The offline driver
`uv run python ops/season_qb_experiment.py --help` documents seven explicit private input
arguments. It archives exact source/config/raw/dependencies before fitting and validates
an immutable run index on reuse. Actual repeated invocation added/changed no files; all1850
probabilities exactly matched the corrected artifact. Private reproduction output is under
probability/qb/cli-reproducibility-v2; the active definition remains unchanged.

QB is SHADOW-RUNNING, not eligible for promotion. First10normal shadow forecasts published
2026-09-13T06:08:21Z; second real cycle retained20revisions on10games. The calibration shadow
has116revisions on29games. Both have zero finalized prospective outcomes at verification.
ARI@LAC production: home.7558747474462543/away.24014797982647298/tie.003977272727272727.
QB: home.7440243280415569/away.25199839923117023/same tie. Saved residuals LAC−.006081478698006432,
ARI+.004599561789204294 times coefficient yield logit change−.06396937048384442; applying
that to the saved baseline conditional logit reproduces the shadow exactly. Saved evidence
identifies Justin Herbert and Jacoby Brissett with source/capture times and all feature values.
ATL@PIT remains unconditionalQBfallback due to OUT conflict. A separate CooperRush/Rodgers
conditional estimate givesPIT.7829977687595693,ATL.21302495851315792,tie.003977272727272727.
It is unweighted/unscored; JackStrand is unavailable due to insufficient history. No claim
that either scenario is the expected starter distribution is made.

Live verification: http://127.0.0.1:8510/ and /games/2026_01_ARI_LAC plus /performance,
desktop1440/mobile390, realclick routes and conditional labels/probabilities; no overflow or
JavaScript errors. Screenshots are private/tmp/nfl-qb-shadow-{390,1440}.png and
/tmp/nfl-qb-conditional-{390,1440}.png. Source check06:27:09Z Sep13; next08:27:09Z
(03:27CDT); production lastsave05:47:53Z (00:47CDT), zerochanged probabilities/newrevisions
on that check. Worker reloaded; local Mac awake/online remains required. No cloud deployment
is claimed. Full V2 registry/reviewed champion and promotion-quality historical expected-QB
receipts remain absent. Missing2026finalized QB statistics block affected next-week team states;
missing week2injury reports and unsupported rookies block their QB forecasts, not production.


## Final live operation check — September13,11:48CDT
Source milestone f823ba78462be4fb97b9742ea34f41d70667ef32 is pushed to V2; remote matches,
worktree clean at verification. Actual11:46:05CDT source/forecast cycle succeeded, next11:51:05;
348calibration revisions across29games and100QB revisions across10games, prospective settledN0.
Week1official record0/1correct,14pending,15/16coverage remains unchanged. Paidusage0.

Operational limitation observed, not hidden: eight noon games missed T60. Worker log scheduled
16:00UTC but next completed at16:12:15UTC, outside the10minute horizon window. macOS power
logs show sleep across that interval (11:01:34–11:11:10CDT, plus earlier maintenance sleep).
No caffeinate process/assertion existed. Existing pregame forecasts remain valid; no later
revision was relabeled T60. FINAL snapshots remain scheduled before noon kickoff.

Local mitigation installed in the existing private LaunchAgent:
~/Library/LaunchAgents/com.su760.nfl-season-worker.plist ProgramArguments now wrap the
existing Python command with /usr/bin/caffeinate -i. Original plist is preserved privately
under season/operation-backups/100437337…plist. LaunchAgent reloaded; initial bootstrap
returned input/output error during unload, retry succeeded after service removal. launchctl
reports running; pmset confirms the worker-specific PreventUserIdleSystemSleep assertion.
WorkerPID54635/guardPID54636, healthy11:48:40CDT. This prevents idle sleep only; closed-lid,
shutdown, network loss and unavailable private always-on hosting remain real dependencies.
It does not recover missed windows or weaken kickoff deadlines. Rollback is the preserved
plist followed by bootout/bootstrap of the same label; no global power settings were changed.

## Frozen prospective model comparison — September 23, 2026

This milestone reuses the existing shadow scheduler, immutable per-game forecast directories,
durable source receipts, paired production references and scorecards in `ops/season_shadow.py`.
There is no second forecast pipeline or result ledger. Production remains
`elo-season-v1`; its policy, probabilities and archived forecasts are unchanged.

The comparison contract is configured in `configs/season_live.toml` and written once as the
private `shadow/comparison-policy.json`. Its collection start is
`2026-09-24T00:11:30.920767Z`. Eligibility is limited to regular-season games with kickoff
after that instant. A future game's already-saved, valid, immutable forecast remains eligible;
games whose outcomes were already known at the freeze are excluded. The primary horizon is
T60 and the separately reported secondary horizon is T72, both using the existing ±10-minute
origin window. At each horizon, scoring selects the latest valid saved challenger forecast and
the production Elo reference embedded with that exact publication. No result can choose the
revision or comparison sample.

The latest observed official FINAL is the outcome; unresolved retractions remain pending.
Ties are excluded from winner accuracy and included in multinomial natural-log loss and the
three-outcome Brier score (sum of three squared outcome errors, range 0–2). Accuracy receives
a 95% Wilson interval and samples below 30 decisive games are labeled small. Operational
coverage reports all eligible games as forecasted, awaiting result, scheduled, due or missed,
with an explicit reason for each missed forecast. Performance is only the identical-game pair;
missing predictions remain visible in coverage and are not converted to losses or silently
dropped. Promotion is manual-review-only and is separate from display; weekly outcomes do not
retune or automatically promote a challenger.

Frozen challengers:

- `elo-calibration-fa15c047` uses the existing hash-bound calibration artifact and only the
  already-supported production Elo probability input. It is the currently operational
  challenger.
- `qb-residual-cdeab16a` uses the existing hash-bound QB artifact, runtime config, historical
  source and timestamped depth/injury/inactive receipts. It remains selected because the
  runtime path is implemented, but forecasts fail closed whenever current QB evidence or the
  nflverse state supplement is unavailable.
- The repaired EPA experiments remain research-only and are not selected. No supported live,
  timestamped EPA feature feed and runtime artifact exists for this scheduler; historical
  reconstructed features are not substituted for prospective inputs.

The `/performance` dashboard now shows a compact **Live model comparison** section separate
from official scoring and reconstructed research. It reports operational coverage, correct /
non-ties, Wilson uncertainty, challenger/Elo Brier and log loss on the same games, blocker
reasons and the frozen conventions. Game pages retain the detailed research-only shadow view.

Activation verification and the first live obligation audit are recorded below after the
committed worker reload. Existing archives must remain byte-identical; only new, valid future
publications may be appended. Missed windows are recorded and never backfilled.

### Activation and current obligations — September 23, 2026, 19:24 CDT

The existing LaunchAgent was reloaded at commit `da26275`, then the documented `--once`
refresh completed at `2026-09-24T00:24:36.517426Z`. The worker is HEALTHY, required sources
are FRESH, the next source check is `2026-09-24T02:24:36.517426Z`, and paid services remain
disabled. The immutable comparison policy was created with the exact contract above. Hash
verification found all 48,526 pre-existing production/shadow archive files unchanged; the
cycle appended 119 forecast receipts/evidence/policy records and one new valid production
coverage revision. It did not rewrite a completed-game forecast.

Both configured challengers are currently operational. Calibration has 8,860 immutable
revisions over 47 games; QB has 2,414 revisions over 42 games. The frozen comparison currently
has 216 eligible games with known future kickoffs; another 24 schedule entries in Weeks 16–18
have no kickoff timestamp yet and cannot have a cutoff obligation. No eligible outcome has
settled, so all accuracy, Brier and log-loss samples are N=0 and explicitly small.

At T60, both challengers are 0/216 forecasted, 216 scheduled and 0 missed because no eligible
T60 window has occurred. At T72, calibration is 1/216 forecasted with 215 scheduled and 0
missed. QB is 0/216 with 215 scheduled and one missed: ATL@GB has no valid saved QB forecast
with origin T72. Later UPDATE forecasts exist but are not relabeled or backfilled. The exact
per-game blocker at that expired window was not preserved in the latest view; period reports
show QB-state refresh failures, including network timeouts, but do not prove which failure
caused this specific miss. The dashboard therefore reports the narrower verified reason
`NO_SAVED_VALID_T72_SHADOW_FORECAST_BEFORE_CUTOFF`.

The next forecast obligations are nine Week 3 T72 games at the 17:00Z target on September 24
(allowed save window 16:50–17:10Z), followed by two games at the 20:05Z target. ATL@GB T60 is
targeted for 23:15Z (23:05–23:25Z). The daemon and worker-specific `caffeinate -i` assertion
are running. Collection is unattended while this Mac remains awake, logged in and online;
closed-lid sleep, shutdown and network/source outages remain external failure modes.

## Prospective delivery and displayed probabilities — September 23, 2026

Fresh audit time: `2026-09-24T02:18:42Z` (`21:18 CDT`). The worker heartbeat was HEALTHY at
`02:18:16Z`; the saved view's required sources were FRESH from `00:24:36Z`, with its next
deadline-aware run scheduled for `02:24:36Z`. No comparison result had settled.
The scheduled cycle then completed normally at `02:25:28.538750Z`: sources remained FRESH,
production reported zero changed probabilities, zero new forecast coverage and zero new saved
revisions, while `last_forecast_generation` correctly remained `00:24:36.512064Z`. The next
source check is `04:25:28.538750Z`; the next forecast cutoff remains the Week 3 T72 window at
`16:50–17:10Z`. Delivery counts below were unchanged by the refresh.

All frozen-comparison obligations with a known kickoff, classified at that audit time:

| Challenger | Horizon | Delivered closed windows | Missed closed windows | Blocked now | Not yet due | Due now |
|---|---:|---:|---:|---:|---:|---:|
| Elo calibration | T60 primary | 0 | 0 | 0 | 216 | 0 |
| Elo calibration | T72 secondary | 1 | 0 | 0 | 215 | 0 |
| QB residual | T60 primary | 0 | 0 | 0 | 216 | 0 |
| QB residual | T72 secondary | 0 | 1 | 0 | 215 | 0 |

The delivered calibration record for ATL@GB is immutable origin T72, generated
`2026-09-22T00:09:57.001356Z` and durably received at `00:09:57.002196Z`, inside its
`00:05–00:25Z` window. The QB T72 miss is not a scheduler miss: saved input snapshots at
`00:09:51Z` and `00:15:31Z` show expected QBs available, and the contemporaneous QB-state
artifact contains every required prior ATL/GB result. Both snapshots record injuries as
`MISSING / NO_OFFICIAL_INJURY_REPORT_FOR_GAME_WEEK`. The frozen QB guard requires an AVAILABLE
injury report and therefore returned `QB_INJURY_EVIDENCE_UNAVAILABLE`; fallback estimates are
not published as shadow forecasts. Later QB UPDATE records remain UPDATE and do not repair or
replace the missed T72 obligation.

### Which probability the UI displays

- The main card reads `scorecards.games[].prediction_id`, then displays that exact revision's
  `p_home`, `p_away` and `p_tie` from `game.predictions`. Before kickoff it is labeled **Latest
  pregame estimate · production Elo**. After kickoff the same selection is labeled **Official
  scored forecast · latest valid pregame production Elo**.
- The game-detail API independently resolves `official_prediction` from the same scorecard
  `prediction_id`; the detail card receives that exact record. Page visits never reconstruct a
  probability.
- The official scorecard chooses the latest valid production-role revision by durable
  publication time, restricted to the current schedule version and strictly before kickoff.
  Outcome contents cannot select the revision.
- T72, T60 and FINAL scorecards instead choose the latest valid revision whose `origin` exactly
  matches that horizon. A later UPDATE, FINAL or official revision does not rewrite an earlier
  horizon.

The card now shows **Forecast published** separately from **Latest source check** and states that
a newer check does not imply a changed probability. Research challenger tables are explicitly
labeled **Research shadow probabilities · not official**. Live comparison coverage uses separate
columns for future scheduled, due-now and missed/overdue obligations.

### Recalculation, publication and input use

The daemon polls its clock every 60 seconds. Normal source refresh is approximately every two
hours; within two hours of a game it checks every five minutes. Independently, `next_check`
inserts every future T72, T60 and FINAL target and selects the earliest deadline or cadence event.
T72/T60 accept publications from target−10 minutes through target+10 minutes. FINAL begins at
kickoff−5 minutes and closes at kickoff. The scheduler regression verifies a T72 target preempts
the two-hour source interval, while the five-minute near-game cadence reaches T60 even earlier.
External fetch latency or source failure can still consume a window; timestamp enforcement stays
fail-closed. No scheduler defect was reproduced, so scheduler code was not changed.

Each scheduled run refetches sources, rebuilds production Elo state from only available finalized
results, and considers games within the eight-day slate. It publishes ON_DEMAND for first
coverage, the named horizon while that window is due, or UPDATE when the saved input/model-state
fingerprint changes. A refresh may therefore save newer evidence while probabilities remain
identical.

- Production Elo probabilities use past finalized scores, team identity, offseason regression,
  home/neutral venue, fixed Elo policy and the historical tie rate. Expected QB, injuries,
  inactives and weather are collected but do not alter production probabilities.
- The calibration shadow maps production Elo's conditional home probability through its frozen
  sigmoid and preserves production tie mass. It does not use QB, injury or weather values.
- The QB shadow starts from the paired production probability, then uses timestamped expected-QB
  identity, injury/inactive availability guards, and frozen pre-cutoff player/team passing-EPA
  state. Weather does not affect it. Missing required evidence yields an unscored fallback.

At each named horizon the saved origin record becomes immutable when published and no later
origin can replace it. At kickoff all publication closes; official scoring remains fixed to the
latest valid pre-kickoff production revision, while pending outcomes can later settle or retract
without changing that forecast.

The LaunchAgent remains wrapped by `caffeinate -i`, and macOS reports an active
`PreventUserIdleSystemSleep` assertion. This supports unattended collection only while the Mac is
awake, logged in and online. It does not support closed-lid sleep, shutdown or offline operation.

## Week 3 closeout and QB source limitation — September 29, 2026

Bounded recovery from existing immutable reports, with no evaluation rerun:
`R/weekly-reports/a04c924f28693a8f2080876ac35803fddc0b9edde7e5a0a4d07fd30bf61713a4.json`
and `R/analysis/shadow-reports/9297ee5021d9327a4732e15b4a031850e5e8eca2d9a5030c1abb952bf79c4f11.json`.
Here `R` is the private season data root. Both report content hashes match filenames.
PHI–CHI is FINAL, PHI 7–CHI 27; the saved outcome was first observed
`2026-09-29T04:17:24.159644Z` and agrees with the NFL game center checked September 29.

| Selection / identical-game sample | Correct/total | Accuracy | Natural-log loss | Three-outcome Brier |
| --- | ---: | ---: | ---: | ---: |
| Official production, latest valid pregame | 12/16 | 75.00% | .632924 | .438892 |
| T60 production, calibration's 15 games | 12/15 | 80.00% | .588083 | .397682 |
| T60 calibration, same 15 games | 10/15 | 66.67% | .622981 | .427824 |
| T60 production, QB's 13 games | 11/13 | 84.62% | .520387 | .337420 |
| T60 QB, same 13 games | 11/13 | 84.62% | .459791 | .287113 |

Official production coverage is 16/16, zero missing. T60 production/calibration each
cover 15/16 and miss ATL–GB. QB covers 13/16 and misses ATL–GB, SEA–WAS and PHI–CHI.
Production delivered SEA–WAS and PHI–CHI; they are excluded only from the paired QB
sample. There are no ties. Brier is the sum of three squared errors, range 0–2.
These are small prospective samples; no tuning, model promotion or policy change.

### Archived expected-QB conflict and source semantics

The first original collector record is
`R/shadow/qb-change-evidence/d4078180aa01948d58ef95d1f8256bb06a78c9a9fc8e8d29114d02955e8388c9.json`
(`2026-09-28T23:15:55.671411Z`); the second is
`2dd0d57b5797996bc0bf4e90907ef3cdf7389aed8710643ede6f012defe6c6ff.json`
(`23:21:28.727783Z`). Both remain unchanged and represent one game, not two samples.

- Depth raw `bd5542ae46887d38c06c06c9a0483787c6a1a0326a0c375be6e9ec3542ab169c.parquet`,
  from `https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_2026.parquet`:
  `dt=2026-09-28T15:17:44Z`, captured `23:15:33.364506Z`. Rank 1 is Caleb Williams,
  GSIS `00-0039918`, ESPN `4431611`; rank 2 Tyson Bagent, `00-0038416` / `4434153`;
  rank 3 Case Keenum, `00-0028986` / `15168`.
- NFL injuries raw `ecaee16de21b909812e710480bb94fefffcc1723fe43d498c6524c12d435ed30.html`,
  source `https://www.nfl.com/injuries/`, captured `23:15:32.647789Z`, with no verified
  publication time: Williams OUT (hamstring); Bagent questionable (concussion).
- NFL inactives raw `4cfb2f17483668f9e1967534010e308a8d23a36b1734d87196b77688130d0ddd.html`,
  source `https://www.nfl.com/news/week-3-monday-night-inactives-philadelphia-eagles-at-chicago-bears`,
  published `22:47:49.097000Z`, modified `22:48:47.412000Z`, captured `23:15:33.603290Z`:
  Williams inactive. The retained NewsArticle body lists inactives and names no replacement starter.

All times above are September 28 UTC. The raw hashes were checked against the original
collector links. `ops/season_sources.py:579` selects the latest `dt`, QB, rank 1 and
copies the provider IDs; there is no demonstrated identity or parsing defect.
The [provider dictionary](https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html)
defines `dt` as record-load time and `pos_rank` as rank within a depth-chart position slot.
The existing `source_updated_at` field holds that load timestamp; it must not be interpreted
as a starter announcement or its publication time. This snapshot passes the existing
36-hour age check. The conflict is an inadequate game-starter signal, not a failed fetch
or an age-gate violation.

`ops/season_qb.py:682` rejects the OUT expected player; the inactive guard at line 700
remains an independent protection. Replaying the original inputs and archived pregame QB
state `fb5e1e033120ba9b67951222348d09a695c3681d4183847cfe3924c8d74c808a`
returns `FALLBACK / EXPECTED_QB_INJURY_STATUS_OUT`, preserving baseline probabilities.
Bagent/Keenum estimates remain explicitly hypothetical, unweighted and excluded from
forecast scorecards. Injury clearance or omission from inactives cannot confirm who starts.
The collector already records `expected_starter_source=depth_chart_rank_1` and
`confirmed_starter_id=null` (`ops/season_qb_evidence.py:291`).

No existing captured, supported source establishes the replacement. A future selection
path needs a reliable game/team/player-specific pregame confirmation with an archived URL,
content hash, capture time, publication time when available, and unambiguous player-ID join.
It must reject stale/conflicting assertions and still enforce OUT/inactive guards. No new
scraper, arbitrary backup promotion or postgame reconstruction is supported here. Explicit
confirmation ingestion remains unimplemented. No research input policy changed; its frozen
identity and existing live comparison are preserved. Any future selection-policy change
requires its own prospective version before collection.

### Focused verification and deployment status

Added twelve focused test cases across `tests/test_season_sources.py`,
`tests/test_season_qb.py` and `tests/test_season_qb_evidence.py`: archived IDs/ranks,
ambiguous rank 1, fresh/stale depth, valid but unconfirmed inference, future captures,
missing injury evidence, OUT/inactive rejection, retained source provenance, and absence
of automatic replacement/confirmation. Four existing guard/scenario tests were included.
The valid-availability example is explicitly a synthetic unit fixture, never a revision
of the real PHI–CHI evidence. No explicit-confirmed-starter acceptance test is claimed,
because there is no supported confirmation ingestion path.

Focused command: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider tests/test_season_sources.py tests/test_season_qb.py tests/test_season_qb_evidence.py -k 'phi_chi or depth_inference_and_inactive_omission or stale_depth_capture_keeps or uncertain_starter_scenarios_have_no_weights or real_shape_injury_out or expected_qb_listed_inactive or missing_injury_evidence_fails_closed'`.
Result: **16 passed, 52 deliberately deselected**, zero failed/skipped (1.20 seconds).
Changed-test Ruff and `git diff --check` passed. No full suite or completed evaluation rerun.
A before/after SHA-256 check of 1,283 PHI–CHI forecast/receipt/proof, collector, report,
frozen-policy and relevant source/config files found zero changes.

At `2026-09-29T21:46:50.940463Z`, worker PID 3006 is healthy; its normal source run is
scheduled for `22:40:55.877177Z`. The existing LaunchAgent's executable and working
directory point to this V2 worktree; `season_live.run_once` uses `fetch_sources`,
then `run_shadow`, then `archive_t60`. No runtime source/config changed and no worker
restart, deployment, commit or push occurred. These tests establish the existing guard
behavior; they do not establish a deployed replacement-starter fix.

Next collection checkpoint: **PIT at CLE, October 1, T60 18:05–18:25 CDT**
(target 18:15 CDT; kickoff 19:15 CDT / October 2 00:15Z). Read-only execution of the
existing collector history helpers finds three final-observed games for each team,
complete QB attempts and no missing-history reasons: CLE totals 22/30/31; PIT 40/40/34.
The normal worker will use the unchanged guarded depth-inference path. At that window,
inspect the newly appended collector record: expect three prior games/team, original
source timestamps/IDs, `confirmed_starter_id=null` absent a supported confirmation path,
and valid paired T60 forecast references or explicit missing/availability-conflict reasons.

## Prospective T60 baseline pairing repair — October 2, 2026

### Recovered PIT–CLE collection and separate settlement

Read original hash/receipt/durability-verified records for `2026_04_PIT_CLE`.
Four collector snapshots were captured October 1 at 18:07:24.119430,
18:13:08.492201, 18:15:46.382468 and 18:21:29.089310 CDT, all inside
the unchanged 18:05–18:25 T60 window. These are one game, not four samples.
Both teams have three observed-final prior games and complete attempts
(PIT 40/40/34; CLE 22/30/31), with no expected-QB availability conflict.
Starter IDs remain rank-1 inferences: Rodgers `00-0023459`, Watson
`00-0033537`; confirmed starter IDs remain null. Source publication times
remain unknown where the provider did not supply them.

| Saved origin                                  | Revision ID                                                        | Paired baseline ID                                                 | Published October 1 CDT |
| --------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------ | ----------------------- |
| Official T60                                  | `5180a1462d733a1539f77ef0bb192a3b874de264fc47aba0618056e06066f1ca` | none                                                               | 18:06:59.928917         |
| QB T60                                        | `b7a34207f77f0c81fc7306cdec7e1740bfa4d91697b52370379dcab50ecc1a83` | `0d46d5b5b6e16de92124c570f73bf8c5a42662f738320000c76d3f6bd394dd78` | 18:07:24.094795         |
| Calibration T60 (separate research reference) | `a6d68827c0736f3d43fd3085a8329d681be37cc8293502a48dc752e3e1041388` | `0d46d5b5b6e16de92124c570f73bf8c5a42662f738320000c76d3f6bd394dd78` | 18:07:18.887252         |

Paired baseline `0d46d5b5…` is production UPDATE, published 18:06:59.930157,
1.240 milliseconds after T60. Its probabilities equal the production T60
probabilities, but its revision identity differs. The PIT–CLE collector row
remains excluded from the frozen same-official-T60-revision comparison.
Existing live shadow scorecards may settle the original paired UPDATE; their
historical forecasts, scorecards and eligibility decisions are not rewritten.

Separately, the original outcome journal records FINAL, PIT 24–CLE 27,
version 1, first observed October 1 22:45:07.573593 CDT, from an ESPN
capture at 22:45:05.907015. Forecast delivery and final-result settlement
are independent; settlement does not cure the pairing mismatch.

### Confirmed cause and prospective correction

At original commit `db2a8c7`, `season_live.py:525` selects the latest current
record, appends a due T60 plus material UPDATE in the same cycle at lines
526–531, and selects the latest generated record again at lines 589–598.
`season_shadow.py:517` uses that latest `game["prediction"]`, calculates a
candidate once at lines 533–535, and reuses it for every publication origin.
This exactly explains the original UPDATE pairing in both T60 shadows.

The prospective fix changes only shadow T60 publication. It reads the saved
official T60 through the existing receipt/hash/durability validator, requires
matching game/team/kickoff/schedule identity, production role, valid probabilities
and pre-cutoff publication inside the T60 window, then invokes the actual
QB/calibration calculator against that selected baseline. Probability, explanation,
fingerprint and paired ID all come from that calculation. No baseline ID is
substituted onto a candidate calculated from UPDATE. T72, FINAL, ON_DEMAND,
UPDATE and conditional-scenario behavior keep the existing latest baseline.

No valid saved official T60 produces origin exclusion
`NO_VALID_SAVED_OFFICIAL_T60_BASELINE`. Candidate availability failures retain
their explicit reason. Actual generation, publication and receipt durability
still must beat the existing T60 deadline. A later cycle may retry only while
the original window remains open. An existing T60 shadow is never replaced,
and a closed window is never backfilled.

This repairs execution of the existing `prospective-qb-change-v1` pairing
requirement. It does not change the model, artifact/config hashes, comparison
contract, eligibility rule or experiment start time; no new experiment version
is necessary. New shadow records retain the corrected source-code hashes.

### Focused validation and next prospective verification

The same-cycle production integration regression fails against committed old
shadow code (T60 pairs to UPDATE) and passes after correction. A separate
regression gives T60 and UPDATE different probabilities and checks calculated
values plus explanation provenance, so relabeling alone cannot pass. Actual
QB and calibration calculators are exercised against both baselines.

Focused command:
`PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider tests/test_season_shadow.py tests/test_season_live.py tests/test_season_qb_evidence.py tests/test_season_qb.py`.
Includes missing/unreceipted/future/old-schedule/out-of-window/invalid-probability
baselines; availability guards; calculation and receipt deadline crossings;
unchanged other origins; original mismatched-T60 preservation; and collector
source/cutoff checks. Result: **98 passed**, zero failures/skips. Changed-file Ruff and diff checks
passed. Comparable strict mypy remains failing on the inherited ops graph:
HEAD 571 diagnostics, current 569, zero new normalized diagnostics; no clean
typecheck claim. A SHA-256 manifest check found **87,311 existing immutable
files unchanged**, including forecast/receipt/proof, collector, model and policy
objects. No broad suite or historical evaluation is run.

Next opportunity from the current schedule: **IND at WAS, October 4**,
kickoff **08:30 CDT**, T60 **07:20–07:40 CDT** (target **07:30**).
At **07:45 CDT**, read worker health and original newly appended collector
records. Verify exact equality of QB paired-baseline revision and saved official
T60 revision, receipt-valid publication inside the window, three prior games/team,
original source timestamps/IDs and expected-QB availability. Report calibration
separately. Verify the shadow source-version contains the committed corrected
`season_shadow.py` hash; report explicit source/availability or delivery exclusions.
Do not wait for this window or alter PIT–CLE records.
