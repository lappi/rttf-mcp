"""Разбор профиля игрока /players/<id>.

Страница смешивает две категории данных, которые сайт разделяет в
robots.txt: уровень турнира (место, рейтинг до и после, баланс побед)
открыт, уровень матча (соперник, счёт, длительность, дельта за матч)
закрыт. Флаг include_matches решает, извлекается ли второй уровень; при
False его ключей в ответе нет вовсе, а парсер не касается строк матчей.

Устройство страницы, замерено 2026-09-24:
- section.player-info — ФИО, город, разряд, рука, инвентарь, вкладки
  категорий ul#tabs li[data-tab=rat_X];
- main.players > section.rat_X — содержимое категории X. Состав разный:
  rat_s: максимум, график, сводка, лучшие победы, результаты; rat_d:
  сводка и результаты, своего рейтинга нет (dfn пуст); rat_f (ФНТР):
  только максимум и график.
- В результатах — не больше 10 последних турниров категории; «все
  результаты» ведут на закрытый /results.
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
    parse_minutes,
    parse_ru_date,
    parse_score,
    parse_win_loss,
    raise_if_not_found,
    require,
    soup_of,
    text_of,
)

PARSER = "parse_player"

# Коды вкладок, чей смысл установлен замером. Остальные коды из списка
# data-tabs (h, p, m, w, r) в замерах не встретились ни разу: их смысл не
# угадывается, категория отдаётся под своим кодом с подписью сайта.
KNOWN_CATEGORIES = {"s": "одиночный разряд", "d": "парный разряд", "f": "рейтинг ФНТР"}

_CHART = re.compile(r"chartAll(Labels|Data)_(\w)\s*=\s*(\[[^\]]*\])")
_PLACE = re.compile(r"(\d+)\s*место")
_SIGNED_INT = re.compile(r"-?\d+")
_MAX = re.compile(r"\(([^)]*)\)")
_HALL_COUNT = re.compile(r"турниров:\s*(\d+)")
_EQUIPMENT = {"Основание": "blade", "Накладка справа": "rubber_right", "Накладка слева": "rubber_left"}
_INFO_FIELDS = {"город": "city", "Разряд": "sports_rank", "Игровая рука": "playing_hand"}


def parse_player(html: str, player_id: str, *, include_matches: bool) -> dict:
    soup = soup_of(html)
    raise_if_not_found(soup, f"/players/{player_id}")
    info = require(soup.select_one("section.player-info"), "section.player-info", PARSER)
    name = text_of(require(info.find("h1"), "section.player-info h1", PARSER))
    tabs = require(info.select_one("ul#tabs"), "section.player-info ul#tabs", PARSER)

    profile: dict = {"player_id": player_id, "name": name}
    fields = _labelled_fields(info)
    for label, key in _INFO_FIELDS.items():
        profile[key] = fields.get(label)
    profile["equipment"] = _equipment(info)

    charts = _charts(html)
    categories: dict[str, dict] = {}
    for tab in tabs.select("li[data-tab]"):
        code = tab["data-tab"].removeprefix("rat_")
        section = require(
            soup.select_one(f"main.players > section.rat_{code}"),
            f"main.players > section.rat_{code}",
            PARSER,
        )
        categories[code] = _category(code, tab, section, charts, include_matches)
    if not categories:
        raise ParseError(PARSER, "ul#tabs li[data-tab]", "у профиля нет ни одной категории рейтинга")
    profile["categories"] = categories
    if include_matches:
        rows = soup.select("main.players > section.rat_s section.player-wins tbody tr")
        wins = [row for row in rows if "all" in (row.get("class") or [])]
        # Строки есть, а «за всё время» среди них нет — сменились классы
        # интервалов; пустой best_wins выглядел бы как «побед нет».
        if rows and not wins:
            raise ParseError(PARSER, "section.player-wins tbody tr.all", f"строк {len(rows)}, с классом all — 0")
        profile["best_wins"] = [_best_win(row) for row in wins]
    return profile


def _labelled_fields(info: Tag) -> dict[str, str]:
    fields = {}
    for p in info.find_all("p", recursive=False):
        strong = p.find("strong")
        text = text_of(p)
        if strong is None or ":" not in text:
            continue
        fields[text.split(":", 1)[0].strip()] = text_of(strong)
    return fields


def _equipment(info: Tag) -> dict:
    found: dict = {key: None for key in _EQUIPMENT.values()}
    for p in info.find_all("p", recursive=False):
        label = text_of(p).split(":", 1)[0].strip()
        key = _EQUIPMENT.get(label)
        if key is None:
            continue
        link = require(p.find("a", href=True), f"{label}: a[href^=items/]", PARSER)
        found[key] = {"item_id": extract_id(link["href"], "items"), "name": text_of(link)}
    return found


def _charts(html: str) -> dict[str, dict[str, list]]:
    """Полная история рейтинга из встроенного скрипта графика.

    Массивы chartAllLabels_X и chartAllData_X — это JavaScript-литералы из
    строк и целых; разбираются регулярками, без eval.
    """
    charts: dict[str, dict[str, list]] = {}
    for kind, code, body in _CHART.findall(html):
        items = [item.strip() for item in body.strip("[]").split(",") if item.strip()]
        if kind == "Labels":
            values: list = [item.strip('"') for item in items]
        else:
            values = [parse_int(item) if _SIGNED_INT.fullmatch(item) else _bad_chart(code, item) for item in items]
        charts.setdefault(code, {})[kind] = values
    return charts


def _bad_chart(code: str, item: str):
    raise ParseError(PARSER, f"chartAllData_{code}", f"не целое {item!r}")


def _history(code: str, charts: dict, expected: bool) -> list[dict]:
    chart = charts.get(code)
    if chart is None:
        # У категории с максимумом график на странице есть всегда (замерено
        # на s и f); его пропажа — вёрстка, а не пустая история.
        if expected:
            raise ParseError(PARSER, f"chartAllLabels_{code} / chartAllData_{code}")
        return []
    labels, data = chart.get("Labels", []), chart.get("Data", [])
    if len(labels) != len(data):
        raise ParseError(PARSER, f"chartAll*_{code}", f"подписей {len(labels)}, значений {len(data)}")
    # Первая подпись бывает пустой: это стартовый рейтинг до первого
    # турнира, у него нет даты.
    return [
        {"date": parse_date(label) if label else None, "rating": rating}
        for label, rating in zip(labels, data)
    ]


def _category(code: str, tab: Tag, section: Tag, charts: dict, include_matches: bool) -> dict:
    dfn = tab.find("dfn")
    rating_text = text_of(dfn) if dfn is not None else ""
    category: dict = {
        "label": KNOWN_CATEGORIES.get(code, clean(tab.find(string=True) or "")),
        "rating_current": parse_int(rating_text) if rating_text else None,
    }
    maximum = section.select_one(f"span#maxAll_{code}")
    if maximum is not None:
        category["rating_max"] = parse_int(text_of(require(maximum.find("dfn"), f"#maxAll_{code} dfn", PARSER)))
        date_match = _MAX.search(text_of(maximum))
        date_text = date_match.group(1).strip() if date_match else ""
        category["rating_max_date"] = parse_date(date_text) if date_text else None
    else:
        category["rating_max"] = None
        category["rating_max_date"] = None
    category["rating_history"] = _history(code, charts, expected=maximum is not None)

    stats = section.select_one("section.player-stats")
    category["stats"] = _stats(stats) if stats is not None else None

    results = section.select_one("section.player-results")
    if results is None:
        category["recent_tournaments"] = None
        category["recent_tournaments_truncated"] = None
    else:
        doubles = code == "d"
        tournaments = _results(results, doubles=doubles, include_matches=include_matches)
        category["recent_tournaments"] = tournaments
        played = category["stats"]["tournaments_played"] if category["stats"] else None
        category["recent_tournaments_truncated"] = (
            played > len(tournaments) if played is not None else None
        )
    return category


def _stats(section: Tag) -> dict:
    cells = [text_of(td) for td in section.find_all("td")]
    pairs = dict(zip(cells[0::2], cells[1::2]))
    stats: dict = {
        "rank_position": None,
        "tournaments_played": None,
        "games": None,
        "wins": None,
        "losses": None,
        "first_tournament_date": None,
        "most_frequent_hall": None,
    }
    for label, value in pairs.items():
        if label.startswith("Место в рейтинге"):
            stats["rank_position"] = parse_int(value)
        elif label.startswith("Сыграно турниров"):
            stats["tournaments_played"] = parse_int(value)
        elif label.startswith("Игры") or label.startswith("Игр "):
            stats["games"] = parse_int(value.split("(")[0])
            stats["wins"], stats["losses"] = parse_win_loss(value)
        elif label.startswith("Первый турнир"):
            stats["first_tournament_date"] = parse_ru_date(value)
    hall_link = section.select_one('a[href^="halls/"]')
    if hall_link is not None:
        count = _HALL_COUNT.search(text_of(hall_link.parent))
        stats["most_frequent_hall"] = {
            "hall_id": extract_id(hall_link["href"], "halls"),
            "name": text_of(hall_link),
            "tournaments": int(count.group(1)) if count else None,
        }
    if stats["tournaments_played"] is None:
        raise ParseError(PARSER, "section.player-stats td «Сыграно турниров»")
    return stats


def _results(section: Tag, *, doubles: bool, include_matches: bool) -> list[dict]:
    tournaments = []
    link: Tag | None = None
    participants: int | None = None
    tables = 0
    for child in section.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "a" and child.get("href", "").lstrip("/").startswith("tournaments/"):
            link, participants = child, None
        elif child.name == "kbd" and link is not None:
            participants = parse_int(text_of(child))
        elif child.name == "table":
            tables += 1
            if link is not None:
                parse = _doubles_entry if doubles else _singles_entry
                tournaments.append(parse(link, participants, child, include_matches))
                link = None
    # Таблица без ссылки-заголовка перед ней молча пропала бы, и список
    # последних турниров укоротился бы правдоподобно — а это вёрстка.
    if len(tournaments) != tables:
        raise ParseError(
            PARSER, "section.player-results > a[href*=tournaments/] + table",
            f"таблиц {tables}, разобрано турниров {len(tournaments)}",
        )
    return tournaments


def _heading(link: Tag) -> dict:
    """'20.09.2026 16:00 <var>отк</var> NEO TTA' -> дата, время, лимит, зал."""
    var = link.find("var")
    limit = text_of(var) if var is not None else None
    before, _, after = clean(link.get_text(" ")).partition(limit or "\0")
    parts = before.split()
    if not parts:
        raise ParseError(PARSER, "a[href*=tournaments/] «ДД.ММ.ГГГГ ЧЧ:ММ»", f"получено {text_of(link)!r}")
    return {
        "tournament_id": extract_id(link["href"], "tournaments"),
        "date": parse_date(parts[0]),
        "start_time": parts[1] if len(parts) > 1 else None,
        "limit": limit,
        "hall_name": clean(after) or None,
    }


def _place(header: Tag) -> int | None:
    for i in header.find_all("i"):
        match = _PLACE.search(text_of(i))
        if match:
            return int(match.group(1))
    return None


def _footer(table: Tag) -> Tag:
    # Итоговая строка лежит прямо в table, после tbody.
    rows = table.find_all("tr", recursive=False)
    return require(rows[-1] if rows else None, "table > tr (итог турнира)", PARSER)


def _singles_entry(link: Tag, participants: int | None, table: Tag, include_matches: bool) -> dict:
    header = require(table.select_one("thead tr td"), "thead tr td (шапка турнира)", PARSER)
    footer = _footer(table)
    ths = footer.find_all("th")
    if len(ths) < 3:
        raise ParseError(PARSER, "table > tr th ×3 (итог турнира)", f"найдено {len(ths)}")
    gift = header.select_one("i.gift")
    entry = _heading(link) | {
        "participants": participants,
        "place": _place(header),
        "rating_before_event": parse_int(text_of(require(header.find("dfn"), "шапка dfn", PARSER))),
        "rating_after_event": parse_int(text_of(require(footer.find("dfn"), "итог dfn", PARSER))),
        "rating_delta": parse_delta(text_of(ths[1])),
        "bonus": parse_delta(text_of(gift)) if gift is not None else None,
    }
    entry["wins"], entry["losses"] = parse_win_loss(text_of(ths[0]))
    if include_matches:
        entry["duration_minutes_total"] = parse_minutes(text_of(ths[2]))
        entry["matches"] = [_singles_match(row) for row in table.select("tbody tr")]
    return entry


def _singles_match(row: Tag) -> dict:
    cells = row.find_all("td")
    if len(cells) < 7:
        raise ParseError(PARSER, "tbody tr td ×7 (матч)", f"найдено {len(cells)}")
    opponent = require(cells[2].find("a", href=True), "td a[href^=players/] (соперник)", PARSER)
    score_raw = text_of(cells[4])
    score_for, score_against, result = parse_score(score_raw)
    return {
        "start_time": text_of(cells[0]) or None,
        "stage": text_of(cells[1]) or None,
        "opponent_id": extract_id(opponent["href"], "players"),
        "opponent_name": text_of(opponent),
        "opponent_rating_at_event": parse_int(text_of(require(cells[3].find("dfn"), "td dfn (рейтинг соперника)", PARSER))),
        "score_for": score_for,
        "score_against": score_against,
        "score_raw": score_raw,
        "result": result,
        "rating_delta": parse_delta(text_of(cells[5])),
        "duration_minutes": parse_minutes(text_of(cells[6])),
    }


def _pair(cell: Tag) -> list[dict]:
    people = []
    for a in cell.select('a[href^="players/"]'):
        dfn = a.find_next_sibling("dfn")
        people.append(
            {
                "player_id": extract_id(a["href"], "players"),
                "name": text_of(a),
                "rating_at_event": parse_int(text_of(dfn)) if dfn is not None and text_of(dfn) else None,
            }
        )
    if len(people) != 2:
        raise ParseError(PARSER, "пара: a[href^=players/] ×2", f"найдено {len(people)}")
    return people


def _doubles_entry(link: Tag, participants: int | None, table: Tag, include_matches: bool) -> dict:
    header_cells = require(table.select_one("thead tr"), "thead tr (шапка пары)", PARSER).find_all("td")
    if len(header_cells) < 2:
        raise ParseError(PARSER, "thead tr td ×2 (шапка пары)", f"найдено {len(header_cells)}")
    pair = _pair(header_cells[0])
    footer_ths = _footer(table).find_all("th")
    if len(footer_ths) < 2:
        raise ParseError(PARSER, "table > tr th ×2 (итог пары)", f"найдено {len(footer_ths)}")
    entry = _heading(link) | {
        "participants": participants,
        "place": _place(header_cells[0]),
        "pair": pair,
        "pair_rating_at_event": parse_int(text_of(require(header_cells[1].find("dfn"), "шапка пары dfn", PARSER))),
    }
    entry["wins"], entry["losses"] = parse_win_loss(text_of(footer_ths[0]))
    if include_matches:
        entry["duration_minutes_total"] = parse_minutes(text_of(footer_ths[1]))
        entry["matches"] = [_doubles_match(row) for row in table.select("tbody tr")]
    return entry


def _doubles_match(row: Tag) -> dict:
    cells = row.find_all("td")
    if len(cells) < 6:
        raise ParseError(PARSER, "tbody tr td ×6 (парный матч)", f"найдено {len(cells)}")
    score_raw = text_of(cells[4])
    score_for, score_against, result = parse_score(score_raw)
    return {
        "start_time": text_of(cells[0]) or None,
        "stage": text_of(cells[1]) or None,
        "opponents": _pair(cells[2]),
        "opponents_pair_rating_at_event": parse_int(text_of(require(cells[3].find("dfn"), "td dfn (сумма пары)", PARSER))),
        "score_for": score_for,
        "score_against": score_against,
        "score_raw": score_raw,
        "result": result,
        "duration_minutes": parse_minutes(text_of(cells[5])),
    }


def _best_win(row: Tag) -> dict:
    cells = row.find_all("td")
    if len(cells) < 7:
        raise ParseError(PARSER, "section.player-wins tr td ×7", f"найдено {len(cells)}")
    link = require(cells[0].find("a", href=True), "лучшая победа: a[href^=tournaments/]", PARSER)
    opponent = require(cells[2].find("a", href=True), "лучшая победа: соперник", PARSER)
    score_raw = text_of(cells[4])
    score_for, score_against, result = parse_score(score_raw)
    heading = _heading(link)
    return {
        "tournament_id": heading["tournament_id"],
        "date": heading["date"],
        "limit": heading["limit"],
        "hall_name": heading["hall_name"],
        "player_rating_at_event": parse_int(text_of(cells[1])),
        "opponent_id": extract_id(opponent["href"], "players"),
        "opponent_name": text_of(opponent),
        "opponent_rating_at_event": parse_int(text_of(cells[3])),
        "score_for": score_for,
        "score_against": score_against,
        "score_raw": score_raw,
        "result": result,
        "rating_delta": parse_delta(text_of(cells[5])),
        "duration_minutes": parse_minutes(text_of(cells[6])),
    }
