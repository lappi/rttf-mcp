import pytest

from rttf_mcp.errors import (
    InvalidInput,
    NotFound,
    ParseError,
    PolicyViolation,
    RttfError,
    UpstreamError,
)


def test_parse_error_names_parser_and_selector():
    err = ParseError("parse_player", "section.player-info h1")
    assert "parse_player" in str(err)
    assert "section.player-info h1" in str(err)


def test_parse_error_includes_detail_when_given():
    err = ParseError("parse_tournament", "table.tour-players", "пустая таблица")
    assert "пустая таблица" in str(err)


def test_upstream_error_names_url_and_reason():
    err = UpstreamError("https://rttf.ru/players/28415", "timeout 30.0s")
    assert "https://rttf.ru/players/28415" in str(err)
    assert "timeout 30.0s" in str(err)


def test_policy_violation_names_url_and_rule():
    err = PolicyViolation("https://rttf.ru/games/?tourID=1", "/games")
    assert "https://rttf.ru/games/?tourID=1" in str(err)
    assert "'/games'" in str(err)


@pytest.mark.parametrize("cls", [InvalidInput, NotFound, ParseError, UpstreamError, PolicyViolation])
def test_all_errors_share_common_ancestor(cls):
    assert issubclass(cls, RttfError)
