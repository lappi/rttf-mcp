"""Разбор страницы турнира /tournaments/<id>.

Уровень турнира открыт целиком: метаданные и итоговая таблица. Матчей
на странице нет — этапы (section.tour-stage-N) выложены картинками, а
«список игр» ведёт на закрытый /games (у пар — /gamesd, под тем же
правилом).

Замерено 2026-09-24:
- section.tour-header: time «ДД.ММ.ГГГГ<br>день<br>ЧЧ:ММ», h1 с лимитом
  в var, h3 — название;
- section.tour-desc: kbd «заявилось/лимит», «Клуб: a[href^=halls/]»,
  «Адрес: mark», картинка i-doubles-*.svg у парного турнира;
- section.tour-results есть только у сыгранного турнира. Одиночная
  таблица — table.tour-players, парная — table.tour-doubles (без дельты и
  рейтинга после: сайт их для пар не публикует).
"""

import re

from bs4 import Tag

from rttf_mcp.errors import ParseError
from rttf_mcp.parsers.common import (
    clean,
    extract_id,
    parse_date,
    parse_delta,
    parse_int,
    parse_win_loss,
    raise_if_not_found,
    require,
    soup_of,
    text_of,
)

PARSER = "parse_tournament"

_TIME = re.compile(r"\b(\d{1,2}:\d{2})\b")
_REGISTERED = re.compile(r"^(\d+)(?:\s*/\s*(\d+))?$")
_COUNTS = {
    "participants": re.compile(r"Участников:\s*(\d+)"),
    "pairs": re.compile(r"Пар:\s*(\d+)"),
    "players": re.compile(r"Игроков:\s*(\d+)"),
    "games": re.compile(r"Игр:\s*(\d+)"),
}
_AVERAGE = re.compile(r"Средний рейтинг[^:]*:\s*([\d.]+)")
_COEFFICIENT = re.compile(r"Коэффициент турнира:\s*([\d.]+)")


def parse_tournament(html: str, tournament_id: str) -> dict:
    soup = soup_of(html)
    raise_if_not_found(soup, f"/tournaments/{tournament_id}")
    header = require(soup.select_one("section.tour-header"), "section.tour-header", PARSER)
    desc = require(soup.select_one("section.tour-desc"), "section.tour-desc", PARSER)
    # Части разделены <br>: без разделителя get_text склеил бы их в
    # «20.09.2026воскресенье16:00», и время не нашлось бы.
    when = clean(require(header.find("time"), "section.tour-header time", PARSER).get_text(" "))
    time_match = _TIME.search(when)
    limit_node = header.select_one("h1 var")
    hall_link = desc.select_one('a[href^="halls/"]')

    tournament: dict = {
        "tournament_id": tournament_id,
        "title": text_of(require(header.find("h3"), "section.tour-header h3", PARSER)),
        "date": parse_date(when),
        "start_time": time_match.group(1) if time_match else None,
        "limit": text_of(limit_node) if limit_node is not None else None,
        "doubles": desc.select_one('img[src*="i-doubles"]') is not None,
        "hall": (
            {"hall_id": extract_id(hall_link["href"], "halls"), "name": text_of(hall_link)}
            if hall_link is not None
            else None
        ),
        "address": _labelled_mark(desc, "Адрес"),
    }
    tournament["registered"], tournament["registration_limit"] = _registered(desc)

    results = soup.select_one("section.tour-results")
    tournament["has_results"] = results is not None
    summary = text_of(results) if results is not None else ""
    for key, pattern in _COUNTS.items():
        match = pattern.search(summary)
        tournament[key] = int(match.group(1)) if match else None
    average = _AVERAGE.search(summary)
    coefficient = _COEFFICIENT.search(summary)
    tournament["average_rating"] = float(average.group(1)) if average else None
    tournament["coefficient"] = float(coefficient.group(1)) if coefficient else None

    if results is None:
        tournament["standings"] = []
    elif tournament["doubles"]:
        table = require(results.select_one("table.tour-doubles"), "table.tour-doubles", PARSER)
        tournament["standings"] = [_doubles_row(row) for row in table.select("tbody tr")]
    else:
        table = require(results.select_one("table.tour-players"), "table.tour-players", PARSER)
        tournament["standings"] = [_singles_row(row) for row in table.select("tbody tr")]
    if results is not None and not tournament["standings"]:
        raise ParseError(PARSER, "итоговая таблица tbody tr", "секция результатов есть, строк нет")
    return tournament


def _labelled_mark(desc: Tag, label: str) -> str | None:
    for p in desc.find_all("p"):
        if text_of(p).startswith(f"{label}:"):
            mark = p.find("mark")
            return text_of(mark) if mark is not None else None
    return None


def _registered(desc: Tag) -> tuple[int | None, int | None]:
    kbd = desc.find("kbd")
    if kbd is None:
        return None, None
    match = _REGISTERED.match(text_of(kbd))
    if match is None:
        raise ParseError(PARSER, "section.tour-desc kbd", f"получено {text_of(kbd)!r}")
    return int(match.group(1)), int(match.group(2)) if match.group(2) else None


def _games_and_sets(games_cell: Tag, sets_cell: Tag) -> dict:
    wins, losses = parse_win_loss(text_of(games_cell))
    sets_won, sets_lost = parse_win_loss(text_of(sets_cell))
    return {"wins": wins, "losses": losses, "sets_won": sets_won, "sets_lost": sets_lost}


def _singles_row(row: Tag) -> dict:
    cells = row.find_all("td")
    if len(cells) < 8:
        raise ParseError(PARSER, "table.tour-players tr td ×8", f"найдено {len(cells)}")
    player = require(cells[1].find("a", href=True), "td a[href^=players/]", PARSER)
    return {
        "place": parse_int(text_of(cells[0])),
        "player_id": extract_id(player["href"], "players"),
        "name": text_of(player),
        "city": text_of(cells[2]) or None,
        "rating_before_event": parse_int(text_of(cells[3])),
        "rating_delta": parse_delta(text_of(cells[4])),
        "rating_after_event": parse_int(text_of(cells[5])),
    } | _games_and_sets(cells[6], cells[7])


def _doubles_row(row: Tag) -> dict:
    cells = row.find_all("td")
    if len(cells) < 5:
        raise ParseError(PARSER, "table.tour-doubles tr td ×5", f"найдено {len(cells)}")
    pair = []
    for a in cells[1].select('a[href^="players/"]'):
        dfn = a.find_next_sibling("dfn")
        pair.append(
            {
                "player_id": extract_id(a["href"], "players"),
                "name": text_of(a),
                "rating_at_event": parse_int(text_of(dfn)) if dfn is not None and text_of(dfn) else None,
            }
        )
    if len(pair) != 2:
        raise ParseError(PARSER, "table.tour-doubles пара a ×2", f"найдено {len(pair)}")
    return {
        "place": parse_int(text_of(cells[0])),
        "pair": pair,
        "pair_rating_at_event": parse_int(text_of(cells[2])),
    } | _games_and_sets(cells[3], cells[4])
