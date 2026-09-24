import pytest

from rttf_mcp.errors import ParseError
from rttf_mcp.parsers.common import soup_of
from rttf_mcp.parsers.lists import PAGE_SIZE, parse_players_list, parse_tournaments_list


def test_top_of_the_rating(load_fixture):
    result = parse_players_list(load_fixture("players_top.html"), None)
    players = result["players"]
    assert len(players) == PAGE_SIZE == 100
    assert [p["rank"] for p in players[:3]] == [1, 2, 3]
    assert players[0]["rating"] == 1934
    assert players[-1]["rating"] == 1080
    assert players[0]["updated"] == "2026-09-23"


def test_corporate_teams_are_flagged_not_dropped(load_fixture):
    players = parse_players_list(load_fixture("players_top.html"), None)["players"]
    assert [p["rank"] for p in players if p["is_team"]] == [1, 2, 4, 22, 47]


def test_city_link_and_empty_city(load_fixture):
    players = parse_players_list(load_fixture("players_top.html"), None)["players"]
    assert players[0]["city"] == {"city_id": "1", "name": "Москва"}
    # «cities[]=0» с пустым текстом — город не указан: null, а не пустая строка.
    assert sum(p["city"] is None for p in players) == 29


def test_next_window_overlaps_instead_of_skipping(load_fixture):
    # Нижняя строка вершины — 1080. Следующее окно — rat_to=1081, а не 1080
    # и не 1079: сайт сравнивает с внутренним дробным рейтингом, и при
    # rat_to=990 из выдачи выпали ранги 200–201 с показанным 990.
    result = parse_players_list(load_fixture("players_top.html"), None)
    assert result["next_max_rating"] == 1081
    assert result["stalled_on_tie"] is False


def test_rating_window(load_fixture):
    result = parse_players_list(load_fixture("players_window_990.html"), 990)
    players = result["players"]
    assert players[0]["rank"] == 202  # ранги 200–201 окно пропустило
    assert players[0]["rating"] == 990
    assert players[-1]["rating"] == 940
    assert result["next_max_rating"] == 941


def test_window_of_equal_ratings_is_reported_as_stalled(load_fixture):
    # Сто строк с рейтингом 1: следующее окно совпало бы с текущим. Честный
    # ответ — «дальше не пройти», а не курсор, крутящийся на месте.
    result = parse_players_list(load_fixture("players_tie_1.html"), 1)
    assert {p["rating"] for p in result["players"]} == {1}
    assert result["next_max_rating"] is None
    assert result["stalled_on_tie"] is True


def test_city_filter_keeps_global_ranks(load_fixture):
    players = parse_players_list(load_fixture("players_city_2.html"), None)["players"]
    assert players[0]["rank"] == 5
    assert players[0]["player_id"] == "70524"
    assert all(p["city"]["city_id"] == "2" for p in players)


def test_short_page_has_no_next_window(load_fixture):
    soup = soup_of(load_fixture("players_top.html"))
    for row in soup.select("section.players-list > table tr")[51:]:
        row.decompose()
    result = parse_players_list(str(soup), None)
    assert len(result["players"]) == 50
    assert result["next_max_rating"] is None
    assert result["stalled_on_tie"] is False


def test_players_list_garbage_fails_loudly():
    with pytest.raises(ParseError):
        parse_players_list("<html><body></body></html>", None)


def test_tournaments_list(load_fixture):
    result = parse_tournaments_list(load_fixture("tournaments.html"))
    assert len(result["upcoming"]) == 100
    assert len(result["past"]) == 100
    assert result["upcoming_total"] is None
    assert result["upcoming_truncated"] is True
    assert result["past_truncated"] is True
    first = result["upcoming"][0]
    assert first == {
        "tournament_id": "244964",
        "date": "2026-09-24",
        "start_time": "17:20",
        "limit": "отк",
        "hall_name": "ФНТ Алматы",
        "doubles": False,
        "ladder": False,
        "live": False,
        "registration_open": True,
        "average_rating": 383,
        "registered": 5,
        "registration_limit": 32,
        "games": None,
    }


def test_live_tournament_row(load_fixture):
    past = parse_tournaments_list(load_fixture("tournaments.html"))["past"]
    live = past[0]
    assert live["tournament_id"] == "244633"
    assert live["live"] is True
    assert (live["registered"], live["registration_limit"]) == (12, None)
    assert live["games"] == 12
    assert sum(t["live"] for t in past) == 8
    assert sum(t["ladder"] for t in past) == 10
    assert sum(t["doubles"] for t in past) == 2


def test_hall_archive_reports_its_total(load_fixture):
    # В отфильтрованном списке сайт пишет «итого: N», если список короче
    # потолка. Это единственный честный счётчик списков турниров.
    result = parse_tournaments_list(load_fixture("tournaments_hall_1073.html"))
    assert result["upcoming_total"] == 5
    assert len(result["upcoming"]) == 5
    assert result["upcoming_truncated"] is False
    assert all(t["hall_name"] == "NEO TTA" for t in result["upcoming"] + result["past"])
    assert result["past"][-1]["date"] == "2026-08-02"
    assert result["past_truncated"] is True


def test_unknown_row_shape_fails_loudly(load_fixture):
    soup = soup_of(load_fixture("tournaments.html"))
    table = soup.select_one("section.tours-list2 > table")
    table.append(soup_of("<tr><td>что-то новое</td></tr>").tr)
    with pytest.raises(ParseError):
        parse_tournaments_list(str(soup))


def test_cursor_never_goes_below_the_window_floor(load_fixture):
    # Полное окно, в котором встретился рейтинг ниже 1 (новичок после
    # неудачного турнира, фильтр по городу): курсор ≤ 0 инструмент сам же
    # отверг бы, а сайт на rat_to ≤ 0 отдаёт вершину. Честный ответ — конец.
    soup = soup_of(load_fixture("players_window_990.html"))
    soup.select("section.players-list > table tr dfn")[-1].string = "-3"
    result = parse_players_list(str(soup), 990)
    assert result["next_max_rating"] is None
    assert result["stalled_on_tie"] is False
