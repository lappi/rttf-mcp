"""MCP-сервер над rttf.ru.

Докстринги инструментов — интерфейс для языковой модели. В них описаны
ограничения источника, без знания которых модель делает неверные выводы.

Какие инструменты видит модель, решает policy.py: при выключенном
RTTF_MCP_ALLOW_RESTRICTED инструменты уровня матча не регистрируются, а
описание get_player прямо говорит, почему матчей в ответе нет.
"""

import functools
import re
from importlib.metadata import version

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from rttf_mcp.client import RttfClient
from rttf_mcp.errors import InvalidInput, RttfError
from rttf_mcp.parsers.halls import parse_hall
from rttf_mcp.parsers.lists import parse_players_list, parse_tournaments_list
from rttf_mcp.parsers.player import parse_player
from rttf_mcp.parsers.tournament import parse_tournament
from rttf_mcp.policy import FLAG, restricted_allowed, tools_to_register

_client = RttfClient()

# fullmatch, а не match с "$": в Python "$" совпадает и перед завершающим
# переводом строки, так что "28415\n" прошёл бы проверку и ушёл в запрос.
_ID = re.compile(r"[1-9][0-9]{0,8}")
_CITY = re.compile(r"r[0-9]{1,3}|[1-9][0-9]{0,5}")
MIN_RATING_WINDOW = 1
MAX_RATING_WINDOW = 4000
TOURNAMENT_TYPES = {
    "s": "одиночные",
    "d": "парные",
    "t": "командные",
    "p": "пинг-понг",
    "r": "ракетлон",
    "w": "шах-понг",
    "v": "ветеранские",
    "c": "детские",
    "l": "лесенки",
    "h": "форовые",
    "m": "мини-теннис",
    "t2": "триатлон 2",
    "t3": "триатлон 3",
    "t4": "триатлон 4",
    "d12": "дуатлон 12",
}
MATCH_CATEGORIES = ("s", "d")


def _reporting(fn):
    """Доносит ошибки проекта до модели.

    SDK превращает всё, кроме ToolError, в безликое "Error executing tool
    <name>", и тогда ParseError теряет имя селектора, а NotFound становится
    неотличим от падения сайта. Перевод делаем здесь: errors.py и parsers/
    остаются свободны от зависимости на MCP.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except RttfError as exc:
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    return wrapper


def _valid_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InvalidInput(f"{field} должен быть числом из адреса сайта, например 28415; получено {value!r}")
    return value


def _valid_city(value: str) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if not _CITY.fullmatch(cleaned):
        raise InvalidInput(
            f"city — код региона вида r77 или числовой код города вида 2; получено {value!r}"
        )
    return cleaned


def _valid_max_rating(value: int | None) -> int | None:
    if value is None:
        return None
    # bool — подкласс int: True прошёл бы как рейтинг 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInput(f"max_rating должен быть целым числом; получено {value!r}")
    if not MIN_RATING_WINDOW <= value <= MAX_RATING_WINDOW:
        # rat_to=0 и отрицательные сайт молча игнорирует и отдаёт вершину
        # рейтинга: курсор 0 вернул бы модель в начало списка. Замерено.
        raise InvalidInput(
            f"max_rating должен быть от {MIN_RATING_WINDOW} до {MAX_RATING_WINDOW}; получено {value}"
        )
    return value


def _fetch_player(player_id: str, *, include_matches: bool) -> dict:
    player_id = _valid_id(player_id, "player_id")
    html = _client.get_html(f"/players/{player_id}")
    return parse_player(html, player_id, include_matches=include_matches)


@_reporting
def get_player(player_id: str) -> dict:
    """Профиль игрока rttf.ru: рейтинги по категориям, история рейтинга,
    сводка и последние турниры с местом и балансом. Один запрос к сайту.

    player_id — число из адреса профиля на сайте (rttf.ru/players/28415).
    Поиска по имени нет ни в одном режиме: сайт закрыл его в robots.txt.
    Выйти на игрока можно через list_players или через итоговую таблицу
    турнира (get_tournament).

    categories — словарь по коду категории; ключ есть, только если у игрока
    есть такая вкладка на сайте. Отсутствие ключа означает «категории у
    игрока нет», а не сбой. Замеренные коды:
    - "s" — одиночный разряд, основной рейтинг RTTF;
    - "d" — парный разряд. Своего рейтинга у пар на сайте нет:
      rating_current, rating_max и rating_max_date равны null, а
      rating_history пуст. В турнирах пары стоят одиночные рейтинги
      партнёров на момент турнира и их сумма (pair_rating_at_event);
    - "f" — рейтинг ФНТР (федерации), внешний к RTTF. Для него есть только
      текущее значение, максимум и история; stats, recent_tournaments и
      recent_tournaments_truncated равны null.
    Прочие коды (h, p, m, w, r) в замерах не встретились; такая категория
    придёт под своим кодом с подписью сайта в label. Отдельной категории
    для нерабочей руки на сайте не найдено; playing_hand — это игровая рука
    из анкеты.

    Рейтинги — целые, и это округление внутреннего дробного значения, а не
    усечение (проверено на различающем случае). Рейтинг бывает
    отрицательным: у новичка после неудачного турнира -18, а перед
    следующим турниром сайт сбрасывает его до 1.

    rating_history — полный график рейтинга по датам турниров (у ФНТР — по
    месяцам). Первая точка может иметь date null: это стартовый рейтинг.

    stats — сводка категории; поля, которых на сайте нет (rank_position у
    пар), равны null.

    recent_tournaments — НЕ ВСЕ турниры, а не больше десяти последних по
    категории: полный список сайт отдаёт только через закрытый /results.
    recent_tournaments_truncated равно true, когда stats.tournaments_played
    больше длины списка. Для каждого турнира: rating_before_event и
    rating_after_event — рейтинг игрока до и после этого турнира,
    rating_delta — изменение (пустая ячейка сайта — это 0.0), bonus —
    бонус за призовое место или null, place — место или null, если сайт
    его не указал (лиги), wins/losses — баланс встреч в турнире.
    """
    return _fetch_player(player_id, include_matches=False)


_GET_PLAYER_OFF = f"""

    ДАННЫЕ УРОВНЯ МАТЧА В ЭТОМ РЕЖИМЕ НЕ ВЫДАЮТСЯ: соперники, счёт,
    длительность и дельта отдельных встреч, лучшие победы, очные встречи.
    Это не поломка и не отсутствие данных на сайте, а сознательное
    ограничение: rttf.ru закрыл эти разделы в robots.txt, и сервер
    запущен без {FLAG}. Не делайте вывода, что у игрока нет матчей, —
    турниры с местом и балансом побед в ответе есть.
    """

_GET_PLAYER_ON = """

    Матчи последних турниров и лучшие победы отдаёт get_player_matches,
    встречи с конкретным соперником — get_head_to_head.
    """


@_reporting
def get_player_matches(player_id: str, category: str = "s") -> dict:
    """Матчи последних турниров игрока и его лучшие победы. Один запрос.

    Инструмент закрытой категории: сайт запрещает раздел матчей в
    robots.txt, и сервер показывает его только при поднятом
    RTTF_MCP_ALLOW_RESTRICTED. Данные берутся с разрешённой страницы
    профиля, закрытые адреса не запрашиваются.

    category — "s" (одиночный разряд, по умолчанию) или "d" (пары).

    tournaments — не больше десяти последних турниров категории, каждый с
    полями как в get_player и списком matches; более ранних матчей через
    разрешённые адреса не достать. tournaments_truncated равно true, когда
    турниров у игрока больше.

    Одиночный матч: opponent_id, opponent_rating_at_event — рейтинг
    соперника ДО этого турнира (проверено по итоговой таблице турнира; это
    не сегодняшний рейтинг), score_for/score_against, result "win" |
    "loss" | "unparsed" (неопознанный счёт — признак изменившейся вёрстки,
    исходная запись в score_raw), rating_delta — изменение рейтинга за матч
    (пустая ячейка сайта — 0.0), duration_minutes — длительность или null,
    start_time и stage — время начала и стадия («гр. 1», «1/8», «фин»,
    «кл. 4» и т. п., свободный текст сайта) или null.

    Парный матч: opponents — два соперника с одиночными рейтингами на
    момент турнира, opponents_pair_rating_at_event — их сумма. Дельты за
    парный матч сайт не публикует, поля rating_delta нет.

    best_wins — десять лучших побед за всё время (только для "s", для "d"
    — пустой список): это единственный путь к матчам старше последних
    десяти турниров. player_rating_at_event и opponent_rating_at_event —
    рейтинги на момент той встречи.
    """
    if category not in MATCH_CATEGORIES:
        raise InvalidInput(f"category должна быть одной из {list(MATCH_CATEGORIES)}; получено {category!r}")
    profile = _fetch_player(player_id, include_matches=True)
    section = profile["categories"].get(category)
    return {
        "player_id": profile["player_id"],
        "name": profile["name"],
        "category": category,
        "tournaments": section["recent_tournaments"] if section else [],
        "tournaments_truncated": section["recent_tournaments_truncated"] if section else False,
        "best_wins": profile["best_wins"] if category == "s" else [],
    }


@_reporting
def get_head_to_head(player_id: str, opponent_id: str) -> dict:
    """Встречи игрока с конкретным соперником. Один запрос — профиль игрока.

    Инструмент закрытой категории (очные встречи, /rivals в robots.txt);
    виден только при поднятом RTTF_MCP_ALLOW_RESTRICTED. Раздел «соперники»
    сайта не запрашивается: встречи отбираются из разрешённого профиля.

    ОХВАТ НЕПОЛНЫЙ, и это ограничение источника, а не отсутствие встреч:
    просматриваются только последние турниры игрока (не больше десяти по
    каждой категории) и десять его лучших побед. Пустые списки означают
    «в этих турнирах не встречались», а не «не встречались никогда».
    coverage показывает, сколько турниров просмотрено и есть ли более
    ранние.

    singles — одиночные матчи, doubles — парные, где соперник был в
    противоположной паре; поля матчей как в get_player_matches, плюс
    tournament_id, date и hall_name турнира. best_wins — победы над
    соперником из лучших побед, не вошедшие в singles.
    """
    player_id = _valid_id(player_id, "player_id")
    opponent_id = _valid_id(opponent_id, "opponent_id")
    if player_id == opponent_id:
        raise InvalidInput("player_id и opponent_id совпадают")
    profile = _fetch_player(player_id, include_matches=True)
    context_keys = ("tournament_id", "date", "hall_name")
    found: dict[str, list] = {"s": [], "d": []}
    coverage = {}
    for code in MATCH_CATEGORIES:
        section = profile["categories"].get(code)
        tournaments = section["recent_tournaments"] if section else []
        coverage[code] = {
            "tournaments_checked": len(tournaments),
            "truncated": bool(section and section["recent_tournaments_truncated"]),
        }
        for entry in tournaments:
            for match in entry["matches"]:
                if code == "s":
                    hit = match["opponent_id"] == opponent_id
                else:
                    hit = any(p["player_id"] == opponent_id for p in match["opponents"])
                if hit:
                    found[code].append({key: entry[key] for key in context_keys} | match)
    seen = {m["tournament_id"] for m in found["s"]}
    best = [
        win
        for win in profile["best_wins"]
        if win["opponent_id"] == opponent_id and win["tournament_id"] not in seen
    ]
    return {
        "player_id": player_id,
        "opponent_id": opponent_id,
        "singles": found["s"],
        "doubles": found["d"],
        "best_wins": best,
        "coverage": {"singles": coverage["s"], "doubles": coverage["d"], "best_wins_checked": len(profile["best_wins"])},
    }


@_reporting
def get_tournament(tournament_id: str) -> dict:
    """Турнир rttf.ru: метаданные и итоговая таблица. Один запрос.

    tournament_id — число из адреса турнира (rttf.ru/tournaments/243702).

    Матчей турнира в ответе нет ни в каком режиме: на странице турнира их
    нет вовсе (этапы выложены картинками), а список игр лежит в закрытом
    /games.

    limit — ограничение по рейтингу как на сайте: число, "отк" (открытый)
    или "дети". doubles — парный ли турнир. has_results — сыгран ли
    турнир; у несыгранного standings пуст, а participants, games,
    average_rating и coefficient равны null.

    registered / registration_limit — сколько заявилось и лимит заявок
    (null, если лимита нет). У парного турнира сайт считает заявки
    парами.

    Одиночная таблица: place, player_id, name (как на сайте, с логином в
    скобках), city, rating_before_event, rating_delta, rating_after_event —
    рейтинги до и после ЭТОГО турнира, а не сегодняшние; wins/losses —
    встречи, sets_won/sets_lost — партии.

    Парная таблица: place, pair — два игрока с одиночными рейтингами на
    момент турнира, pair_rating_at_event — их сумма, wins/losses,
    sets_won/sets_lost. Дельты и рейтинга после у пар сайт не публикует.

    average_rating — средний рейтинг участников с двумя знаками: сайт
    считает его по внутренним дробным рейтингам, поэтому среднее целых из
    таблицы с ним точно не совпадёт. coefficient — коэффициент турнира
    (у парных null).
    """
    tournament_id = _valid_id(tournament_id, "tournament_id")
    html = _client.get_html(f"/tournaments/{tournament_id}")
    return parse_tournament(html, tournament_id)


@_reporting
def list_players(city: str = "", max_rating: int | None = None) -> dict:
    """Листает рейтинг игроков rttf.ru окнами по сто строк. Один запрос.

    Это единственный разрешённый способ выйти на игрока без поиска по
    имени (поиск сайт закрыл в robots.txt). Строки идут по убыванию
    рейтинга.

    city — код региона ("r77" — Москва и МО, "r78" — Санкт-Петербург и ЛО)
    или числовой код города ("1" — Москва, "2" — Санкт-Петербург); коды
    видны в ссылках городов на сайте и в поле city.city_id ответа. Пусто —
    вся страна.

    max_rating — верхняя граница окна (от 1 до 4000). Первый вызов — без
    неё; следующий — с max_rating из next_max_rating ответа. Соседние окна
    ПЕРЕКРЫВАЮТСЯ: сайт сравнивает границу с внутренним дробным рейтингом,
    а показывает округлённый, и без перекрытия часть игроков выпадала бы.
    Повторы между окнами отбрасывайте по player_id.

    next_max_rating равно null, когда список кончился (строк меньше ста)
    или когда stalled_on_tie равно true: сто строк окна имеют один и тот же
    рейтинг, и дальше окнами не пройти — остальные игроки с этим рейтингом
    через разрешённые адреса недоступны. Игроки с рейтингом ниже 1 тоже
    недоступны: такое окно сайт молча игнорирует.

    rank — место в ОБЩЕМ рейтинге, даже при фильтре по городу. is_team —
    корпоративная команда, а не человек: сайт ставит их в общий рейтинг
    наравне с игроками. city равно null, если город не указан. updated —
    дата последнего изменения рейтинга.
    """
    params: dict[str, str] = {}
    city_code = _valid_city(city)
    window = _valid_max_rating(max_rating)
    if city_code is not None:
        params["cities[]"] = city_code
    if window is not None:
        params["rat_to"] = str(window)
    html = _client.get_html("/players/", params)
    return {"city": city_code, "max_rating": window} | parse_players_list(html, window)


@_reporting
def search_tournaments(city: str = "", hall_id: str = "", tournament_type: str = "") -> dict:
    """Ближайшие и недавние турниры rttf.ru по фильтрам. Один запрос.

    Фильтра по датам и по названию нет: сайт закрыл оба в robots.txt.
    Доступны: city (как в list_players), hall_id (число из адреса зала,
    rttf.ru/halls/1073) и tournament_type — один из кодов сайта: s
    одиночные, d парные, t командные, p пинг-понг, r ракетлон, w шах-понг,
    v ветеранские, c детские, l лесенки, h форовые, m мини-теннис, t2/t3/t4
    триатлон, d12 дуатлон.

    upcoming — ближайшие турниры, past — прошедшие, от свежих к старым; в
    каждом списке не больше ста строк, листания нет (подгрузка на сайте
    идёт через закрытый адрес). Без фильтров сто прошедших покрывают
    примерно полтора дня; с фильтром по залу — несколько недель.
    *_total — счётчик «итого» сайта, если он показан (в коротких
    отфильтрованных списках), иначе null. *_truncated равно true, когда
    строк на сайте больше, чем в ответе.

    Строка: tournament_id, date, start_time, limit, hall_name (краткое имя
    зала), doubles, ladder (лесенка), live (идёт сейчас), registration_open,
    average_rating (null, если сайт не посчитал), registered /
    registration_limit, games (сыграно встреч или null).
    """
    params: dict[str, str] = {}
    city_code = _valid_city(city)
    if city_code is not None:
        params["cities[]"] = city_code
    if hall_id:
        params["halls[]"] = _valid_id(hall_id, "hall_id")
    if tournament_type:
        if tournament_type not in TOURNAMENT_TYPES:
            raise InvalidInput(
                f"tournament_type должен быть одним из {sorted(TOURNAMENT_TYPES)}; получено {tournament_type!r}"
            )
        params["types[]"] = tournament_type
    html = _client.get_html("/tournaments/", params)
    return {"filters": params} | parse_tournaments_list(html)


@_reporting
def get_hall(hall_id: str) -> dict:
    """Зал (клуб) rttf.ru. Один запрос.

    hall_id — число из адреса зала (rttf.ru/halls/1073); оно же приходит в
    get_tournament (hall.hall_id) и в сводке игрока
    (stats.most_frequent_hall.hall_id).

    Поля: name, address, metro (список станций или null), opening_hours,
    tables — число столов, website, amenities и paid_amenities — удобства,
    бесплатные и платные, rating — оценка посетителей (overall и по
    аспектам) или null, representative_player_id — профиль представителя
    зала на сайте. Любое поле может быть null, если зал его не заполнил.

    Контакты зала не отдаются намеренно: там личные телефоны сотрудников.
    Турниры зала — search_tournaments(hall_id=...).
    """
    hall_id = _valid_id(hall_id, "hall_id")
    html = _client.get_html(f"/halls/{hall_id}")
    return parse_hall(html, hall_id)


TOOLS = {
    "get_player": get_player,
    "get_player_matches": get_player_matches,
    "get_head_to_head": get_head_to_head,
    "get_tournament": get_tournament,
    "list_players": list_players,
    "search_tournaments": search_tournaments,
    "get_hall": get_hall,
}


def build_server(allow_restricted: bool) -> MCPServer:
    # Версия берётся из метаданных пакета, а не дублируется строкой: иначе
    # она разъедется с pyproject, и клиент покажет неверную.
    server = MCPServer("rttf", version=version("rttf-mcp"))
    for name in tools_to_register(TOOLS, allow_restricted):
        description = None
        if name == "get_player":
            note = _GET_PLAYER_ON if allow_restricted else _GET_PLAYER_OFF
            description = (get_player.__doc__ or "").rstrip() + note
        server.add_tool(TOOLS[name], name=name, description=description)
    return server


mcp = build_server(restricted_allowed())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
