"""Explicit NFL and nflverse team-abbreviation canonicalization."""

CANONICAL_TEAM_ALIASES: dict[str, str] = {
    "ARI": "ARI",
    "ARZ": "ARI",
    "ATL": "ATL",
    "BAL": "BAL",
    "BUF": "BUF",
    "CAR": "CAR",
    "CHI": "CHI",
    "CIN": "CIN",
    "CLE": "CLE",
    "DAL": "DAL",
    "DEN": "DEN",
    "DET": "DET",
    "GB": "GB",
    "GNB": "GB",
    "HOU": "HOU",
    "IND": "IND",
    "JAC": "JAX",
    "JAX": "JAX",
    "KAN": "KC",
    "KC": "KC",
    "LAC": "LAC",
    "LA": "LAR",
    "LAR": "LAR",
    "LV": "LV",
    "MIA": "MIA",
    "MIN": "MIN",
    "NE": "NE",
    "NO": "NO",
    "NOR": "NO",
    "NWE": "NE",
    "NYG": "NYG",
    "NYJ": "NYJ",
    "OAK": "LV",
    "PHI": "PHI",
    "PIT": "PIT",
    "SD": "LAC",
    "SEA": "SEA",
    "SF": "SF",
    "SFO": "SF",
    "STL": "LAR",
    "TB": "TB",
    "TAM": "TB",
    "TEN": "TEN",
    "WAS": "WAS",
    "WFT": "WAS",
    "WSH": "WAS",
}


def canonicalize_team(abbreviation: str) -> str:
    """Return the canonical current abbreviation for a known source abbreviation."""
    canonical = CANONICAL_TEAM_ALIASES.get(abbreviation.upper())
    if canonical is None:
        raise ValueError(f"unknown NFL team abbreviation: {abbreviation!r}")
    return canonical
