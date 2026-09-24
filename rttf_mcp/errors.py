"""Типы ошибок rttf-mcp.

Главное требование: потребитель — языковая модель, поэтому сломанный
парсер обязан отличаться от честно пустого результата.
"""


class RttfError(Exception):
    """Общий предок всех ошибок rttf-mcp."""


class InvalidInput(RttfError):
    """Аргумент инструмента не прошёл проверку; запрос не отправлялся."""


class NotFound(RttfError):
    """Сайт ответил, но запрошенной сущности нет."""


class ParseError(RttfError):
    """Разметка не совпала с ожидаемой — вероятно, сайт изменился."""

    def __init__(self, parser: str, selector: str, detail: str = "") -> None:
        self.parser = parser
        self.selector = selector
        self.detail = detail
        message = f"{parser}: не найден селектор {selector!r}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


class UpstreamError(RttfError):
    """Сетевой сбой или неуспешный HTTP-код от rttf.ru."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"запрос к {url} не удался: {reason}")


class PolicyViolation(RttfError):
    """Сервер попытался запросить адрес, закрытый robots.txt.

    Это всегда ошибка в коде сервера, а не во входных данных: закрытые
    адреса не запрашиваются ни в одном режиме, и клиент отказывает до
    обращения к сети.
    """

    def __init__(self, url: str, rule: str) -> None:
        self.url = url
        self.rule = rule
        super().__init__(f"адрес {url} закрыт правилом robots.txt {rule!r}; запрос не отправлялся")
