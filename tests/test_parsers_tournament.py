import pytest

from rttf_mcp.errors import NotFound, ParseError
from rttf_mcp.parsers.common import soup_of
from rttf_mcp.parsers.tournament import parse_tournament


@pytest.fixture
def singles(load_fixture):
    return parse_tournament(load_fixture("tournament_243702.html"), "243702")


@pytest.fixture
def doubles(load_fixture):
    return parse_tournament(load_fixture("tournament_244471.html"), "244471")


@pytest.fixture
def future(load_fixture):
    return parse_tournament(load_fixture("tournament_244791.html"), "244791")


def test_singles_metadata(singles):
    assert singles["tournament_id"] == "243702"
    assert singles["title"] == "Neo Open"
    assert singles["date"] == "2026-09-20"
    assert singles["start_time"] == "16:00"
    assert singles["limit"] == "отк"
    assert singles["doubles"] is False
    assert singles["hall"] == {"hall_id": "1073", "name": "NEO TTA"}
    assert singles["address"].startswith("г. Москва")
    assert (singles["registered"], singles["registration_limit"]) == (31, 48)


def test_singles_summary(singles):
    assert singles["has_results"] is True
    assert singles["participants"] == 31
    assert singles["games"] == 73
    assert singles["average_rating"] == pytest.approx(622.63)
    assert singles["coefficient"] == pytest.approx(0.4)
    assert singles["pairs"] is None


def test_singles_standings(singles):
    rows = singles["standings"]
    assert len(rows) == 31
    first = rows[0]
    assert first["place"] == 1
    assert first["player_id"] == "199984"
    assert first["city"] == "Краснодар"
    assert first["rating_before_event"] == 1137
    assert first["rating_delta"] == pytest.approx(6.8)
    assert first["rating_after_event"] == 1144
    assert (first["wins"], first["losses"]) == (5, 0)
    assert (first["sets_won"], first["sets_lost"]) == (15, 3)
    second = rows[1]
    assert (second["player_id"], second["rating_delta"]) == ("28415", pytest.approx(-5.0))


def test_displayed_ratings_are_rounded_not_truncated(singles):
    # Различающий случай из замеров. Сайт публикует средний рейтинг с двумя
    # знаками, то есть по внутренним дробным значениям, а рейтинги в таблице —
    # целые. Если бы целые были усечением, их сумма была бы меньше
    # внутренней примерно на 0.5 × 31 ≈ 15. Она меньше всего на 0.53 —
    # это округление.
    before = [row["rating_before_event"] for row in singles["standings"]]
    assert sum(before) == 19301
    internal_sum = singles["average_rating"] * len(before)
    assert internal_sum - sum(before) == pytest.approx(0.53, abs=0.02)


def test_doubles_tournament(doubles):
    assert doubles["doubles"] is True
    assert doubles["start_time"] == "20:10"
    assert doubles["limit"] == "577"
    assert (doubles["pairs"], doubles["players"], doubles["games"]) == (15, 30, 49)
    assert doubles["participants"] is None
    assert doubles["average_rating"] == pytest.approx(243.57)
    # Лимита заявок нет: «заявилось 15 чел.» без «/N».
    assert (doubles["registered"], doubles["registration_limit"]) == (15, None)


def test_doubles_standings_are_pairs(doubles):
    rows = doubles["standings"]
    assert len(rows) == 15
    first = rows[0]
    assert first["place"] == 1
    assert [p["player_id"] for p in first["pair"]] == ["109086", "95664"]
    assert [p["rating_at_event"] for p in first["pair"]] == [361, 282]
    assert first["pair_rating_at_event"] == 643
    assert (first["wins"], first["losses"], first["sets_won"], first["sets_lost"]) == (5, 0, 15, 5)
    assert "rating_delta" not in first  # у пар сайт дельту не публикует


def test_pair_rating_is_the_sum_of_the_two(doubles):
    # Независимая проверка смысла поля: сумма совпала во всех 15 строках.
    for row in doubles["standings"]:
        assert row["pair_rating_at_event"] == sum(p["rating_at_event"] for p in row["pair"])


def test_future_tournament_has_no_results(future):
    assert future["has_results"] is False
    assert future["standings"] == []
    assert future["games"] is None
    assert future["average_rating"] is None
    assert (future["registered"], future["registration_limit"]) == (14, 20)
    assert future["date"] == "2026-09-25"
    assert future["start_time"] == "20:30"


def test_results_section_without_rows_fails_loudly(load_fixture):
    soup = soup_of(load_fixture("tournament_243702.html"))
    for row in soup.select("table.tour-players tbody tr"):
        row.decompose()
    with pytest.raises(ParseError):
        parse_tournament(str(soup), "243702")


def test_not_found_page(load_fixture):
    with pytest.raises(NotFound):
        parse_tournament(load_fixture("player_999999999.html"), "999999999")


def test_garbage_fails_loudly():
    with pytest.raises(ParseError):
        parse_tournament("<html><body>ничего</body></html>", "1")
