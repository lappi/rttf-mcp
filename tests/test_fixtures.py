import pytest

CASES = [
    ("player_28415.html", 'data-tab="rat_d"'),
    ("player_226194.html", "<dfn>-1</dfn>"),
    ("player_70524.html", 'class="gift"'),
    ("player_999999999.html", "page404"),
    ("tournament_243702.html", "tour-players"),
    ("tournament_244471.html", "tour-doubles"),
    ("tournament_244791.html", "tour-reg-list1"),
    ("players_top.html", "players-list"),
    ("players_window_990.html", "players-list"),
    ("players_tie_1.html", "players-list"),
    ("players_city_2.html", "players-list"),
    ("tournaments.html", "tours-list2"),
    ("tournaments_hall_1073.html", "итого:"),
    ("hall_1073.html", "hall-rating"),
]


@pytest.mark.parametrize("name,anchor", CASES)
def test_fixture_loads_and_contains_anchor(load_fixture, name, anchor):
    html = load_fixture(name)
    assert len(html) > 1000
    assert anchor in html
