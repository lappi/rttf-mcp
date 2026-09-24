"""HTTP-доступ к rttf.ru.

Клиент ничего не знает о разметке — он отдаёт текст, а разбирают его
парсеры. Запросы последовательны: сайт отвечает за 1–3 секунды, и
параллельные пачки ему ни к чему. Последовательность обеспечивает
блокировка в самом клиенте: SDK запускает синхронные инструменты в
потоках, и параллельные вызовы модели иначе ушли бы на сайт разом.

Каждый исходящий запрос — включая шаги перенаправления — сверяется в
хуке httpx до отправки: хост должен быть хостом сайта, адрес — открыт
robots.txt (policy.blocking_rule). Закрытое не запрашивается ни в каком
режиме, и отказ живёт в единственной точке выхода в сеть, а не на
совести каждого инструмента.
"""

import threading
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

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeout = timeout
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
                response = self._client.get(path, params=params or {})
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
