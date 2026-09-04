FEATURE_SCHEMA_V1: tuple[tuple[str, str], ...] = tuple(
    (name, "float64")
    for name in (
        "home_elo",
        "away_elo",
        "elo_diff",
        "home_colley",
        "away_colley",
        "colley_diff",
        "home_massey",
        "away_massey",
        "massey_diff",
        "home_off_epa",
        "away_off_epa",
        "off_epa_diff",
        "home_def_epa",
        "away_def_epa",
        "def_epa_diff",
        "home_pass_epa",
        "away_pass_epa",
        "pass_epa_diff",
        "home_rush_epa",
        "away_rush_epa",
        "rush_epa_diff",
        "home_qb_epa",
        "away_qb_epa",
        "qb_epa_diff",
        "home_qb_cpoe",
        "away_qb_cpoe",
        "qb_cpoe_diff",
        "home_rest_days",
        "away_rest_days",
        "rest_diff",
        "home_field",
        "neutral_site",
        "division_game",
        "home_short_week",
        "away_short_week",
        "home_games_observed",
        "away_games_observed",
        "home_prior_weight",
        "away_prior_weight",
        "venue_altitude_m",
        "surface_turf",
        "roof_capable",
    )
)

FEATURE_NAMES_V1 = tuple(name for name, _ in FEATURE_SCHEMA_V1)
RATING_FEATURE_NAMES = FEATURE_NAMES_V1[:21]
QB_FEATURE_NAMES = FEATURE_NAMES_V1[21:27]
SCHEDULE_FEATURE_NAMES = FEATURE_NAMES_V1[27:39]
VENUE_FEATURE_NAMES = FEATURE_NAMES_V1[39:42]
