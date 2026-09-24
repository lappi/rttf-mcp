"""Проверка, что вёрстка живого сайта всё ещё совпадает с фикстурами.

Запускать вручную: uv run pytest -m smoke -v

Проверяются формы, а не числа: живые профили меняются после каждого
турнира. Четыре запроса, последовательно.
"""

import pytest

from rttf_mcp.client import RttfClient
from rttf_mcp.parsers.halls import parse_hall
from rttf_mcp.parsers.lists import parse_players_list, parse_tournaments_list
from rttf_mcp.parsers.player import parse_player

pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module")
def client():
    with RttfClient() as live:
        yield live


def test_live_profile_still_parses_with_matches(client):
    profile = parse_player(client.get_html("/players/28415"), "28415", include_matches=True)
    singles = profile["categories"]["s"]
    assert singles["rating_current"] is not None
    assert singles["recent_tournaments"], "турниров нет — вероятно, изменилась section.player-results"
    assert singles["recent_tournaments"][0]["matches"], "матчей нет — изменились строки таблицы"
    assert profile["best_wins"], "лучших побед нет — изменилась section.player-wins"


def test_live_players_list_still_pages(client):
    result = parse_players_list(client.get_html("/players/", {"rat_to": "990"}), 990)
    assert len(result["players"]) == 100
    assert result["next_max_rating"] is not None


def test_live_tournaments_list_still_parses(client):
    result = parse_tournaments_list(client.get_html("/tournaments/"))
    assert result["upcoming"] and result["past"]


def test_live_hall_still_parses(client):
    hall = parse_hall(client.get_html("/halls/1073"), "1073")
    assert hall["name"]
    assert hall["tables"] is not None
