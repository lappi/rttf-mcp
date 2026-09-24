"""Обновление тестовых фикстур с живого сайта.

Запускается вручную, когда надо проверить, не изменилась ли вёрстка:

    uv run python scripts/fetch_fixtures.py

Фикстуры не хранятся в git: в них ФИО, логины и даты рождения реальных
людей. Запросы идут через RttfClient, поэтому закрытый robots.txt адрес
сюда не попадёт даже по ошибке; последовательно, с паузой.

ВНИМАНИЕ: ожидаемые числа в тестах сняты со снимка 2026-09-24. Профили
живые — после нового турнира у игрока 28415 сдвинутся рейтинг, список
последних турниров и история. Упавший после перекачки тест означает
«пересними значение со страницы глазами», а не «подгони парсер».
"""

import time
from pathlib import Path

from rttf_mcp.client import RttfClient

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

PAGES = [
    ("player_28415.html", "/players/28415", {}),
    ("player_226194.html", "/players/226194", {}),
    ("player_70524.html", "/players/70524", {}),
    ("tournament_243702.html", "/tournaments/243702", {}),
    ("tournament_244471.html", "/tournaments/244471", {}),
    ("tournament_244791.html", "/tournaments/244791", {}),
    ("players_top.html", "/players/", {}),
    ("players_window_990.html", "/players/", {"rat_from": "0", "rat_to": "990"}),
    ("players_tie_1.html", "/players/", {"rat_from": "1", "rat_to": "1"}),
    ("players_city_2.html", "/players/", {"cities[]": "2"}),
    ("tournaments.html", "/tournaments/", {}),
    ("tournaments_hall_1073.html", "/tournaments/", {"halls[]": "1073"}),
    ("hall_1073.html", "/halls/1073", {}),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with RttfClient() as client:
        for name, path, params in PAGES:
            html = client.get_html(path, params)
            (OUT / name).write_text(html, encoding="utf-8")
            print(f"{name}: {len(html)} символов")
            time.sleep(2)
        # Страница 404 сайта: клиент превращает код 404 в NotFound, а тесту
        # нужна сама разметка main.page404, поэтому она берётся напрямую.
        import httpx

        response = httpx.get(
            f"{RttfClient.BASE_URL}/players/999999999",
            headers={"User-Agent": RttfClient.USER_AGENT},
            timeout=RttfClient.TIMEOUT,
        )
        (OUT / "player_999999999.html").write_text(response.text, encoding="utf-8")
        print(f"player_999999999.html: HTTP {response.status_code}")


if __name__ == "__main__":
    main()
