import asyncio
import json
from importlib.metadata import version

import httpx
import pytest
from mcp.server.mcpserver.exceptions import UnexpectedToolError

from rttf_mcp import server
from rttf_mcp.client import RttfClient
from rttf_mcp.errors import InvalidInput
from rttf_mcp.policy import FLAG, RESTRICTED_TOOLS

ALWAYS = {"get_player", "get_tournament", "list_players", "search_tournaments", "get_hall"}

# Независимый от policy.py список: канарейка не должна проверять код его же
# собственным выражением, иначе ошибка в policy прошла бы и здесь.
CANARY_PREFIXES = ("/games", "/results", "/rivals", "/search", "/ratings", "/comments", "/names")
CANARY_QUERY = ("name=", "date_from=", "date_to=", "sort=", "order=", "title=", "metro", "ajax")

ROUTES = {
    "/players/28415": "player_28415.html",
    "/players/": "players_top.html",
    "/tournaments/243702": "tournament_243702.html",
    "/tournaments/": "tournaments.html",
    "/halls/1073": "hall_1073.html",
}

CALLS = {
    "get_player": {"player_id": "28415"},
    "get_player_matches": {"player_id": "28415"},
    "get_head_to_head": {"player_id": "28415", "opponent_id": "199984"},
    "get_tournament": {"tournament_id": "243702"},
    "list_players": {"city": "r77", "max_rating": 990},
    "search_tournaments": {"city": "2", "hall_id": "1073", "tournament_type": "d"},
    "get_hall": {"hall_id": "1073"},
}


class StubClient:
    def __init__(self, html: str = "") -> None:
        self.html = html
        self.calls: list[tuple] = []

    def get_html(self, path, params=None):
        self.calls.append((path, params or {}))
        return self.html


@pytest.fixture
def stub(monkeypatch):
    def install(html: str = "") -> StubClient:
        client = StubClient(html)
        monkeypatch.setattr(server, "_client", client)
        return client

    return install


@pytest.fixture
def canary(monkeypatch, load_fixture):
    """Настоящий RttfClient на подменённом транспорте: запросы видны целиком."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        path, query = request.url.path, request.url.query.decode()
        if path.startswith(CANARY_PREFIXES) or any(k in query for k in CANARY_QUERY):
            raise AssertionError(f"закрытый адрес запрошен: {url}")
        return httpx.Response(200, text=load_fixture(ROUTES[path]))

    monkeypatch.setattr(server, "_client", RttfClient(transport=httpx.MockTransport(handler), min_interval=0.0))
    return seen


def listed(allow_restricted: bool) -> dict[str, str]:
    tools = asyncio.run(server.build_server(allow_restricted).list_tools())
    return {tool.name: tool.description or "" for tool in tools}


def call(mcp, name: str, args: dict):
    return asyncio.run(mcp._tool_manager.call_tool(name, args, None))


def test_flag_off_hides_restricted_tools():
    assert set(listed(False)) == ALWAYS


def test_flag_on_shows_them():
    assert set(listed(True)) == ALWAYS | set(RESTRICTED_TOOLS)


def test_name_search_exists_in_neither_mode():
    assert "search_players" not in listed(False)
    assert "search_players" not in listed(True)


def test_get_player_says_why_matches_are_missing_when_off():
    # Без этой фразы модель решит, что у игрока просто нет матчей.
    description = listed(False)["get_player"]
    assert FLAG in description
    assert "не поломка" in description
    assert "get_player_matches" not in description


def test_get_player_points_to_the_match_tools_when_on():
    description = listed(True)["get_player"]
    assert "get_player_matches" in description
    assert "не поломка" not in description


def test_server_version_matches_package():
    assert server.build_server(False)._lowlevel_server.version == version("rttf-mcp")


@pytest.mark.parametrize("allow_restricted", [False, True])
def test_canary_no_closed_address_is_requested(canary, allow_restricted):
    # Каждый видимый инструмент вызывается через слой MCP; транспорт роняет
    # тест на любом закрытом адресе. В обоих режимах.
    mcp = server.build_server(allow_restricted)
    for name in listed(allow_restricted):
        call(mcp, name, CALLS[name])
    assert canary, "ни одного запроса — канарейка ничего не проверила"


def test_flag_off_output_has_no_match_level_data(canary):
    # Флаг решает не только какие адреса, но и какие категории мы извлекаем
    # с разрешённой страницы. Выдача режима «выключено» не содержит ни одного
    # ключа уровня матча — проверяем по сериализованному ответу целиком.
    mcp = server.build_server(False)
    for name in listed(False):
        text = json.dumps(call(mcp, name, CALLS[name]), ensure_ascii=False)
        for key in ("matches", "best_wins", "opponent_id", "opponents", "score_for", "duration_minutes"):
            assert f'"{key}"' not in text, (name, key)


def test_get_player_requests_the_profile_path(stub, load_fixture):
    client = stub(load_fixture("player_28415.html"))
    result = server.get_player("28415")
    assert client.calls == [("/players/28415", {})]
    assert result["categories"]["s"]["rating_current"] == 1188


def test_get_player_matches_returns_the_category(stub, load_fixture):
    stub(load_fixture("player_28415.html"))
    singles = server.get_player_matches("28415")
    assert singles["category"] == "s"
    assert len(singles["tournaments"]) == 10
    assert singles["tournaments_truncated"] is True
    assert len(singles["best_wins"]) == 10
    doubles = server.get_player_matches("28415", category="d")
    assert doubles["tournaments"][0]["tournament_id"] == "239285"
    assert doubles["best_wins"] == []


def test_head_to_head_finds_singles_meetings(stub, load_fixture):
    stub(load_fixture("player_28415.html"))
    result = server.get_head_to_head("28415", "199984")
    assert len(result["singles"]) >= 1
    final = result["singles"][0]
    assert final["tournament_id"] == "243702"
    assert final["date"] == "2026-09-20"
    assert final["result"] == "loss"
    assert result["coverage"]["singles"] == {"tournaments_checked": 10, "truncated": True}
    assert result["coverage"]["best_wins_checked"] == 10


def test_head_to_head_finds_doubles_meetings(stub, load_fixture):
    stub(load_fixture("player_28415.html"))
    result = server.get_head_to_head("28415", "141107")
    assert result["singles"] == []
    assert len(result["doubles"]) == 1
    assert result["doubles"][0]["tournament_id"] == "239285"


def test_head_to_head_uses_best_wins_beyond_recent_tournaments(stub, load_fixture):
    # Победа 2020 года над 22833 есть только в лучших победах.
    stub(load_fixture("player_28415.html"))
    result = server.get_head_to_head("28415", "22833")
    assert result["singles"] == []
    assert [w["tournament_id"] for w in result["best_wins"]][:1] == ["46509"]


def test_head_to_head_rejects_the_same_player(stub):
    client = stub()
    with pytest.raises(Exception) as excinfo:
        server.get_head_to_head("28415", "28415")
    assert "совпадают" in str(excinfo.value)
    assert client.calls == []


def test_list_players_sends_city_and_window(stub, load_fixture):
    client = stub(load_fixture("players_window_990.html"))
    result = server.list_players(city="r77", max_rating=990)
    assert client.calls == [("/players/", {"cities[]": "r77", "rat_to": "990"})]
    assert result["max_rating"] == 990
    assert result["next_max_rating"] == 941


def test_list_players_without_arguments_sends_no_filters(stub, load_fixture):
    client = stub(load_fixture("players_top.html"))
    server.list_players()
    assert client.calls == [("/players/", {})]


def test_search_tournaments_sends_allowed_filters_only(stub, load_fixture):
    client = stub(load_fixture("tournaments_hall_1073.html"))
    result = server.search_tournaments(hall_id="1073", tournament_type="s")
    assert client.calls == [("/tournaments/", {"halls[]": "1073", "types[]": "s"})]
    assert result["upcoming_total"] == 5


def test_get_tournament_and_hall_paths(stub, load_fixture):
    client = stub(load_fixture("tournament_243702.html"))
    server.get_tournament("243702")
    client_hall = stub(load_fixture("hall_1073.html"))
    server.get_hall("1073")
    assert client.calls == [("/tournaments/243702", {})]
    assert client_hall.calls == [("/halls/1073", {})]


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        ("get_player", {"player_id": "28415\n"}),
        ("get_player", {"player_id": "0"}),
        ("get_player", {"player_id": "1c18ed8"}),
        ("get_player", {"player_id": ""}),
        ("get_player", {"player_id": "../games/1"}),
        ("get_tournament", {"tournament_id": "243702/"}),
        ("get_hall", {"hall_id": "-1"}),
        ("list_players", {"max_rating": 0}),
        ("list_players", {"max_rating": -5}),
        ("list_players", {"max_rating": 4001}),
        ("list_players", {"max_rating": True}),
        ("list_players", {"city": "Москва"}),
        ("list_players", {"city": "r77&name=x"}),
        ("search_tournaments", {"tournament_type": "x"}),
        ("search_tournaments", {"hall_id": "abc"}),
        ("get_player_matches", {"player_id": "28415", "category": "f"}),
        ("get_head_to_head", {"player_id": "28415", "opponent_id": "x"}),
    ],
)
def test_invalid_input_is_refused_before_the_network(stub, tool, kwargs):
    client = stub()
    with pytest.raises(Exception) as excinfo:
        getattr(server, tool)(**kwargs)
    assert isinstance(excinfo.value.__cause__, InvalidInput)
    assert client.calls == []


def test_errors_reach_the_model_through_mcp(stub):
    # Прямой вызов функции этот путь не проверяет: стирание происходит в
    # SDK, между инструментом и моделью.
    stub()
    manager = server.build_server(False)._tool_manager

    async def run():
        try:
            await manager.call_tool("get_player", {"player_id": "zz"}, None)
        except Exception as exc:  # noqa: BLE001 — нас интересует сам объект
            return exc
        return None

    error = asyncio.run(run())
    assert not isinstance(error, UnexpectedToolError)
    assert "InvalidInput" in str(error)
    assert "player_id" in str(error)


def test_parse_error_names_the_selector_for_the_model(stub):
    stub("<html><body>ничего похожего</body></html>")
    with pytest.raises(Exception) as excinfo:
        server.get_player("28415")
    assert "ParseError" in str(excinfo.value)
    assert "section.player-info" in str(excinfo.value)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("list_players", {"max_rating": True}),
        ("list_players", {"max_rating": "990"}),
        ("list_players", {"max_rating": 990.0}),
        ("list_players", {"max_rating": 0}),
        ("list_players", {"city": "r77&name=x"}),
        ("get_player", {"player_id": "28415\n"}),
    ],
)
def test_invalid_input_is_refused_through_the_mcp_layer(stub, tool, args):
    # Прямой вызов функции минует pydantic, который в нестрогом режиме
    # превращал True в 1 и "990" в 990 раньше нашей проверки. Модель
    # вызывает инструменты только так — через SDK.
    client = stub()
    manager = server.build_server(True)._tool_manager

    async def run():
        try:
            await manager.call_tool(tool, args, None)
        except Exception as exc:  # noqa: BLE001 — нас интересует сам объект
            return exc
        return None

    error = asyncio.run(run())
    assert error is not None
    assert client.calls == []


def test_missing_category_is_null_not_empty(stub, load_fixture):
    # Пар у игрока 226194 нет вовсе. Пустой список значил бы «турниров в
    # парах ноль» — это другое утверждение.
    stub(load_fixture("player_226194.html"))
    result = server.get_player_matches("226194", category="d")
    assert result["tournaments"] is None
    assert result["tournaments_truncated"] is None


DOC_CASES = [
    ("get_player", "player_28415.html", {"player_id": "28415"}),
    ("get_player_matches", "player_28415.html", {"player_id": "28415"}),
    ("get_head_to_head", "player_28415.html", {"player_id": "28415", "opponent_id": "199984"}),
    ("get_tournament", "tournament_244471.html", {"tournament_id": "244471"}),
    ("get_tournament", "tournament_243702.html", {"tournament_id": "243702"}),
    ("list_players", "players_top.html", {}),
    ("search_tournaments", "tournaments_hall_1073.html", {"hall_id": "1073"}),
    ("get_hall", "hall_1073.html", {"hall_id": "1073"}),
]


def _keys(value, depth=0):
    """Ключи ответа на двух верхних уровнях, включая первые элементы списков."""
    found = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            found.add(key)
            # Ключи aspects — подписи сайта («Расположение»), а не схема ответа.
            if depth < 2 and key != "aspects":
                found |= _keys(inner, depth + 1)
    elif isinstance(value, list) and value and depth < 3:
        found |= _keys(value[0], depth)
    return found


@pytest.mark.parametrize("tool,fixture,args", DOC_CASES)
def test_every_output_key_is_named_in_the_description(stub, load_fixture, tool, fixture, args):
    # Докстринг — единственный канал к модели: поле, о котором он молчит,
    # модель истолкует по имени, а имена здесь уже раз соврали.
    stub(load_fixture(fixture))
    result = getattr(server, tool)(**args)
    description = listed(True)[tool]
    missing = sorted(key for key in _keys(result) if key not in description)
    assert missing == []
