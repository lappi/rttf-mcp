import httpx
import pytest

from rttf_mcp.client import RttfClient
from rttf_mcp.errors import NotFound, PolicyViolation, UpstreamError


def make_client(handler, **kwargs) -> RttfClient:
    # Интервал между запросами по умолчанию выключен: тесты ниже проверяют
    # другое и не должны спать. Сам интервал проверяется с поддельными часами.
    kwargs.setdefault("min_interval", 0.0)
    return RttfClient(transport=httpx.MockTransport(handler), **kwargs)


class FakeTime:
    """Часы и sleep без настоящего ожидания: sleep двигает часы вперёд."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_get_html_returns_body():
    def handler(request):
        assert request.url.path == "/players/28415"
        return httpx.Response(200, text="<html>профиль</html>")

    with make_client(handler) as client:
        assert client.get_html("/players/28415") == "<html>профиль</html>"


def test_list_params_are_sent_in_array_form():
    seen = {}

    def handler(request):
        seen["params"] = request.url.params.multi_items()
        return httpx.Response(200, text="ok")

    with make_client(handler) as client:
        client.get_html("/players/", {"cities[]": ["r77", "2"], "rat_to": "990"})
    assert seen["params"] == [("cities[]", "r77"), ("cities[]", "2"), ("rat_to", "990")]


def test_user_agent_is_sent():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, text="ok")

    with make_client(handler) as client:
        client.get_html("/players/28415")
    assert seen["ua"] == RttfClient.USER_AGENT
    assert "github.com/lappi/rttf-mcp" in seen["ua"]


@pytest.mark.parametrize(
    "path,params",
    [
        ("/games/", {"tourID": "243702"}),
        ("/results/28415", None),
        ("/rivals/28415", None),
        ("/players/", {"name": "фамилия"}),
        ("/tournaments/", {"date_from": "01.09.2026"}),
    ],
)
def test_closed_address_is_refused_before_the_network(path, params):
    def handler(request):
        raise AssertionError(f"запрос ушёл в сеть: {request.url}")

    with make_client(handler) as client:
        with pytest.raises(PolicyViolation) as excinfo:
            client.get_html(path, params)
    assert excinfo.value.rule


def test_redirect_to_a_closed_address_is_not_followed():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/players/28415":
            return httpx.Response(302, headers={"Location": "/results/28415"})
        raise AssertionError(f"закрытый адрес запрошен: {request.url}")

    with make_client(handler) as client:
        with pytest.raises(PolicyViolation):
            client.get_html("/players/28415")
    assert calls == ["/players/28415"]


def test_404_becomes_not_found():
    def handler(request):
        return httpx.Response(404, text="not here")

    with make_client(handler) as client:
        with pytest.raises(NotFound):
            client.get_html("/players/999999999")


def test_server_error_becomes_upstream_error_without_retry():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500, text="boom")

    with make_client(handler) as client:
        with pytest.raises(UpstreamError):
            client.get_html("/players/28415")
    assert len(calls) == 1


def test_network_error_is_retried_exactly_once_then_raises():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectTimeout("нет связи")

    with make_client(handler) as client:
        with pytest.raises(UpstreamError) as excinfo:
            client.get_html("/players/", {"rat_to": "990"})
    assert len(calls) == 2
    # Причина называет настоящий тип сбоя, а адрес сохраняет параметры —
    # иначе неясно, что именно упало.
    assert "ConnectTimeout" in excinfo.value.reason
    assert "rat_to=990" in excinfo.value.url


def test_redirect_loop_becomes_upstream_error():
    # TooManyRedirects — сосед TransportError, а не потомок: узкий except
    # выпускал его наружу сырым, мимо типизации ошибок проекта.
    def handler(request):
        return httpx.Response(302, headers={"Location": "/tournaments/"})

    with make_client(handler) as client:
        with pytest.raises(UpstreamError) as excinfo:
            client.get_html("/tournaments/")
    assert "TooManyRedirects" in excinfo.value.reason


def test_retry_succeeds_on_second_attempt():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("медленно")
        return httpx.Response(200, text="получилось")

    with make_client(handler) as client:
        assert client.get_html("/players/28415") == "получилось"
    assert len(calls) == 2


def test_concurrent_calls_reach_the_site_one_at_a_time():
    # SDK запускает синхронные инструменты в потоках, и модель часто вызывает
    # несколько инструментов разом. Сайт должен видеть их по очереди.
    import threading
    import time

    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def handler(request):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return httpx.Response(200, text="ok")

    with make_client(handler) as client:
        threads = [threading.Thread(target=client.get_html, args=("/players/28415",)) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert state["peak"] == 1


def test_redirect_to_another_host_is_refused():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "rttf.ru":
            return httpx.Response(302, headers={"Location": "https://other.example/anything"})
        return httpx.Response(200, text="чужая страница")

    with make_client(handler) as client:
        with pytest.raises(PolicyViolation) as excinfo:
            client.get_html("/players/28415")
    assert calls == ["https://rttf.ru/players/28415"]
    assert "other.example" in str(excinfo.value)


def spaced_client(handler, fake: FakeTime) -> RttfClient:
    return make_client(handler, min_interval=2.0, clock=fake.clock, sleep=fake.sleep)


def test_default_interval_is_two_seconds():
    # Спека, §5: клиент — копия ttw-mcp «с правкой пауз». Без паузы девять
    # вызовов модели подряд ушли на сайт за секунды, и сайт перестал
    # принимать соединения с этого адреса (2026-10-12).
    assert RttfClient.MIN_INTERVAL == 2.0


def test_first_request_does_not_wait():
    fake = FakeTime()
    with spaced_client(lambda request: httpx.Response(200, text="ok"), fake) as client:
        client.get_html("/players/28415")
    assert fake.sleeps == []


def test_back_to_back_requests_are_spaced_from_the_end_of_the_previous_one():
    fake = FakeTime()

    def handler(request):
        fake.now += 0.3  # сайт отвечает 0.3 с
        return httpx.Response(200, text="ok")

    with spaced_client(handler, fake) as client:
        client.get_html("/players/28415")
        client.get_html("/tournaments/243702")
    assert fake.sleeps == [pytest.approx(2.0)]


def test_no_wait_when_the_interval_has_already_passed():
    fake = FakeTime()
    with spaced_client(lambda request: httpx.Response(200, text="ok"), fake) as client:
        client.get_html("/players/28415")
        fake.now += 5.0  # модель думала пять секунд
        client.get_html("/players/28415")
    assert fake.sleeps == []


def test_retry_also_waits_the_interval():
    fake = FakeTime()
    calls = []

    def handler(request):
        calls.append(fake.now)
        fake.now += 0.1
        if len(calls) == 1:
            raise httpx.ConnectTimeout("нет связи")
        return httpx.Response(200, text="ok")

    with spaced_client(handler, fake) as client:
        assert client.get_html("/players/28415") == "ok"
    assert fake.sleeps == [pytest.approx(2.0)]
    assert calls[1] - calls[0] == pytest.approx(2.1)


def test_a_refused_request_still_counts_for_the_interval():
    # Отказ сайта — тоже обращение к нему; следующий запрос ждёт так же.
    fake = FakeTime()

    def handler(request):
        fake.now += 0.1
        return httpx.Response(500, text="boom")

    with spaced_client(handler, fake) as client:
        with pytest.raises(UpstreamError):
            client.get_html("/players/28415")
        with pytest.raises(UpstreamError):
            client.get_html("/players/28415")
    assert fake.sleeps == [pytest.approx(2.0)]

