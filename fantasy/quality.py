"""Credited opportunities and explicit player-game attribution checks."""
from fantasy.usage import number

COMPONENTS = ("rushing_yards", "receiving_yards", "rushing_tds", "receiving_tds", "receptions")
EXCLUSIONS = ["incomplete_or_duplicate_pbp", "missing_box_counts", "opportunity_reconciliation",
              "missing_context", "depth_reconciliation", "missing_scoring_components",
              "invalid_scoring_components", "missing_play_outcomes", "outcome_reconciliation"]
DEFINITIONS = {
    "rz_targets": "Credited targets starting at or inside the opponent's 20-yard line.",
    "rz_carries": "Credited carries starting at or inside the opponent's 20-yard line.",
    "i10_targets": "Credited targets starting at or inside the opponent's 10-yard line.",
    "i10_carries": "Credited carries starting at or inside the opponent's 10-yard line.",
    "i5_targets": "Credited targets starting at or inside the opponent's 5-yard line.",
    "i5_carries": "Credited carries starting at or inside the opponent's 5-yard line.",
    "target_depth": "Sum of verified target air yards / credited targets, including incompletions and negative depths. Never a route metric.",
}


def credited(play):
    return (play.get("play_type") in {"pass", "run", "qb_kneel", "qb_spike"}
            and number(play.get("play_deleted")) == 0
            and number(play.get("two_point_attempt")) == 0)


def outcome(play, kind, player_id):
    """Labels only. An explicitly incomplete pass earns zero receiving points."""
    result = dict.fromkeys(COMPONENTS, 0.0)
    prefix = "receiving" if kind == "target" else "rushing"
    caught = number(play.get("complete_pass")) if kind == "target" else 1
    td = number(play.get("pass_touchdown" if kind == "target" else "rush_touchdown"))
    yards = number(play.get(prefix + "_yards")) if caught == 1 else 0.0
    if caught not in (0, 1) or td not in (0, 1) or yards is None:
        return None
    if td == 1 and not play.get("td_player_id"):
        return None
    result[prefix + "_yards"] = yards
    result[prefix + "_tds"] = float(td == 1 and play["td_player_id"] == player_id)
    result["receptions"] = caught if kind == "target" else 0.0
    return result


def audit_player(player, plays):
    metrics = {k: None for k in DEFINITIONS if k != "target_depth"}
    metrics.update(depth_sum=None, depth_count=None, targets=number(player.get("targets")),
                   carries=number(player.get("carries")))
    components = {k: number(player.get(k)) for k in COMPONENTS}
    result = {"metrics": metrics, "components": components, "events": [], "reason": None}
    keys = [p.get("play_id") for p in plays]
    if (not any(str(p.get("desc", "")).strip().upper() == "END GAME" for p in plays)
            or None in keys or len(keys) != len(set(keys))):
        result["reason"] = "incomplete_or_duplicate_pbp"
        return result
    pid, team = player["player_id"], player["team"]
    selected = [p for p in plays if p.get("posteam") == team and credited(p)]
    reasons = []
    for kind, stat, flag, identity in [("target", "targets", "pass_attempt", "receiver_player_id"),
                                       ("carry", "carries", "rush_attempt", "rusher_player_id")]:
        opportunities = [p for p in selected if number(p.get(flag)) == 1 and p.get(identity) == pid]
        expected = metrics[stat]
        if expected is None or expected < 0 or expected != int(expected):
            reasons.append("missing_box_counts")
            continue
        if len(opportunities) != expected:
            reasons.append("opportunity_reconciliation")
            continue
        yardlines = [number(p.get("yardline_100")) for p in opportunities]
        field_ok = all(y is not None and 0 <= y <= 100 for y in yardlines)
        if field_ok:
            for name, edge in [("rz", 20), ("i10", 10), ("i5", 5)]:
                metrics[name + "_" + stat] = sum(y <= edge for y in yardlines)
        else:
            reasons.append("missing_context")
        if kind == "target":
            depths = [number(p.get("air_yards")) for p in opportunities]
            metrics["depth_count"] = sum(d is not None for d in depths)
            if all(d is not None for d in depths):
                total = sum(depths)
                if number(player.get("receiving_air_yards")) == total:
                    metrics["depth_sum"] = total
                else:
                    reasons.append("depth_reconciliation")
            else:
                reasons.append("missing_context")
        for play in opportunities:
            result["events"].append({"context": {
                "position": player["position"], "kind": kind,
                "yardline": number(play.get("yardline_100")),
                "depth": number(play.get("air_yards")) if kind == "target" else None,
            }, "components": outcome(play, kind, pid)})
    if any(v is None for v in components.values()):
        reasons.append("missing_scoring_components")
    elif any(v < 0 or v != int(v) for k, v in components.items() if not k.endswith("yards")):
        reasons.append("invalid_scoring_components")
    elif any(e["components"] is None for e in result["events"]):
        reasons.append("missing_play_outcomes")
    elif any(abs(sum(e["components"][k] for e in result["events"]) - components[k]) > 1e-6
             for k in COMPONENTS):
        reasons.append("outcome_reconciliation")
    result["reason"] = next((r for r in EXCLUSIONS if r in reasons), None)
    return result
