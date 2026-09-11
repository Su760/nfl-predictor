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

Automatic refresh runs after material model, source evidence, schedule or outcome changes. Unchanged evidence reuses its hash-verified immutable snapshot. The season dashboard shows current probabilities, dated history and Super Bowl probability movement; failures remain separate from game forecasting. All snapshots stay in private storage.
