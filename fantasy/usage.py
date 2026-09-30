"""Pure historical aggregations; a missing observation is never a zero."""
from collections import defaultdict
from math import isfinite

POSITIONS = {"WR", "RB", "TE"}

METRICS = {
    "targets": ("Targets", "Credited receiving targets, including incomplete passes."),
    "target_share": ("Target share", "Sum of player targets / sum of all team targets in the same games. Not pass attempts; not an average of weekly percentages."),
    "carries": ("Carries", "Credited rushing attempts from player box scores."),
    "carry_share": ("Carry share", "Sum of player carries / sum of team carries, including QB rushes, in the same games."),
    "snap_share": ("Snap share", "Sum of offensive snaps / sum of team offensive snaps. Team totals must be uniquely recoverable from all PFR counts and rounded percentages. Snaps are not routes."),
    "air_yards": ("Air yards", "Sum of receiving air yards on targets, complete or incomplete. Negative air yards are valid."),
    "red_zone": ("Red-zone opps", "Targets plus carries on credited plays starting at or inside the opponent's 20. Excludes no-plays, deleted plays and two-point tries. Requires completed PBP and matching player target/carry counts."),
}
WINDOW_NOTE = (
    "Last game and last three use the latest observed team's completed games, not the "
    "player's last appearances. Season uses all completed games for that team. Byes do not "
    "consume games. A missed game or absent player row stays unavailable, not zero; no "
    "older game replaces it. A confirmed zero in a published row remains zero. Incomplete "
    "windows show coverage but no full-window total/share. Samples can differ between players."
)


def number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if isfinite(n) else None
    except (ValueError, TypeError):
        return None


def aggregate(rows, numerator, denominator=None, *, blocked=False):
    pairs = []
    for row in rows:
        n = number(row.get(numerator))
        d = number(row.get(denominator)) if denominator else None
        valid_n = n is not None and (numerator == "air_yards" or n >= 0)
        valid_d = not denominator or (d is not None and d >= 0 and n is not None and n <= d)
        if valid_n and valid_d:
            pairs.append((n, d))
    complete = bool(rows) and len(pairs) == len(rows) and not blocked
    numerator_sum = sum(n for n, _ in pairs) if pairs else None
    denominator_sum = sum(d for _, d in pairs) if pairs and denominator else None
    value = None
    if complete:
        value = numerator_sum if not denominator else (
            numerator_sum / denominator_sum if denominator_sum and denominator_sum > 0 else None
        )
    return {"value": value, "covered_games": len(pairs), "expected_games": len(rows),
            "numerator": numerator_sum if complete else None,
            "denominator": denominator_sum if complete else None}


def summarize(rows, games, window):
    if window not in {"last", "last3", "season"}:
        raise ValueError("Invalid window")
    by_player = defaultdict(list)
    seen = set()
    games_by_id = {g["game_id"]: g for g in games}
    for row in rows:
        key = (row["player_id"], row["game_id"])
        if key in seen:
            raise ValueError("duplicate player game")
        seen.add(key)
        if row["position"] in POSITIONS and row["game_id"] in games_by_id:
            by_player[row["player_id"]].append(row)
    result = []
    for player_id, observed in by_player.items():
        observed.sort(key=lambda r: (games_by_id[r["game_id"]]["date"], r["game_id"]))
        latest = observed[-1]
        transfer = len({r["team"] for r in observed}) > 1
        selected = sorted((g for g in games if latest["team"] in g["teams"]),
                          key=lambda g: (g["date"], g["game_id"]))
        if window != "season":
            selected = selected[-(1 if window == "last" else 3):]
        lookup = {r["game_id"]: r for r in observed if r["team"] == latest["team"]}
        sample = [lookup.get(g["game_id"], {}) for g in selected]
        blocked = transfer and window == "season"
        specifications = {"targets": ("targets", None), "target_share": ("targets", "team_targets"),
                          "carries": ("carries", None), "carry_share": ("carries", "team_carries"),
                          "snap_share": ("snaps", "team_snaps"), "air_yards": ("air_yards", None),
                          "red_zone": ("red_zone", None)}
        metrics = {k: aggregate(sample, n, d, blocked=blocked)
                   for k, (n, d) in specifications.items()}
        result.append({"player_id": player_id, "name": latest["name"],
                       "position": latest["position"], "team": latest["team"],
                       "transfer": transfer, "weeks": [g["week"] for g in selected],
                       "observed_games": sum(bool(r) for r in sample),
                       "expected_games": len(selected), "metrics": metrics,
                       "window_note": "Team change detected; full-season values unavailable without dated roster history. Short windows use latest observed team." if transfer else WINDOW_NOTE,
                       "games": [{**g, "observed": bool(r), "usage": r} for g, r in zip(selected, sample)]})
    return sorted(result, key=lambda p: (p["name"], p["player_id"]))
