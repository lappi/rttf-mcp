"""Тесты policy.py: флаг, robots.txt, выбор инструментов. Без сети."""

import pytest

from rttf_mcp.policy import (
    FLAG,
    RESTRICTED_TOOLS,
    ROBOTS_DISALLOW,
    FlagError,
    blocking_rule,
    restricted_allowed,
    tools_to_register,
)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_flag_on_values(value):
    assert restricted_allowed({FLAG: value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "False", "no", "off", "  "])
def test_flag_off_values(value):
    # «0» и «false» — самые вероятные способы явно выключить флаг. Трактовать
    # их как непустую строку, то есть как «включено», значит открыть
    # закрытые разделы вопреки явной настройке.
    assert restricted_allowed({FLAG: value}) is False


def test_flag_absent_is_off():
    assert restricted_allowed({}) is False


@pytest.mark.parametrize("value", ["maybe", "ture", "2", "enabled"])
def test_unrecognised_flag_refuses_to_start(value):
    with pytest.raises(FlagError) as excinfo:
        restricted_allowed({FLAG: value})
    assert FLAG in str(excinfo.value)


@pytest.mark.parametrize(
    "path,query,rule",
    [
        ("/games", "", "/games"),
        ("/gamesd/", "tourID=244471", "/games"),
        ("/games/", "user1ID=28415&user2ID=167955&type=s", "/games"),
        ("/results/28415", "", "/results"),
        ("/rivals/28415", "", "/rivals"),
        ("/ratings", "", "/ratings"),
        ("/search", "q=x", "/search"),
        ("/", "ajax", "/?ajax*"),
        ("/players/", "name=%D0%B8%D0%B2", "/*?*name=*"),
        ("/players/", "type=s&name=x", "/*?*name=*"),
        ("/players/", "sort=rating", "/*?*sort=*"),
        ("/tournaments/", "date_from=01.09.2026", "/*?*date_from=*"),
        ("/tournaments/", "title=x", "/*?*title=*"),
        ("/halls/", "isTours=1", "/*?*isTours=*"),
        ("/players/28415/edit", "", "/*/edit"),
        ("/", "fullver=1", "/?fullver="),
    ],
)
def test_blocking_rule_names_the_rule(path, query, rule):
    assert blocking_rule(path, query) == rule


@pytest.mark.parametrize("query", ["metro%5B%5D=386", "metro[]=386", "cities[]=2&metro[]=53"])
def test_array_form_of_a_closed_filter_is_blocked(query):
    # Форма сайта шлёт фильтр метро массивом: metro[]=386, а httpx ещё и
    # кодирует скобки. Буква правила /*?*metro=* не видит ни того, ни
    # другого, но закрыт именно этот фильтр.
    assert blocking_rule("/halls/", query) == "/*?*metro=*"


def test_encoded_name_is_blocked():
    assert blocking_rule("/players/", "type=s&%6Eame=x") == "/*?*name=*"


@pytest.mark.parametrize(
    "path,query",
    [
        ("/players/28415", ""),
        ("/players/", ""),
        ("/players/", "rat_from=0&rat_to=990"),
        ("/players/", "cities%5B%5D=r77&rat_from=0&rat_to=4000"),
        ("/tournaments/243702", ""),
        ("/tournaments/", "halls%5B%5D=1073"),
        ("/tournaments/", "types%5B%5D=d&cities%5B%5D=2"),
        ("/halls/1073", ""),
        ("/statistics/", ""),
    ],
)
def test_addresses_the_server_uses_are_open(path, query):
    assert blocking_rule(path, query) is None


def test_every_rule_blocks_something():
    # Правило, которое ничего не закрывает из-за ошибки компиляции шаблона,
    # молча открыло бы раздел. Для каждого правила строим адрес из него самого.
    for rule in ROBOTS_DISALLOW:
        sample = rule.replace("*", "x")
        path, _, query = sample.partition("?")
        assert blocking_rule(path, query) is not None, rule


def test_restricted_tools_hidden_when_flag_off():
    names = ["get_player", "get_player_matches", "get_head_to_head", "list_players"]
    assert tools_to_register(names, allow_restricted=False) == ["get_player", "list_players"]


def test_restricted_tools_shown_when_flag_on():
    names = ["get_player", "get_player_matches", "get_head_to_head"]
    assert tools_to_register(names, allow_restricted=True) == names


def test_misnamed_restricted_tool_is_an_error_not_a_leak():
    with pytest.raises(ValueError):
        tools_to_register(["get_player", "get_player_match"], allow_restricted=False)


def test_name_search_is_not_a_tool_in_any_mode():
    assert "search_players" not in RESTRICTED_TOOLS
