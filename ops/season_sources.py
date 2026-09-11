"""Free-source capture and normalization for the live season worker."""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import polars as pl
import week1_live

ALIASES = {"LAR": "LA", "WSH": "WAS"}
TEAM_NAMES = {
    "49ers": "SF",
    "Bears": "CHI",
    "Bengals": "CIN",
    "Bills": "BUF",
    "Broncos": "DEN",
    "Browns": "CLE",
    "Buccaneers": "TB",
    "Cardinals": "ARI",
    "Chargers": "LAC",
    "Chiefs": "KC",
    "Colts": "IND",
    "Commanders": "WAS",
    "Cowboys": "DAL",
    "Dolphins": "MIA",
    "Eagles": "PHI",
    "Falcons": "ATL",
    "Giants": "NYG",
    "Jaguars": "JAX",
    "Jets": "NYJ",
    "Lions": "DET",
    "Packers": "GB",
    "Panthers": "CAR",
    "Patriots": "NE",
    "Raiders": "LV",
    "Rams": "LA",
    "Ravens": "BAL",
    "Saints": "NO",
    "Seahawks": "SEA",
    "Steelers": "PIT",
    "Texans": "HOU",
    "Titans": "TEN",
    "Vikings": "MIN",
}
REQUIRED_GAMES = 272


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("CLOCK_MUST_BE_UTC")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _alias(team: str) -> str:
    return ALIASES.get(team, team)


def _curl_download(url: str, cfg: dict[str, Any], root: Path) -> tuple[bytes, str | None]:
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=root, delete=False) as body_handle:
        body_path = Path(body_handle.name)
    with tempfile.NamedTemporaryFile(dir=root, delete=False) as header_handle:
        header_path = Path(header_handle.name)
    try:
        command = [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--max-time",
            str(cfg["request_timeout_seconds"]),
            "--dump-header",
            str(header_path),
            "--output",
            str(body_path),
            "--write-out",
            "%{url_effective}",
            url,
        ]
        completed = subprocess.run(command, check=True, capture_output=True)
        effective_url = completed.stdout.decode("utf-8", errors="strict")
        effective = urllib.parse.urlparse(effective_url)
        if effective.scheme != "https" or effective.hostname not in cfg["allowed_hosts"]:
            raise ValueError("SOURCE_REDIRECT_HOST_NOT_ALLOWED")
        modified = None
        for line in header_path.read_text(errors="replace").splitlines():
            if line.lower().startswith("last-modified:"):
                modified = line.split(":", 1)[1].strip()
        return body_path.read_bytes(), modified
    finally:
        body_path.unlink(missing_ok=True)
        header_path.unlink(missing_ok=True)


def _fetch(
    url: str, cfg: dict[str, Any], root: Path, clock, suffix: str
) -> tuple[bytes, dict[str, Any]]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in cfg["allowed_hosts"]:
        raise ValueError("SOURCE_HOST_NOT_ALLOWED")
    started = clock()
    if cfg.get("transport", "urllib") == "curl":
        body, modified = _curl_download(url, cfg, root)
    elif cfg.get("transport", "urllib") == "urllib":
        request = urllib.request.Request(url, headers={"User-Agent": "nfl-predictor/season-worker"})
        with urllib.request.urlopen(request, timeout=cfg["request_timeout_seconds"]) as response:
            body = response.read()
            modified = response.headers.get("Last-Modified")
    else:
        raise ValueError("SOURCE_TRANSPORT_UNSUPPORTED")
    captured = clock()
    digest = sha256(body).hexdigest()
    raw_path = root / "raw" / f"{digest}.{suffix}"
    week1_live.write_once(raw_path, body)
    if sha256(raw_path.read_bytes()).hexdigest() != digest:
        raise ValueError("RAW_HASH_MISMATCH")
    receipt = {
        "source_url": url,
        "request_started_at": _stamp(started),
        "captured_at": _stamp(captured),
        "source_last_modified": modified,
        "raw_sha256": digest,
    }
    week1_live.write_once(
        root / "captures" / f"{sha256(_canonical(receipt)).hexdigest()}.json", receipt
    )
    return body, receipt


def _status(
    receipt: dict[str, Any], status: str, reason: str | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "captured_at": receipt["captured_at"],
        "source_updated_at": receipt["source_last_modified"],
        "raw_sha256": receipt["raw_sha256"],
        **extra,
    }


def _competition(event: dict[str, Any]) -> dict[str, Any]:
    values = event.get("competitions")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError("ESPN_COMPETITION_INVALID")
    return values[0]


def _team(comp: dict[str, Any], side: str) -> tuple[str, dict[str, Any]]:
    matches = [x for x in comp.get("competitors", []) if x.get("homeAway") == side]
    if len(matches) != 1:
        raise ValueError("ESPN_TEAMS_INVALID")
    team = matches[0].get("team", {})
    abbreviation = team.get("abbreviation")
    if not isinstance(abbreviation, str):
        raise TypeError("ESPN_TEAM_ABBREVIATION_INVALID")
    return _alias(abbreviation), matches[0]


def _kickoff(comp: dict[str, Any], event: dict[str, Any]) -> str | None:
    if comp.get("timeValid") is not True:
        return None
    raw = comp.get("date") or event.get("date")
    if not isinstance(raw, str):
        raise TypeError("ESPN_VALID_KICKOFF_MISSING")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ValueError("ESPN_VALID_KICKOFF_INVALID") from error
    if parsed.tzinfo is None:
        raise ValueError("ESPN_VALID_KICKOFF_INVALID")
    return _stamp(parsed.astimezone(UTC).replace(microsecond=0))


def _score(competitor: dict[str, Any]) -> int | None:
    value = competitor.get("score")
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("ESPN_FINAL_SCORE_INVALID") from error
    if number < 0:
        raise ValueError("ESPN_FINAL_SCORE_INVALID")
    return number


def _games(scoreboard: bytes, rows: list[dict[str, str]], season: int) -> list[dict[str, Any]]:
    document = json.loads(scoreboard)
    mapping: dict[tuple[int, str, str], str] = {}
    for row in rows:
        if int(row["season"]) != season or row["game_type"] != "REG":
            continue
        key = (season, _alias(row["home_team"]), _alias(row["away_team"]))
        if key in mapping and mapping[key] != row["game_id"]:
            raise ValueError("NFLVERSE_TEAM_PAIR_AMBIGUOUS")
        mapping[key] = row["game_id"]
    games = []
    for event in document.get("events", []):
        if (
            event.get("season", {}).get("year") != season
            or event.get("season", {}).get("type") != 2
        ):
            continue
        comp = _competition(event)
        home, home_row = _team(comp, "home")
        away, away_row = _team(comp, "away")
        game_id = mapping.get((season, home, away))
        if game_id is None:
            raise ValueError("ESPN_NFLVERSE_GAME_UNMATCHED")
        time_valid = comp.get("timeValid") is True
        kickoff = _kickoff(comp, event)
        venue = comp.get("venue") if isinstance(comp.get("venue"), dict) else None
        weather = event.get("weather", comp.get("weather"))
        state = comp.get("status", {}).get("type", {})
        status = state.get("name")
        identity = {
            "season": season,
            "home": home,
            "away": away,
            "kickoff": kickoff,
            "venue_id": venue.get("id") if venue else None,
            "neutral_site": comp.get("neutralSite") is True,
        }
        outcome = None
        if (
            status == "STATUS_FINAL"
            and state.get("completed") is True
            and state.get("state") == "post"
        ):
            home_score, away_score = _score(home_row), _score(away_row)
            if home_score is None or away_score is None:
                raise ValueError("ESPN_FINAL_SCORE_INVALID")
            outcome = {
                "status": "FINAL",
                "home_score": home_score,
                "away_score": away_score,
                "source": "ESPN",
                "source_capture_at": None,
            }
        games.append(
            {
                "game_id": game_id,
                "source_event_id": str(event["id"]),
                "season": season,
                "week": int(event["week"]["number"]),
                "home": home,
                "away": away,
                "home_name": home_row["team"].get("displayName"),
                "away_name": away_row["team"].get("displayName"),
                "kickoff": kickoff,
                "time_valid": time_valid,
                "neutral_site": identity["neutral_site"],
                "venue": venue,
                "venue_identity": identity["venue_id"],
                "status": status,
                "schedule_version": sha256(_canonical(identity)).hexdigest(),
                "weather": weather,
                "result": outcome,
            }
        )
    if len(games) != REQUIRED_GAMES or len({g["game_id"] for g in games}) != REQUIRED_GAMES:
        raise ValueError("BLOCK_REQUIRED_SCHEDULE_INCOMPLETE")
    return sorted(games, key=lambda g: (g["week"], g["game_id"]))


class _ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.team: str | None = None
        self.in_team_title = False
        self.in_table = False
        self.in_cell = False
        self.cells: list[str] = []
        self.rows: list[tuple[str | None, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class") or ""
        if tag == "div" and "d3-o-section-sub-title" in classes:
            self.in_team_title = True
        if tag == "table" and "d3-o-reports--detailed" in classes:
            self.in_table = True
        if self.in_table and tag in ("td", "th"):
            self.in_cell = True
            self.cells.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.in_team_title:
            self.in_team_title = False
        if tag in ("td", "th"):
            self.in_cell = False
        if tag == "tr" and self.in_table and self.cells:
            self.rows.append((self.team, [re.sub(r"\s+", " ", x).strip() for x in self.cells]))
            self.cells = []
        if tag == "table":
            self.in_table = False

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.text.append(value)
            if self.in_team_title:
                self.team = value
            if self.in_cell:
                self.cells[-1] += (" " if self.cells[-1] else "") + value


def _injuries(
    body: bytes, receipt: dict[str, Any], season: int
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    parser = _ReportParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    full_text = " ".join(parser.text)
    week_match = re.search(
        rf"(?:Week\s+(\d+)\s+of\s+{season}|{season}[^<]{{0,80}}?Week\s+(\d+))",
        full_text,
        re.IGNORECASE,
    )
    headers = ["Player", "Position", "Injuries", "Practice Status", "Game Status"]
    reports: dict[str, list[dict[str, Any]]] = {}
    current_team: str | None = None
    valid = False
    for team, cells in parser.rows:
        if cells == headers:
            current_team = team
            valid = True
        elif valid and current_team and len(cells) == 5:
            reports.setdefault(TEAM_NAMES.get(current_team, current_team), []).append(
                dict(
                    zip(
                        ("player", "position", "injury", "practice_status", "game_status"),
                        cells,
                        strict=True,
                    )
                )
            )
    if not week_match or not valid:
        return {}, _status(receipt, "UNPARSED", "OFFICIAL_REPORT_STRUCTURE_UNVERIFIED")
    return reports, _status(
        receipt,
        "AVAILABLE",
        None,
        week=int(week_match.group(1) or week_match.group(2)),
        season=season,
        publication_time_basis="CAPTURE_ONLY",
    )


ARTICLE_TEAM_LABELS = {**TEAM_NAMES, "NINERS": "SF"}


def _inactive_links(body: bytes, maximum: int) -> list[str]:
    text = body.decode("utf-8", errors="replace")
    links = []
    for match in re.finditer(
        r'href=["\']([^"\']*/news/[^"\']*-inactives-[^"\']*)', text, re.IGNORECASE
    ):
        path = match.group(1)
        url = urllib.parse.urljoin("https://www.nfl.com", path)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "www.nfl.com" or not parsed.path.startswith("/news/"):
            continue
        if url not in links:
            links.append(url)
    return links[:maximum]


def _news_article(body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        text,
        re.IGNORECASE | re.DOTALL,
    ):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict) and item.get("@type") == "NewsArticle":
                return item
    raise ValueError("OFFICIAL_INACTIVES_NEWS_ARTICLE_MISSING")


def _inactive_article(
    body: bytes, game: dict[str, Any], source_url: str, *,
    captured_at: datetime | None = None, maximum_age_seconds: int | None = None,
) -> tuple[list[dict[str, Any]], str]:
    article = _news_article(body)
    headline = article.get("headline")
    description = article.get("description")
    published = article.get("datePublished")
    body_text = article.get("articleBody")
    if not all(isinstance(value, str) for value in (headline, description, published, body_text)):
        raise TypeError("OFFICIAL_INACTIVES_ARTICLE_FIELDS_INVALID")
    context = f"{headline} {description}"
    published_time = datetime.fromisoformat(published)
    modified_time = datetime.fromisoformat(article.get("dateModified") or published)
    if published_time.tzinfo is None or modified_time.tzinfo is None or modified_time < published_time:
        raise ValueError("OFFICIAL_INACTIVES_INVALID_SOURCE_TIME")
    effective_time = max(published_time, modified_time)
    if captured_at is not None and effective_time > captured_at:
        raise ValueError("OFFICIAL_INACTIVES_SOURCE_TIME_FROM_FUTURE")
    stated_years = {int(value) for value in re.findall(r"\b20\d{2}\b", context)}
    if stated_years and stated_years != {game["season"]}:
        raise ValueError("OFFICIAL_INACTIVES_SEASON_MISMATCH")
    week = re.search(r"Week\s+(\d+)", context, re.IGNORECASE)
    if week and int(week.group(1)) != game["week"]:
        raise ValueError("OFFICIAL_INACTIVES_WEEK_MISMATCH")
    expected = f"{game['away_name']} at {game['home_name']}"
    if any(expected.casefold() not in value.casefold() for value in (headline, description)):
        raise ValueError("OFFICIAL_INACTIVES_TEAMS_MISMATCH")
    if maximum_age_seconds is None:
        import tomllib
        maximum_age_seconds = tomllib.loads((Path(__file__).resolve().parents[1] / "configs/season_live.toml").read_text())["maximum_inactive_publication_age_seconds"]
    kickoff = datetime.fromisoformat(game["kickoff"]) if game["kickoff"] else None
    if kickoff is None or kickoff.tzinfo is None or not 0 < (kickoff - published_time).total_seconds() <= maximum_age_seconds or effective_time >= kickoff:
        raise ValueError("OFFICIAL_INACTIVES_NOT_PREGAME")
    lines = [re.sub(r"\s+", " ", line).strip() for line in body_text.splitlines()]
    headings: list[tuple[int, str]] = []
    expected_teams = {game["away"], game["home"]}
    for index, line in enumerate(lines):
        team = ARTICLE_TEAM_LABELS.get(line.upper()) or TEAM_NAMES.get(line.title())
        if team in expected_teams:
            headings.append((index, team))
    if len(headings) != 2 or {team for _, team in headings} != expected_teams:
        raise ValueError("OFFICIAL_INACTIVES_TEAM_SECTIONS_MISSING")
    headings.sort()
    rows = []
    for heading_index, (start, team) in enumerate(headings):
        end = headings[heading_index + 1][0] if heading_index + 1 < len(headings) else len(lines)
        for line in lines[start + 1 : end]:
            if not line:
                continue
            player = re.fullmatch(r"([A-Z]{1,3})\s+(.+)", line)
            if player is None:
                raise ValueError("OFFICIAL_INACTIVES_PLAYER_ROW_INVALID")
            rows.append(
                {
                    "team": team,
                    "position": player.group(1),
                    "player": player.group(2),
                    "emergency_third_qb": "emergency third qb" in player.group(2).casefold(),
                    "source_url": source_url,
                    "published_at": _stamp(published_time),
                    "modified_at": _stamp(modified_time),
                }
            )
    if {row["team"] for row in rows} != expected_teams:
        raise ValueError("OFFICIAL_INACTIVES_TEAM_SECTIONS_EMPTY")
    return rows, _stamp(effective_time)


def _inactives(
    landing: bytes,
    cfg: dict[str, Any],
    root: Path,
    clock,
    games: list[dict[str, Any]],
) -> tuple[dict[str, tuple[list[dict[str, Any]], dict[str, Any]]], dict[str, Any]]:
    links = _inactive_links(landing, cfg["maximum_inactive_articles"])
    matched: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
    errors = []
    receipts = []
    for url in links:
        try:
            body, receipt = _fetch(url, {**cfg, "allowed_hosts": ["www.nfl.com"]}, root, clock, "html")
            receipts.append(receipt)
            article = _news_article(body)
            candidates = [
                game
                for game in games
                if game["away_name"]
                and game["home_name"]
                and f"{game['away_name']} at {game['home_name']}".casefold()
                in f"{article.get('headline', '')} {article.get('description', '')}".casefold()
            ]
            if len(candidates) != 1:
                raise ValueError("OFFICIAL_INACTIVES_GAME_MATCH_AMBIGUOUS")
            rows, published = _inactive_article(body, candidates[0], url, captured_at=datetime.fromisoformat(receipt["captured_at"]), maximum_age_seconds=cfg["maximum_inactive_publication_age_seconds"])
            check = _status(receipt, "AVAILABLE", None, source_updated_at=published)
            event_id = candidates[0]["game_id"]
            previous = matched.get(event_id)
            if previous is None or datetime.fromisoformat(published) > datetime.fromisoformat(previous[1]["source_updated_at"]):
                matched[event_id] = (rows, check)
        except Exception as error:  # noqa: BLE001 - individual official article is reported
            errors.append(f"{url}: {type(error).__name__}: {error}")
    base = {
        "status": "AVAILABLE" if matched else "MISSING",
        "reason": None if matched else "NO_VERIFIED_PREGAME_OFFICIAL_INACTIVES",
        "articles_discovered": len(links),
        "articles_verified": len(matched),
        "article_errors": errors,
        "captured_at": receipts[-1]["captured_at"] if receipts else None,
        "source_updated_at": None,
        "raw_sha256": None,
    }
    return matched, base


def _depth(
    body: bytes, receipt: dict[str, Any], cfg: dict[str, Any], clock: datetime
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    frame = pl.read_parquet(io.BytesIO(body))
    latest = max(frame["dt"].drop_nulls().to_list())
    updated = datetime.fromisoformat(latest)
    age = (clock.astimezone(UTC) - updated).total_seconds()
    latest_rows = frame.filter(
        (pl.col("dt") == latest) & (pl.col("pos_abb") == "QB") & (pl.col("pos_rank") == 1)
    )
    if age < 0:
        raise ValueError("DEPTH_SOURCE_UPDATED_AT_FROM_FUTURE")
    quarterbacks: dict[str, dict[str, Any]] = {}
    for row in latest_rows.iter_rows(named=True):
        team = _alias(row["team"])
        if team in quarterbacks:
            raise ValueError("DEPTH_STARTING_QB_AMBIGUOUS")
        quarterbacks[team] = {
            "player_name": row["player_name"],
            "espn_id": row["espn_id"],
            "gsis_id": row["gsis_id"],
        }
    status = "AVAILABLE" if age <= cfg["maximum_depth_age_seconds"] else "STALE"
    reason = None if status == "AVAILABLE" else "DEPTH_CAPTURE_TOO_OLD"
    check = _status(receipt, status, reason, source_updated_at=_stamp(updated), age_seconds=age)
    return quarterbacks, check


def _team_check(
    check: dict[str, Any], available: dict[str, Any], teams: tuple[str, str], reason: str
) -> dict[str, Any]:
    if check["status"] == "AVAILABLE" and any(team not in available for team in teams):
        return {**check, "status": "MISSING", "reason": reason}
    return check


def _input(check: dict[str, Any], data: Any) -> dict[str, Any]:
    return {
        "captured_at": check["captured_at"],
        "source_updated_at": check.get("source_updated_at"),
        "status": check["status"],
        "reason": check.get("reason"),
        "data": data,
        "used_by_model": False,
    }


def _required_check(
    receipt: dict[str, Any], cfg: dict[str, Any], now: datetime, **extra: Any
) -> dict[str, Any]:
    captured = datetime.fromisoformat(receipt["captured_at"])
    age = (now.astimezone(UTC) - captured).total_seconds()
    if age < 0:
        raise ValueError("BLOCK_REQUIRED_CAPTURE_FROM_FUTURE")
    if age > cfg["maximum_capture_age_seconds"]:
        raise ValueError("BLOCK_REQUIRED_CAPTURE_STALE")
    return _status(receipt, "AVAILABLE", None, age_seconds=age, **extra)


def fetch_sources(cfg: dict[str, Any], root: Path, clock) -> dict[str, Any]:
    """Capture every configured free source and return normalized season inputs."""
    if (
        cfg.get("allow_paid_usage") is not False
        or cfg.get("zero_dollar", cfg.get("zero_dollar_mode")) is not True
    ):
        raise ValueError("ZERO_DOLLAR_GUARD")
    captures = {
        "source_url": _fetch(cfg["source_url"], cfg, root, clock, "csv"),
        "scoreboard_url": _fetch(cfg["scoreboard_url"], cfg, root, clock, "json"),
    }
    optional_errors: dict[str, str] = {}
    for key, suffix in (
        ("injuries_url", "html"),
        ("inactives_url", "html"),
        ("depth_url", "parquet"),
    ):
        try:
            captures[key] = _fetch(cfg[key], cfg, root, clock, suffix)
        except Exception as error:  # noqa: BLE001 - optional source failure is reported
            optional_errors[key] = f"{type(error).__name__}: {error}"
    now = clock()
    csv_body, csv_receipt = captures["source_url"]
    rows = list(csv.DictReader(io.StringIO(csv_body.decode("utf-8-sig"))))
    if not rows:
        raise ValueError("BLOCK_REQUIRED_NFLVERSE_CSV_EMPTY")
    scoreboard_body, scoreboard_receipt = captures["scoreboard_url"]
    games = _games(scoreboard_body, rows, int(cfg["season"]))
    missing = {"captured_at": _stamp(now), "source_last_modified": None, "raw_sha256": None}
    if "injuries_url" in captures:
        reports, injury_check = _injuries(*captures["injuries_url"], int(cfg["season"]))
    else:
        reports = {}
        injury_check = _status(missing, "MISSING", optional_errors["injuries_url"])
    if "inactives_url" in captures:
        inactive_matches, inactive_check = _inactives(
            captures["inactives_url"][0], cfg, root, clock, games
        )
        inactive_check["captured_at"] = captures["inactives_url"][1]["captured_at"]
        inactive_check["raw_sha256"] = captures["inactives_url"][1]["raw_sha256"]
    else:
        inactive_matches = {}
        inactive_check = _status(missing, "MISSING", optional_errors["inactives_url"])
    if "depth_url" in captures:
        try:
            qbs, depth_check = _depth(*captures["depth_url"], cfg, now)
        except Exception as error:  # noqa: BLE001 - optional source failure is reported
            qbs = {}
            depth_check = _status(
                captures["depth_url"][1], "UNPARSED", f"{type(error).__name__}: {error}"
            )
    else:
        qbs = {}
        depth_check = _status(missing, "MISSING", optional_errors["depth_url"])
    csv_check = _required_check(csv_receipt, cfg, now, rows=len(rows))
    scoreboard_check = _required_check(scoreboard_receipt, cfg, now, games=len(games))
    checks = {
        "schedule": scoreboard_check,
        "results": scoreboard_check,
        "nflverse_games": csv_check,
        "espn_scoreboard": scoreboard_check,
        "nflverse_history": csv_check,
        "injuries": injury_check,
        "inactives": inactive_check,
        "depth": depth_check,
    }
    per_game = {}
    for game in games:
        if game["result"] is not None:
            game["result"]["source_capture_at"] = scoreboard_receipt["captured_at"]
        weather_check = {
            "captured_at": scoreboard_receipt["captured_at"],
            "source_updated_at": None,
            "status": "PROVENANCE_LIMITED" if game["weather"] is not None else "MISSING",
            "reason": "ESPN_SOURCE_UPDATED_AT_UNAVAILABLE"
            if game["weather"] is not None
            else "ESPN_WEATHER_UNAVAILABLE",
        }
        teams = (game["home"], game["away"])
        game_injury_check = _team_check(
            injury_check, reports, teams, "PARTICIPATING_TEAM_INJURY_REPORT_MISSING"
        )
        if injury_check.get("week") != game["week"]:
            game_injury_check = {
                **injury_check,
                "status": "MISSING",
                "reason": "NO_OFFICIAL_INJURY_REPORT_FOR_GAME_WEEK",
            }
        game_depth_check = _team_check(
            depth_check, qbs, teams, "PARTICIPATING_TEAM_EXPECTED_QB_MISSING"
        )
        inputs = {
            "injuries": _input(
                game_injury_check,
                [
                    item
                    for team in (game["home"], game["away"])
                    for item in (reports.get(team) or [])
                ]
                if game_injury_check["status"] == "AVAILABLE"
                else [],
            ),
            "inactives": _input(
                inactive_matches.get(
                    game["game_id"],
                    (
                        [],
                        {
                            **inactive_check,
                            "status": "MISSING",
                            "reason": "NO_VERIFIED_PREGAME_OFFICIAL_INACTIVES_FOR_GAME",
                        },
                    ),
                )[1],
                inactive_matches.get(game["game_id"], ([], inactive_check))[0],
            ),
            "expected_qb": _input(
                game_depth_check,
                {game["home"]: qbs.get(game["home"]), game["away"]: qbs.get(game["away"])},
            ),
            "weather": _input(weather_check, game["weather"]),
        }
        game["inputs"] = inputs
        per_game[game["game_id"]] = inputs
    teams = {game[side] for game in games for side in ("home", "away")}
    if len(teams) != 32:
        raise ValueError("BLOCK_REQUIRED_UNKNOWN_TEAM_SET")
    return {"games": games, "rows": rows, "checks": checks, "per_game_inputs": per_game}
