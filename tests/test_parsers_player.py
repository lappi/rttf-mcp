"""Тесты parse_player на сохранённых профилях.

Ожидаемые числа сняты со страниц 2026-09-24 и сверены глазами со
страницей, а не вычислены тем же кодом. ФИО в тестах не встречаются:
игроки называются идентификаторами.
"""

import pytest

from rttf_mcp.errors import NotFound, ParseError
from rttf_mcp.parsers.common import soup_of
from rttf_mcp.parsers.player import parse_player

MATCH_KEYS = {"matches", "best_wins", "duration_minutes_total"}


@pytest.fixture
def active(load_fixture):
    return parse_player(load_fixture("player_28415.html"), "28415", include_matches=False)


@pytest.fixture
def active_full(load_fixture):
    return parse_player(load_fixture("player_28415.html"), "28415", include_matches=True)


def test_identity_fields(active):
    assert active["player_id"] == "28415"
    assert len(active["name"].split()) == 3
    assert active["city"] == "Москва (Россия)"
    assert active["sports_rank"] == "МС"
    assert active["playing_hand"] == "левая"


def test_categories_follow_the_tabs(active):
    # Вкладки профиля: rttf, пары, фнтр — и только они.
    assert list(active["categories"]) == ["s", "d", "f"]
    assert active["categories"]["s"]["label"] == "одиночный разряд"
    assert active["categories"]["f"]["label"] == "рейтинг ФНТР"


def test_singles_rating_and_maximum(active):
    s = active["categories"]["s"]
    assert s["rating_current"] == 1188
    assert s["rating_max"] == 1193
    assert s["rating_max_date"] == "2026-09-13"


def test_doubles_has_no_rating_of_its_own(active):
    # Во вкладке «пары» оба dfn пусты у всех замеренных профилей: своего
    # рейтинга у парного разряда на сайте нет. Это null, а не 0.
    d = active["categories"]["d"]
    assert d["rating_current"] is None
    assert d["rating_max"] is None
    assert d["rating_history"] == []


def test_fntr_is_a_separate_external_rating(active):
    f = active["categories"]["f"]
    assert f["rating_current"] == 1967
    assert f["rating_max"] == 1967
    assert f["rating_max_date"] == "2025-11-01"
    assert f["stats"] is None
    assert f["recent_tournaments"] is None
    assert f["recent_tournaments_truncated"] is None


def test_rating_history_is_the_full_chart(active):
    history = active["categories"]["s"]["rating_history"]
    assert len(history) == 222
    # Первая точка — стартовый рейтинг без даты.
    assert history[0] == {"date": None, "rating": 300}
    assert history[1] == {"date": "2013-03-24", "rating": 280}
    assert history[-1] == {"date": "2026-09-20", "rating": 1188}


def test_singles_stats(active):
    stats = active["categories"]["s"]["stats"]
    assert stats["rank_position"] == 40
    assert stats["tournaments_played"] == 218
    assert stats["games"] == 1189
    assert stats["wins"] == 800
    assert stats["losses"] == 389
    assert stats["first_tournament_date"] == "2013-03-24"
    assert stats["most_frequent_hall"] == {"hall_id": "53", "name": "Лига ПРО", "tournaments": 80}


def test_doubles_stats_use_the_short_month_form(active):
    stats = active["categories"]["d"]["stats"]
    assert stats["rank_position"] is None
    assert stats["tournaments_played"] == 20
    assert (stats["games"], stats["wins"], stats["losses"]) == (129, 87, 42)
    assert stats["first_tournament_date"] == "2025-02-22"


def test_only_ten_recent_tournaments_and_truncation_is_flagged(active):
    s = active["categories"]["s"]
    assert len(s["recent_tournaments"]) == 10
    assert s["recent_tournaments_truncated"] is True


def test_recent_tournament_fields(active):
    first = active["categories"]["s"]["recent_tournaments"][0]
    assert first["tournament_id"] == "243702"
    assert first["date"] == "2026-09-20"
    assert first["start_time"] == "16:00"
    assert first["limit"] == "отк"
    assert first["hall_name"] == "NEO TTA"
    assert first["participants"] == 31
    assert first["place"] == 2
    assert first["rating_before_event"] == 1193
    assert first["rating_after_event"] == 1188
    assert first["rating_delta"] == pytest.approx(-5.0)
    assert first["bonus"] is None
    assert (first["wins"], first["losses"]) == (3, 1)


def test_tournament_without_place_gives_null(active):
    # Лига 222341: в шапке нет «N место».
    league = active["categories"]["s"]["recent_tournaments"][-1]
    assert league["tournament_id"] == "222341"
    assert league["place"] is None


def test_doubles_recent_tournament(active):
    first = active["categories"]["d"]["recent_tournaments"][0]
    assert first["tournament_id"] == "239285"
    assert first["place"] == 2
    assert [p["player_id"] for p in first["pair"]] == ["28415", "126644"]
    assert [p["rating_at_event"] for p in first["pair"]] == [1192, 323]
    assert first["pair_rating_at_event"] == 1515
    assert (first["wins"], first["losses"]) == (5, 1)


def test_match_level_is_absent_without_the_flag(active):
    # Ключей нет вовсе, а не пустые списки: «не запрашивали» и «матчей не
    # было» — разные ответы.
    assert "best_wins" not in active
    for category in active["categories"].values():
        for entry in category["recent_tournaments"] or []:
            assert MATCH_KEYS.isdisjoint(entry), entry["tournament_id"]


def test_singles_matches(active_full):
    first = active_full["categories"]["s"]["recent_tournaments"][0]
    assert first["duration_minutes_total"] == 55
    assert len(first["matches"]) == 4
    opening, final = first["matches"][0], first["matches"][-1]
    assert opening["start_time"] == "16:46"
    assert opening["stage"] == "1/8"
    assert opening["opponent_id"] == "167955"
    assert opening["opponent_rating_at_event"] == 624
    assert (opening["score_for"], opening["score_against"], opening["result"]) == (3, 0, "win")
    assert opening["rating_delta"] == 0.0  # пустая ячейка
    assert opening["duration_minutes"] == 12
    assert final["opponent_id"] == "199984"
    assert final["result"] == "loss"
    assert final["rating_delta"] == pytest.approx(-5.0)


def test_opponent_rating_is_the_rating_before_that_tournament(active_full, load_fixture):
    # Независимая опора: итоговая таблица турнира 243702 показывает рейтинг
    # «до» у соперника 199984 — 1137, и тот же 1137 стоит в строке матча
    # профиля. Сегодняшний рейтинг соперника другой (1144 после турнира).
    from rttf_mcp.parsers.tournament import parse_tournament

    tournament = parse_tournament(load_fixture("tournament_243702.html"), "243702")
    row = next(r for r in tournament["standings"] if r["player_id"] == "199984")
    final = active_full["categories"]["s"]["recent_tournaments"][0]["matches"][-1]
    assert final["opponent_rating_at_event"] == row["rating_before_event"] == 1137
    assert row["rating_after_event"] == 1144


def test_match_without_time_and_stage(active_full):
    league = active_full["categories"]["s"]["recent_tournaments"][-1]
    assert league["duration_minutes_total"] is None
    assert league["matches"][0]["start_time"] is None
    assert league["matches"][0]["stage"] is None
    assert league["matches"][0]["duration_minutes"] is None


def test_doubles_matches_have_four_participants(active_full):
    first = active_full["categories"]["d"]["recent_tournaments"][0]
    assert first["duration_minutes_total"] == 99
    match = first["matches"][0]
    assert [p["player_id"] for p in match["opponents"]] == ["141107", "129143"]
    assert [p["rating_at_event"] for p in match["opponents"]] == [357, 428]
    assert match["opponents_pair_rating_at_event"] == 785
    assert (match["score_for"], match["score_against"]) == (2, 1)
    assert "rating_delta" not in match  # у парных матчей дельты нет на сайте


def test_best_wins(active_full):
    wins = active_full["best_wins"]
    assert len(wins) == 10
    top = wins[0]
    assert top["tournament_id"] == "46509"
    assert top["date"] == "2020-10-15"
    assert top["player_rating_at_event"] == 1043
    assert top["opponent_id"] == "22833"
    assert top["opponent_rating_at_event"] == 1312
    assert (top["score_for"], top["score_against"]) == (4, 0)
    assert top["rating_delta"] == pytest.approx(17.7)


def test_beginner_ratings_go_negative_and_reset(load_fixture):
    # Профиль 226194: рейтинг после турнира бывает отрицательным (ASCII «-»
    # в dfn), а перед следующим турниром сайт ставит 1.
    profile = parse_player(load_fixture("player_226194.html"), "226194", include_matches=False)
    s = profile["categories"]["s"]
    assert list(profile["categories"]) == ["s"]
    by_id = {e["tournament_id"]: e for e in s["recent_tournaments"]}
    assert by_id["207191"]["rating_after_event"] == -18
    assert by_id["207191"]["rating_delta"] == pytest.approx(-19.3)
    assert by_id["207481"]["rating_before_event"] == 1
    assert by_id["207481"]["rating_after_event"] == -1
    assert by_id["243022"]["limit"] == "дети"
    assert s["stats"]["rank_position"] == 134555
    assert s["recent_tournaments_truncated"] is False


def test_empty_total_delta_is_zero_not_missing(load_fixture):
    profile = parse_player(load_fixture("player_226194.html"), "226194", include_matches=False)
    last = profile["categories"]["s"]["recent_tournaments"][0]
    assert last["tournament_id"] == "243702"
    assert last["rating_before_event"] == last["rating_after_event"] == 12
    assert last["rating_delta"] == 0.0


def test_maximum_without_date(load_fixture):
    # «Максимальный: 50 ()» — стартовые 50 без даты.
    profile = parse_player(load_fixture("player_226194.html"), "226194", include_matches=False)
    s = profile["categories"]["s"]
    assert s["rating_max"] == 50
    assert s["rating_max_date"] is None


def test_bonus_for_prize_place_and_equipment(load_fixture):
    profile = parse_player(load_fixture("player_70524.html"), "70524", include_matches=False)
    first, second = profile["categories"]["s"]["recent_tournaments"]
    assert (first["place"], first["bonus"]) == (3, pytest.approx(7.1))
    assert (second["place"], second["bonus"]) == (1, pytest.approx(14.0))
    assert profile["equipment"]["blade"]["item_id"] == "2229"
    assert profile["equipment"]["rubber_right"]["item_id"] == "5"
    assert profile["equipment"]["rubber_left"]["item_id"] == "5"


def test_missing_equipment_is_null(active):
    assert active["equipment"] == {"blade": None, "rubber_right": None, "rubber_left": None}


def test_doubles_only_profile_has_no_singles_key(load_fixture):
    # Игрок без одиночного разряда: вкладки rat_s нет. Ключа "s" нет вовсе —
    # категории на сайте нет, это не сбой. Профиль собран из настоящего
    # удалением вкладки и секции, а не написан руками.
    soup = soup_of(load_fixture("player_28415.html"))
    soup.select_one('li[data-tab="rat_s"]').decompose()
    soup.select_one("main.players > section.rat_s").decompose()
    profile = parse_player(str(soup), "28415", include_matches=True)
    assert list(profile["categories"]) == ["d", "f"]
    assert profile["best_wins"] == []


def test_not_found_page(load_fixture):
    with pytest.raises(NotFound):
        parse_player(load_fixture("player_999999999.html"), "999999999", include_matches=False)


def test_garbage_fails_loudly():
    # Контрольный случай из чужого парсера, который на любой ошибке
    # возвращает None: у нас мусор обязан давать ParseError.
    with pytest.raises(ParseError):
        parse_player("<html><body><p>ничего похожего</p></body></html>", "1", include_matches=False)


def test_tab_without_its_section_fails_loudly(load_fixture):
    soup = soup_of(load_fixture("player_28415.html"))
    soup.select_one("main.players > section.rat_d").decompose()
    with pytest.raises(ParseError) as excinfo:
        parse_player(str(soup), "28415", include_matches=False)
    assert "section.rat_d" in str(excinfo.value)


def test_absolute_tournament_links_still_parse(load_fixture):
    soup = soup_of(load_fixture("player_28415.html"))
    for a in soup.select('section.player-results > a[href^="tournaments/"]'):
        a["href"] = "/" + a["href"]
    profile = parse_player(str(soup), "28415", include_matches=False)
    assert len(profile["categories"]["s"]["recent_tournaments"]) == 10


def test_results_table_without_its_heading_fails_loudly(load_fixture):
    # Таблиц результатов на странице десять, а разобрано меньше — это
    # изменившаяся вёрстка, а не «турниров нет».
    soup = soup_of(load_fixture("player_28415.html"))
    soup.select_one('section.rat_s section.player-results > a[href^="tournaments/"]').decompose()
    with pytest.raises(ParseError):
        parse_player(str(soup), "28415", include_matches=False)


def test_best_wins_rows_without_the_interval_class_fail_loudly(load_fixture):
    soup = soup_of(load_fixture("player_28415.html"))
    for row in soup.select("section.player-wins tbody tr"):
        del row["class"]
    with pytest.raises(ParseError):
        parse_player(str(soup), "28415", include_matches=True)


def test_missing_rating_chart_fails_loudly(load_fixture):
    # Пропали и подписи, и значения: без проверки история молча стала бы [].
    html = load_fixture("player_28415.html")
    html = html.replace("chartAllData_s", "chartGoneData_s").replace("chartAllLabels_s", "chartGoneLabels_s")
    with pytest.raises(ParseError):
        parse_player(html, "28415", include_matches=False)


def test_non_numeric_chart_value_is_a_parse_error(load_fixture):
    html = load_fixture("player_28415.html")
    assert "chartAllData_s = [300," in html
    html = html.replace("chartAllData_s = [300,", "chartAllData_s = [null,")
    with pytest.raises(ParseError):
        parse_player(html, "28415", include_matches=False)


def test_empty_tournament_heading_is_a_parse_error(load_fixture):
    soup = soup_of(load_fixture("player_28415.html"))
    soup.select_one('section.rat_s section.player-results > a[href^="tournaments/"]').clear()
    with pytest.raises(ParseError):
        parse_player(str(soup), "28415", include_matches=False)
