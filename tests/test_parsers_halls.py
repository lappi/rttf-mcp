import pytest

from rttf_mcp.errors import NotFound, ParseError
from rttf_mcp.parsers.halls import parse_hall


@pytest.fixture
def hall(load_fixture):
    return parse_hall(load_fixture("hall_1073.html"), "1073")


def test_hall_fields(hall):
    assert hall["hall_id"] == "1073"
    assert hall["name"] == "NEO Table Tennis Academy"
    assert hall["address"] == "г. Москва, Каширское ш., д. 14, ТЦ Гудзон, 2-й этаж"
    assert hall["metro"] == ["Варшавская", "Каширская", "Нагатинская"]
    assert hall["opening_hours"] == "10:00 - 00:00"
    assert hall["tables"] == 10
    assert hall["website"] == "https://www.neotennis.ru"
    assert hall["representative_player_id"] == "249400"


def test_amenities_split_free_and_paid(hall):
    assert hall["amenities"] == ["кондиционер", "wi-fi", "душ", "парковка", "кулер", "настольные игры"]
    assert hall["paid_amenities"] == ["кафе", "магазин", "кинотеатр"]


def test_rating(hall):
    assert hall["rating"]["overall"] == pytest.approx(4.3)
    assert len(hall["rating"]["aspects"]) == 5
    assert hall["rating"]["aspects"]["Расположение"] == pytest.approx(4.1)


def test_contacts_are_not_extracted(hall, load_fixture):
    # Телефоны сотрудников на странице есть — в ответе их нет.
    assert "+7929" in load_fixture("hall_1073.html")
    assert "+7929" not in repr(hall)
    assert "contacts" not in hall


def test_not_found_page(load_fixture):
    with pytest.raises(NotFound):
        parse_hall(load_fixture("player_999999999.html"), "999999999")


def test_garbage_fails_loudly():
    with pytest.raises(ParseError):
        parse_hall("<html><body>ничего</body></html>", "1")


def test_non_numeric_rating_is_a_parse_error(load_fixture):
    html = load_fixture("hall_1073.html").replace("<td>4.1</td>", "<td>n/a</td>", 1)
    with pytest.raises(ParseError):
        parse_hall(html, "1073")
