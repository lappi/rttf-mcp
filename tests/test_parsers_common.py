import pytest

from rttf_mcp.errors import NotFound, ParseError
from rttf_mcp.parsers.common import (
    clean,
    extract_id,
    parse_date,
    parse_delta,
    parse_int,
    parse_minutes,
    parse_ru_date,
    parse_score,
    parse_win_loss,
    raise_if_not_found,
    require,
    soup_of,
)


def test_clean_collapses_whitespace_and_nbsp():
    assert clean("  один\xa0 два\n три ") == "один два три"


def test_parse_date_converts_to_iso():
    assert parse_date("20.09.2026") == "2026-09-20"
    assert parse_date("20.09.2026 16:00") == "2026-09-20"


def test_parse_date_rejects_garbage():
    with pytest.raises(ParseError):
        parse_date("не дата")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("24 марта 2013", "2013-03-24"),
        ("30 ноября 2025", "2025-11-30"),
        ("22 фев 2025", "2025-02-22"),
        ("1 мая 2024", "2024-05-01"),
        ("20 сентября 2026", "2026-09-20"),
    ],
)
def test_parse_ru_date_handles_full_and_short_months(raw, expected):
    # Полная форма — в сводке одиночного разряда, сокращённая — в сводке
    # пар одного и того же профиля (28415).
    assert parse_ru_date(raw) == expected


def test_parse_ru_date_rejects_unknown_month():
    with pytest.raises(ParseError):
        parse_ru_date("5 брюмера 2024")


@pytest.mark.parametrize(
    "raw,expected",
    [("1188", 1188), ("1'189", 1189), ("134'555", 134555), ("−18", -18), ("-1", -1), (" 12 ", 12)],
)
def test_parse_int_handles_apostrophes_and_both_minuses(raw, expected):
    assert parse_int(raw) == expected


def test_parse_int_rejects_empty():
    with pytest.raises(ParseError):
        parse_int("")


@pytest.mark.parametrize(
    "raw,expected",
    [("+0.2", 0.2), ("−5", -5.0), ("−18.8", -18.8), ("+14", 14.0), ("", 0.0), ("  ", 0.0)],
)
def test_parse_delta_reads_unicode_minus_and_empty_as_zero(raw, expected):
    assert parse_delta(raw) == pytest.approx(expected)
    assert isinstance(parse_delta(raw), float)


def test_parse_delta_rejects_text():
    with pytest.raises(ParseError):
        parse_delta("история")


@pytest.mark.parametrize(
    "raw,expected",
    [("3 - 1", (3, 1)), ("1'189 (800 - 389)", (800, 389)), ("129 (87-42)", (87, 42)), ("0 - 4", (0, 4))],
)
def test_parse_win_loss(raw, expected):
    assert parse_win_loss(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3 : 0", (3, 0, "win")),
        ("2 : 3", (2, 3, "loss")),
        ("2:1", (2, 1, "win")),
        ("W : L", (None, None, "unparsed")),
        ("", (None, None, "unparsed")),
    ],
)
def test_parse_score(raw, expected):
    assert parse_score(raw) == expected


def test_parse_minutes():
    assert parse_minutes("12 мин.") == 12
    assert parse_minutes("") is None


@pytest.mark.parametrize(
    "href,kind,expected",
    [
        ("players/28415", "players", "28415"),
        ("players/28415?type=d", "players", "28415"),
        ("/tournaments/243702", "tournaments", "243702"),
        ("halls/1073", "halls", "1073"),
    ],
)
def test_extract_id(href, kind, expected):
    assert extract_id(href, kind) == expected


@pytest.mark.parametrize("href", ["players/?cities[]=1", "results/28415", "players/abc"])
def test_extract_id_rejects_other_links(href):
    with pytest.raises(ParseError):
        extract_id(href, "players")


def test_404_page_is_not_found(load_fixture):
    with pytest.raises(NotFound):
        raise_if_not_found(soup_of(load_fixture("player_999999999.html")), "/players/999999999")


def test_require_raises_named_parse_error_on_none():
    with pytest.raises(ParseError) as excinfo:
        require(None, "section.player-info h1", "parse_player")
    assert "parse_player" in str(excinfo.value)
    assert "section.player-info h1" in str(excinfo.value)


def test_require_passes_node_through():
    sentinel = object()
    assert require(sentinel, "div", "parser") is sentinel
