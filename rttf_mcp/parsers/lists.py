"""Разбор списков: игроки /players/ и турниры /tournaments/.

Ни у одного списка нет разрешённой пагинации: кнопки «ещё» ходят на
закрытый /?ajax, параметр page сайт игнорирует. Замерено 2026-09-24.

Игроки листаются окнами по рейтингу (rat_to). Окно сравнивается с
ВНУТРЕННИМ дробным рейтингом, а показывается округлённое целое: при
rat_to=990 игрок с показанным 990 и внутренним 990.4 в выдачу не
попадает. Поэтому следующее окно начинается с «наименьший показанный + 1»
— с перекрытием, а не с пропуском.
"""

import re

from bs4 import Tag

from rttf_mcp.errors import ParseError
from rttf_mcp.parsers.common import (
    extract_id,
    parse_date,
    parse_int,
    require,
    soup_of,
    text_of,
)

PAGE_SIZE = 100
MIN_WINDOW = 1

_CITY = re.compile(r"cities\[\]=(\w+)")
_DATE_ROW = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
_REGISTERED = re.compile(r"^(\d+)(?:\s*/\s*(\d+))?$")
_TOTAL = re.compile(r"итого:\s*(\d+)")


def parse_players_list(html: str, max_rating: int | None) -> dict:
    """Строки списка игроков и курсор следующего окна.

    max_rating — верхняя граница, с которой запрашивали окно (None —
    вершина рейтинга). Нужна, чтобы распознать застревание: если все сто
    строк окна имеют один и тот же рейтинг, следующее окно совпало бы с
    текущим, и сайт отдал бы те же сто строк.
    """
    parser = "parse_players_list"
    soup = soup_of(html)
    table = require(soup.select_one("section.players-list > table"), "section.players-list > table", parser)
    players = [_player_row(row, parser) for row in table.find_all("tr") if row.find("td")]
    if len(players) < PAGE_SIZE:
        return {"players": players, "next_max_rating": None, "stalled_on_tie": False}
    next_max = min(p["rating"] for p in players) + 1
    stalled = max_rating is not None and next_max >= max_rating
    # Окно ниже 1 сайт молча игнорирует и отдаёт вершину рейтинга (замерено),
    # а инструмент такой курсор отвергает: дальше разрешёнными окнами не пройти.
    below_floor = next_max < MIN_WINDOW
    return {
        "players": players,
        "next_max_rating": None if stalled or below_floor else next_max,
        "stalled_on_tie": stalled,
    }


def _player_row(row: Tag, parser: str) -> dict:
    cells = row.find_all("td")
    if len(cells) < 6:
        raise ParseError(parser, "section.players-list tr td ×6", f"найдено {len(cells)}")
    link = require(cells[1].find("a", href=True), "td a[href^=players/]", parser)
    city_link = cells[3].find("a", href=True)
    city = None
    if city_link is not None and text_of(city_link):
        match = _CITY.search(city_link["href"])
        city = {"city_id": match.group(1) if match else None, "name": text_of(city_link)}
    name = text_of(link)
    return {
        "rank": parse_int(text_of(cells[0])),
        "player_id": extract_id(link["href"], "players"),
        "name": name,
        # Корпоративные команды стоят в общем рейтинге наравне с людьми и
        # помечены хвостом « _» в имени: «Компания _». Замерено: 5 из 100
        # строк вершины.
        "is_team": name.endswith(" _"),
        "city": city,
        "rating": parse_int(text_of(require(cells[4].find("dfn"), "td dfn (рейтинг)", parser))),
        "updated": parse_date(text_of(cells[5])),
    }


def parse_tournaments_list(html: str) -> dict:
    """Ближайшие и прошедшие турниры: по сотне строк, без листания."""
    parser = "parse_tournaments_list"
    soup = soup_of(html)
    upcoming = require(
        soup.select_one("section.tours-list:not(.tours-list2) > table"),
        "section.tours-list:not(.tours-list2) > table",
        parser,
    )
    past = require(soup.select_one("section.tours-list2 > table"), "section.tours-list2 > table", parser)
    upcoming_rows, upcoming_total = _tournament_rows(upcoming, parser)
    past_rows, past_total = _tournament_rows(past, parser)
    return {
        "upcoming": upcoming_rows,
        "upcoming_total": upcoming_total,
        "upcoming_truncated": _truncated(upcoming_rows, upcoming_total),
        "past": past_rows,
        "past_total": past_total,
        "past_truncated": _truncated(past_rows, past_total),
    }


def _truncated(rows: list, total: int | None) -> bool:
    # Строку «итого: N» сайт выводит, когда список короче своего потолка;
    # тогда она и есть ответ. Без неё полная сотня означает: дальше есть,
    # но достать нельзя — подгрузка идёт через закрытый /?ajax.
    if total is not None:
        return total > len(rows)
    return len(rows) >= PAGE_SIZE


def _tournament_rows(table: Tag, parser: str) -> tuple[list[dict], int | None]:
    rows = []
    total: int | None = None
    current_date: str | None = None
    for tr in table.find_all("tr", recursive=False):
        classes = tr.get("class") or []
        if "date" in classes:
            match = _DATE_ROW.search(text_of(tr))
            if match is None:
                raise ParseError(parser, "tr.date th", f"получено {text_of(tr)!r}")
            current_date = parse_date(match.group(1))
            continue
        if not tr.has_attr("onclick"):
            summary = _TOTAL.search(text_of(tr))
            if summary is not None:
                total = int(summary.group(1))
            elif tr.find("td", recursive=False) is not None:
                raise ParseError(parser, "tr[onclick] | tr.date | «итого: N»", f"получено {text_of(tr)!r}")
            continue  # шапка из th
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 6:
            raise ParseError(parser, "tr[onclick] td ×7", f"найдено {len(cells)}")
        if current_date is None:
            raise ParseError(parser, "tr.date перед строкой турнира")
        link = require(cells[2].find("a", href=True), "td a[href^=tournaments/]", parser)
        link_classes = " ".join(link.get("class") or [])
        registered, limit = _registered(cells[4], parser)
        average = text_of(cells[3])
        games = text_of(cells[5])
        rows.append(
            {
                "tournament_id": extract_id(link["href"], "tournaments"),
                "date": current_date,
                "start_time": text_of(cells[0]) or None,
                "limit": text_of(cells[1]) or None,
                "hall_name": text_of(link),
                "doubles": "doubles" in link_classes,
                "ladder": "ladder" in link_classes,
                "live": "live" in classes,
                "registration_open": "reg" in classes,
                "average_rating": parse_int(average) if average else None,
                "registered": registered,
                "registration_limit": limit,
                "games": parse_int(games) if games else None,
            }
        )
    return rows, total


def _registered(cell: Tag, parser: str) -> tuple[int | None, int | None]:
    text = text_of(cell)
    if not text:
        return None, None
    match = _REGISTERED.match(text)
    if match is None:
        raise ParseError(parser, "td kbd (заявки)", f"получено {text!r}")
    return int(match.group(1)), int(match.group(2)) if match.group(2) else None
