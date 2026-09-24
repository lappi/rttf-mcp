import httpx
import pytest

from rttf_mcp.client import RttfClient
from rttf_mcp.errors import NotFound, PolicyViolation, UpstreamError


def make_client(handler) -> RttfClient:
    return RttfClient(transport=httpx.MockTransport(handler))


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
