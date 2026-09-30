"""Disjoint NFL calendar-week usage, with explicit unavailable period changes."""
from fantasy.usage import aggregate, summarize

SHARES = {"target_share": ("targets", "team_targets"),
          "carry_share": ("carries", "team_carries"),
          "snap_share": ("snaps", "team_snaps")}


def weekly_trends(rows, games, schedule):
    catalog = summarize(rows, games, "season")  # Includes duplicate-player/game validation.
    completed = {g["game_id"] for g in games}
    if len({g["game_id"] for g in schedule}) != len(schedule):
        raise ValueError("duplicate scheduled game")
    last_week = max((g["week"] for g in games), default=0)
    lookup = {(r["player_id"], r["game_id"], r["team"]): r for r in rows}
    result = {}
    for player in catalog:
        periods = []
        for week in range(1, last_week + 1):
            selected = sorted((g for g in schedule if g["week"] == week
                               and player["team"] in g["teams"]),
                              key=lambda g: (g["date"], g["game_id"]))
            sample = [lookup.get((player["player_id"], g["game_id"], player["team"]), {})
                      if g["game_id"] in completed else {} for g in selected]
            finished = bool(selected) and all(g["game_id"] in completed for g in selected)
            if player["transfer"]:
                status = "Team history unresolved; period change unavailable"
            elif not schedule:
                status = "Schedule unavailable; refresh fantasy sources"
            elif not selected:
                status = "No scheduled game (bye or schedule gap)"
            elif not finished:
                status = "Scheduled game not completed / result unavailable"
            elif not all(sample):
                status = "Player observation missing"
            else:
                status = "Completed; metric coverage shown separately"
            metrics = {}
            for key, (numerator, denominator) in SHARES.items():
                metric = aggregate(sample, numerator, denominator,
                                   blocked=player["transfer"] or not finished)
                metric.update(opportunities=aggregate(sample, numerator)["value"],
                              team_opportunities=aggregate(sample, denominator)["value"],
                              delta_pp=None, delta_reason=None)
                previous = periods[-1] if periods else None
                if previous is None:
                    metric["delta_reason"] = "No prior week in this season"
                elif metric["value"] is None:
                    metric["delta_reason"] = "Current week unavailable: " + status
                elif previous["metrics"][key]["value"] is None:
                    metric["delta_reason"] = "Prior week unavailable: " + previous["status"]
                else:
                    prior_ids = set(previous["game_ids"])
                    if prior_ids.intersection(g["game_id"] for g in selected):
                        raise ValueError("comparison periods overlap")
                    metric["delta_pp"] = 100 * (metric["value"] - previous["metrics"][key]["value"])
                if metric["delta_pp"] is None and status.startswith("Completed"):
                    if metric["value"] is None:
                        metric["delta_reason"] = "Current week has missing counts or zero denominator"
                    elif previous and previous["metrics"][key]["value"] is None:
                        metric["delta_reason"] = "Prior week lacks a complete, nonzero denominator sample"
                metrics[key] = metric
            periods.append({"week": week, "prior_week": week - 1 if week > 1 else None,
                            "games": selected, "game_ids": [g["game_id"] for g in selected],
                            "expected_games": len(selected), "observed_games": sum(bool(r) for r in sample),
                            "status": status, "metrics": metrics})
        result[player["player_id"]] = {"team": player["team"], "transfer": player["transfer"],
                                       "periods": periods}
    return result
