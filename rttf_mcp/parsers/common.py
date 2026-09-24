"""Хелперы, общие для всех парсеров rttf.ru.

Все функции чистые: принимают строку или узел, возвращают значение, в сеть
не ходят.

Особенности записи чисел на сайте, замеренные 2026-09-24:
- минус в дельтах — знак U+2212 «−», а в рейтинге после турнира бывает и
  ASCII «-» (профиль 226194: «<dfn>-1</dfn>»);
- разделитель тысяч — апостроф: «1'189», «134'555»;
- пустая ячейка дельты означает ноль (рейтинг до и после совпадает).

Неразрывный пробел и минус записываются экранированно ("\\xa0",
"\\u2212"): литеральные символы в исходнике невидимы или неотличимы от
дефиса и теряются при копировании.
"""

import re
from typing import Any, TypeVar

from bs4 import BeautifulSoup

from rttf_mcp.errors import NotFound, ParseError

T = TypeVar("T")

_MINUS = "−"
_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_INT = re.compile(r"[+-]?\d+")
_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")
_WIN_LOSS = re.compile(r"(\d+)\s*-\s*(\d+)")
_SCORE = re.compile(r"^(\d+)\s*:\s*(\d+)$")
_ID = re.compile(r"(?:^|/)(players|tournaments|halls|items)/(\d+)(?:[?#]|$)")
_RU_DATE = re.compile(r"(\d{1,2})\s+([а-яё]+)\.?\s+(\d{4})", re.IGNORECASE)
# Сводка профиля пишет месяц полностью в родительном падеже («24 марта
# 2013»), сводка пар — сокращённо («22 фев 2025»). Сличение по трём первым
# буквам покрывает обе формы; «май»/«мая» расходятся уже на третьей.
_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5,
    "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def clean(text: str) -> str:
    """Схлопывает пробелы и неразрывные пробелы, обрезает края."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _normalise_number(text: str) -> str:
    return clean(text).replace(_MINUS, "-").replace("'", "").replace(" ", "")


def parse_date(text: str) -> str:
    """DD.MM.YYYY -> YYYY-MM-DD."""
    match = _DATE.search(text)
    if match is None:
        raise ParseError("parse_date", "DD.MM.YYYY", f"получено {text!r}")
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def parse_ru_date(text: str) -> str:
    """'24 марта 2013' | '22 фев 2025' -> YYYY-MM-DD."""
    match = _RU_DATE.search(clean(text))
    if match is None:
        raise ParseError("parse_ru_date", "Д месяц ГГГГ", f"получено {text!r}")
    day, month_word, year = match.groups()
    month = _MONTHS.get(month_word.lower()[:3])
    if month is None:
        raise ParseError("parse_ru_date", "месяц", f"неизвестный месяц {month_word!r}")
    return f"{year}-{month:02d}-{int(day):02d}"


def parse_int(text: str) -> int:
    """Целое со знаком: '1'189' -> 1189, '−18' -> -18, '-1' -> -1."""
    match = _INT.search(_normalise_number(text))
    if match is None:
        raise ParseError("parse_int", "целое", f"получено {text!r}")
    return int(match.group())


def parse_delta(text: str) -> float:
    """Изменение рейтинга: '+0.2' -> 0.2, '−18.8' -> -18.8, '' -> 0.0.

    Пустая ячейка — это ноль, а не отсутствие данных: сайт не пишет «0».
    Замерено на профиле 226194, турнир 243702: рейтинг до 12, после 12,
    итоговая ячейка пуста.
    """
    normalised = _normalise_number(text)
    if not normalised:
        return 0.0
    match = _NUMBER.fullmatch(normalised)
    if match is None:
        raise ParseError("parse_delta", "±число", f"получено {text!r}")
    return float(match.group())


def parse_win_loss(text: str) -> tuple[int, int]:
    """'3 - 1' | '800 - 389' | '87-42' -> (победы, поражения)."""
    match = _WIN_LOSS.search(_normalise_number(text).replace("(", " ").replace(")", " "))
    if match is None:
        raise ParseError("parse_win_loss", "В - П", f"получено {text!r}")
    return int(match.group(1)), int(match.group(2))


def parse_score(raw: str) -> tuple[int | None, int | None, str]:
    """Счёт матча '3 : 0' -> (3, 0, 'win').

    Исход: 'win' (N > M), 'loss' (N <= M) или 'unparsed' — форма, которой
    в замерах не было, то есть изменившаяся вёрстка. Технических
    результатов в замерах RTTF не встретилось; если появятся, они придут
    как 'unparsed' с исходной записью в score_raw, а не будут угаданы.
    """
    match = _SCORE.match(clean(raw))
    if match is None:
        return None, None, "unparsed"
    score_for, score_against = int(match.group(1)), int(match.group(2))
    return score_for, score_against, "win" if score_for > score_against else "loss"


def parse_minutes(text: str) -> int | None:
    """'12 мин.' -> 12; пустая ячейка -> None (сайт длительность не знает)."""
    cleaned = clean(text)
    if not cleaned:
        return None
    return parse_int(cleaned)


def extract_id(href: str, kind: str) -> str:
    """Числовой идентификатор из ссылки вида 'players/28415?type=d'."""
    for match in _ID.finditer(href):
        if match.group(1) == kind:
            return match.group(2)
    raise ParseError("extract_id", f"{kind}/<id>", f"получено {href!r}")


def raise_if_not_found(soup: BeautifulSoup, what: str) -> None:
    """Страница 404 сайта: main.page404. Сайт отдаёт её с кодом 404, но
    проверка по разметке страхует от ответа 200 с той же страницей."""
    if soup.select_one("main.page404") is not None:
        raise NotFound(f"{what} — страница не найдена")


def require(node: T | None, selector: str, parser: str) -> T:
    """Якорь: нет узла — значит вёрстка изменилась, а не данных нет."""
    if node is None:
        raise ParseError(parser, selector)
    return node


def text_of(node: Any) -> str:
    """Очищенный текст узла BeautifulSoup."""
    return clean(node.get_text())
