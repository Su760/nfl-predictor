# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NFL game outcome predictor using an ensemble of ML models (Logistic Regression, Random Forest, Gradient Boosting, XGBoost, SVM, Voting, Stacking). Target variable is `home_win` (binary). Reported accuracy: ~60–65% (home-team-always baseline: ~57%).

## Running the Pipeline

The pipeline has four sequential steps:

```bash
# 1. Basic feature engineering (reads data/games.csv)
python features.py

# 2. Advanced dataset prep from player-level stats (reads data/raw_weekly.csv)
python prepare_dataset_advanced.py

# 3. Train ensemble (reads data/featurized_games_advanced_clean.csv)
python train_ensemble.py

# 4. Weekly predictions (reads model_ensemble.joblib)
python predict_weekly_ensemble.py 2025        # all upcoming games
python predict_weekly_ensemble.py 2025 12     # specific week
```

**Install dependencies:**
```bash
pip install -r requirements.txt
pip install xgboost  # optional but improves ensemble
```

Use `.venv/` (not `venv/`) — the project has two virtual environments but `.venv/` is the active one.

## Architecture

### Data Flow
```
data/games.csv + data/raw_weekly.csv (13 MB, player-level)
        ↓
prepare_dataset_advanced.py  →  data/featurized_games_advanced.csv
features.py                  →  (basic featurization, merged in)
        ↓ [manual clean step removes data leakage]
data/featurized_games_advanced_clean.csv  (161 features, training input)
        ↓
train_ensemble.py  →  model_ensemble.joblib (best), model_real.joblib (stacking),
                       model_ensemble_basic.joblib (soft voting), model_diff.joblib
        ↓
predict_weekly_ensemble.py  →  predictions_YYYY_WW.csv
```

### Key Files
- **`features.py`** — 17-step `build_features()`: ELO ratings (K=20), weather, rest days, surface, rolling EPA/yards/turnover stats (windows 3, 5, season). Uses `shift(1)` throughout to prevent data leakage.
- **`prepare_dataset_advanced.py`** — Aggregates `raw_weekly.csv` into team-level offensive/defensive stats, computes QB-specific features, merges with featurized games.
- **`train_ensemble.py`** — Time-based train/val/test split (no shuffling). Train: all seasons except last 3. Val: 3rd-to-last. Test: last 2 seasons.
- **`predict_weekly_ensemble.py`** — `load_upcoming_games()` is a stub returning `None`; schedule fetching is not yet implemented.
- **`app.py`** — Empty. Streamlit app is planned but not built yet.

### Feature Groups (161 total)
- ELO ratings: `home_elo`, `away_elo`, `elo_diff`
- Team rolling stats (3-game, 5-game, season avg): EPA per play, yards per play, sack rates, turnover differential, third-down rate, red zone efficiency
- QB features: EPA trend, completion %, sack rate (rolling 3 and 5)
- Context: rest days, short week flags, dome/surface, altitude
- Weather: temp, wind, precip flags
- Betting: spread, implied probability, moneyline, total line
- Matchup diffs: `diff_*` for all home vs away pairs

## Important Notes

- Data CSVs are gitignored (`data/*.csv`) but the trained model `.joblib` files are committed.
- All markdown docs (`CURRENT_FEATURES.md`, `ENSEMBLE_MODELS_EXPLAINED.md`, etc.) are currently empty placeholders.
- There are no tests and no linting configuration in this project.
