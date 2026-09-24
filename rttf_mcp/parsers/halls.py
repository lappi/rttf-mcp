"""Разбор страницы зала /halls/<id>.

Контакты зала (section.hall-info mark[itemprop=telephone]) не
извлекаются намеренно: там имена и личные телефоны сотрудников, а
модели для вопросов о рейтинге они не нужны.
"""

from bs4 import Tag

from rttf_mcp.errors import ParseError
from rttf_mcp.parsers.common import (
    extract_id,
    parse_int,
    raise_if_not_found,
    require,
    soup_of,
    text_of,
)

PARSER = "parse_hall"


def parse_hall(html: str, hall_id: str) -> dict:
    soup = soup_of(html)
    raise_if_not_found(soup, f"/halls/{hall_id}")
    info = require(soup.select_one("section.hall-info"), "section.hall-info", PARSER)
    name = text_of(require(info.select_one("h1[itemprop=name]"), "section.hall-info h1[itemprop=name]", PARSER))
    address = info.select_one("mark[itemprop=address]")
    fields = _marked_fields(info)
    site = _labelled_link(info, "Официальный сайт")
    representative = _labelled_link(info, "Представитель на сайте")
    tables = fields.get("Кол-во столов")
    hall: dict = {
        "hall_id": hall_id,
        "name": name,
        "address": text_of(address) if address is not None else None,
        "metro": [text_of(i) for i in info.select("p i.m")] or None,
        "opening_hours": fields.get("Время работы"),
        "tables": parse_int(tables) if tables else None,
        "website": site["href"] if site is not None else None,
        "representative_player_id": (
            extract_id(representative["href"], "players") if representative is not None else None
        ),
    }
    hall["amenities"], hall["paid_amenities"] = _amenities(info)
    hall["rating"] = _rating(soup.select_one("section.hall-rating"))
    return hall


def _marked_fields(info: Tag) -> dict[str, str]:
    fields = {}
    for p in info.find_all("p", recursive=False):
        mark = p.find("mark")
        text = text_of(p)
        if mark is not None and ":" in text:
            fields[text.split(":", 1)[0].strip()] = text_of(mark)
    return fields


def _labelled_link(info: Tag, label: str) -> Tag | None:
    for p in info.find_all("p", recursive=False):
        if text_of(p).startswith(f"{label}:"):
            return p.find("a", href=True)
    return None


def _amenities(info: Tag) -> tuple[list[str], list[str]]:
    # Удобства — значки i[title] в одном абзаце; платные идут после слова
    # «Платно:». Порядок сохраняется как на сайте.
    for p in info.find_all("p", recursive=False):
        icons = p.select("i[title]")
        if not icons or p.select_one("i.m") is not None:
            continue
        free, paid, is_paid = [], [], False
        for node in p.children:
            if isinstance(node, str) and "Платно" in node:
                is_paid = True
            elif isinstance(node, Tag) and node.name == "i" and node.get("title"):
                (paid if is_paid else free).append(node["title"])
        return free, paid
    return [], []


def _rating(section: Tag | None) -> dict | None:
    if section is None:
        return None
    overall = require(section.select_one("h2 dfn"), "section.hall-rating h2 dfn", PARSER)
    aspects = {}
    for row in section.select("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            raise ParseError(PARSER, "section.hall-rating tr td ×3", f"найдено {len(cells)}")
        aspects[text_of(require(cells[0].find("b"), "section.hall-rating td b", PARSER))] = float(text_of(cells[2]))
    return {"overall": float(text_of(overall)), "aspects": aspects}
