"""Что сервер готов отдавать: флаг, закрытые категории, правила robots.txt.

Решение вынесено из server.py, чтобы читать его в одном месте и проверять
без сети.

Два правила, и они независимы:

1. Адреса. Сервер запрашивает только то, что разрешает robots.txt, — в
   обоих режимах. Клиент проверяет каждый адрес функцией `blocking_rule`
   до обращения к сети. Флаг на это правило не влияет.
2. Категории данных. Сайт закрыл матчи, очные встречи и поиск по имени.
   Флаг RTTF_MCP_ALLOW_RESTRICTED решает, извлекаем ли мы данные уровня
   матча, когда они попались на разрешённой странице (профиль игрока).
   При выключенном флаге инструменты этой категории не регистрируются
   вовсе.
"""

import os
import re
from collections.abc import Iterable, Mapping
from urllib.parse import unquote

FLAG = "RTTF_MCP_ALLOW_RESTRICTED"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})

# Дословно из https://rttf.ru/robots.txt, снято 2026-09-24. Порядок как на
# сайте. При обновлении — копировать целиком, не пересказывать.
ROBOTS_DISALLOW: tuple[str, ...] = (
    "/actions",
    "/blacklist",
    "/changes",
    "/error",
    "/finances",
    "/likes",
    "/login",
    "/markers",
    "/messages",
    "/metro",
    "/names",
    "/notes",
    "/novices",
    "/ratings",
    "/ratscoms",
    "/ratshalls",
    "/recovery",
    "/search",
    "/settings",
    "/send",
    "/?ajax*",
    "/*/edit",
    "/*?*sort=*",
    "/*?*order=*",
    "/*?*date_from=*",
    "/*?*date_to=*",
    "/*?*name=*",
    "/*?*title=*",
    "/*?*metro=*",
    "/*?*isChilds=*",
    "/*?*isTours=*",
    "/comments",
    "/games",
    "/results",
    "/rivals",
    "/?fullver=",
)

# Инструменты уровня матча и поиска по имени: имя -> основание запрета.
# search_players здесь нет сознательно: поиск по имени на сайте возможен
# только через закрытый адрес (?name=, /search), а закрытые адреса не
# запрашиваются ни в каком режиме. См. раздел «Отклонения от спеки» плана.
RESTRICTED_TOOLS: dict[str, str] = {
    "get_player_matches": "Disallow: /games, /results — отдельные матчи",
    "get_head_to_head": "Disallow: /rivals — очные встречи",
}


class FlagError(ValueError):
    """Значение флага не распознано: сервер не запускается, а не гадает."""


def restricted_allowed(environ: Mapping[str, str] | None = None) -> bool:
    """Читает флаг. Неизвестное значение — ошибка запуска.

    Молча трактовать «maybe» или опечатку «ture» как выключенный флаг значит
    запустить сервер не в том режиме, в каком его настраивали; трактовать как
    включённый — открыть закрытое без явного согласия. Поэтому отказ.
    """
    env = os.environ if environ is None else environ
    raw = env.get(FLAG, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise FlagError(
        f"{FLAG}={env.get(FLAG)!r} не распознано; "
        f"включить: {sorted(_TRUE)}, выключить: {sorted(_FALSE - {''})} или не задавать"
    )


def _compile(rule: str) -> re.Pattern[str]:
    # Синтаксис robots.txt: префиксное совпадение, "*" — любая
    # последовательность, "$" в конце — якорь. Остальное литерально.
    anchored = rule.endswith("$")
    body = rule[:-1] if anchored else rule
    pattern = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(pattern + ("$" if anchored else ""))


_COMPILED = tuple((rule, _compile(rule)) for rule in ROBOTS_DISALLOW)
_ARRAY_SUFFIX = re.compile(r"\[\]=")


def blocking_rule(path: str, query: str = "") -> str | None:
    """Правило robots.txt, закрывающее адрес, или None, если адрес открыт.

    query — строка запроса без «?», как она уйдёт в сеть. Сравнение идёт
    по раскодированной строке, в которой у имён параметров снят суффикс
    «[]»: форма самого сайта шлёт фильтр метро как metro[]=386, и буква
    правила /*?*metro=* его бы не поймала, хотя намерение владельца —
    закрыть именно этот фильтр. Проверка строже буквы, но не слабее.
    """
    plain = _ARRAY_SUFFIX.sub("=", unquote(query))
    target = path + (f"?{plain}" if plain else "")
    for rule, compiled in _COMPILED:
        if compiled.match(target):
            return rule
    return None


def tools_to_register(names: Iterable[str], allow_restricted: bool) -> list[str]:
    """Какие из инструментов сервера показывать модели."""
    names = list(names)
    unknown = set(RESTRICTED_TOOLS) - set(names)
    if unknown:
        # Опечатка в имени закрытого инструмента здесь молча открыла бы его:
        # фильтр по несовпадающему имени ничего не отфильтрует.
        raise ValueError(f"закрытые инструменты не найдены среди инструментов сервера: {sorted(unknown)}")
    if allow_restricted:
        return names
    return [name for name in names if name not in RESTRICTED_TOOLS]
