"""HTTP-доступ к rttf.ru.

Клиент ничего не знает о разметке — он отдаёт текст, а разбирают его
парсеры. Запросы последовательны и разнесены во времени: между концом
одного обращения к сайту и началом следующего не меньше MIN_INTERVAL
секунд. Последовательность обеспечивает блокировка в самом клиенте: SDK
запускает синхронные инструменты в потоках, и параллельные вызовы модели
иначе ушли бы на сайт разом. Паузу держит та же блокировка: модель
вызывает инструменты подряд, и без паузы сайт видит пачку.

Каждый исходящий запрос — включая шаги перенаправления — сверяется в
хуке httpx до отправки: хост должен быть хостом сайта, адрес — открыт
robots.txt (policy.blocking_rule). Закрытое не запрашивается ни в каком
режиме, и отказ живёт в единственной точке выхода в сеть, а не на
совести каждого инструмента.
"""

import threading
import time
from collections.abc import Callable
from types import TracebackType

import httpx

from rttf_mcp.errors import NotFound, PolicyViolation, UpstreamError
from rttf_mcp.policy import blocking_rule


def _guard_for(host: str):
    def guard(request: httpx.Request) -> None:
        # Чужой хост — это чужой robots.txt, которого мы не читали, и чужая
        # страница, которую модель приняла бы за данные rttf.ru.
        if request.url.host != host:
            raise PolicyViolation(str(request.url), f"хост не {host}")
        rule = blocking_rule(request.url.path, request.url.query.decode("ascii"))
        if rule is not None:
            raise PolicyViolation(str(request.url), rule)

    return guard


class RttfClient:
    BASE_URL = "https://rttf.ru"
    USER_AGENT = "rttf-mcp/0.1 (+https://github.com/lappi/rttf-mcp)"
    TIMEOUT = 30.0
    # Пауза между концом одного обращения к сайту и началом следующего, как
    # в scripts/fetch_fixtures.py. Каждый инструмент делает один запрос, но
    # модель вызывает инструменты подряд: без паузы девять вызовов ушли на
    # сайт за секунды, и он перестал принимать соединения (2026-10-12).
    MIN_INTERVAL = 2.0

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        min_interval: float = MIN_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._timeout = timeout
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last_request: float | None = None
        self._lock = threading.Lock()
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": self.USER_AGENT},
            transport=transport,
            follow_redirects=True,
            event_hooks={"request": [_guard_for(httpx.URL(base_url).host)]},
        )

    def get_html(self, path: str, params: dict[str, str | list[str]] | None = None) -> str:
        # Блокировка держится на весь запрос вместе с повтором: повтор тоже
        # не должен обгонять чужой запрос.
        with self._lock:
            return self._get(path, params)

    def _get(self, path: str, params: dict[str, str | list[str]] | None) -> str:
        # Полный адрес с параметрами: в сообщении об ошибке он нужен целиком,
        # иначе непонятно, какой именно запрос не прошёл.
        url = str(self._client.build_request("GET", path, params=params or {}).url)
        last: Exception | None = None
        for _ in range(2):  # один повтор, только на сетевую ошибку
            try:
                response = self._spaced_get(path, params)
            except httpx.TransportError as exc:
                last = exc
                continue
            except httpx.RequestError as exc:
                # Цикл редиректов и битая кодировка лежат РЯДОМ с
                # TransportError, а не под ним, и повтор их не лечит. Но выйти
                # наружу они обязаны как UpstreamError: вызывающий ловит
                # RttfError, и сырое httpx-исключение прошло бы мимо всей
                # типизации ошибок.
                raise UpstreamError(url, f"{type(exc).__name__}: {exc}") from exc
            if response.status_code == 404:
                raise NotFound(f"{response.url} — страница не найдена")
            if response.status_code >= 400:
                raise UpstreamError(str(response.url), f"HTTP {response.status_code}")
            return response.text
        raise UpstreamError(
            url, f"{type(last).__name__}: {last} — две попытки, таймаут {self._timeout} с"
        )

    def _spaced_get(self, path: str, params: dict[str, str | list[str]] | None) -> httpx.Response:
        """Одно обращение к сайту не раньше чем через MIN_INTERVAL после конца
        предыдущего. Отсчёт идёт от любого исхода — ответа, отказа или
        сетевой ошибки: каждый из них был обращением к сайту."""
        if self._last_request is not None:
            wait = self._last_request + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        try:
            return self._client.get(path, params=params or {})
        finally:
            self._last_request = self._clock()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RttfClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
