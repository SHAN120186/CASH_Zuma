"""Единые часы приложения: рабочая дата Ташкента и время сервера в UTC.

Все модули берут дату через ``services.today()`` и время через ``db.now()``, которые
обращаются сюда. Тесты фиксируют дату через ``FROZEN``; в работе переменная не задаётся,
и часы идут по системному времени.
"""
from datetime import date, datetime, timedelta, timezone

LOCAL_TZ = timezone(timedelta(hours=5))
# Только для тестов: фиксированная рабочая дата. Время суток при этом идёт как обычно.
FROZEN: date | None = None


def _shift():
    if FROZEN is None:
        return timedelta(0)
    return FROZEN - datetime.now(LOCAL_TZ).date()


def utcnow():
    """Наивное время UTC для записей базы."""
    return (datetime.now(timezone.utc) + _shift()).replace(tzinfo=None)


def today():
    """Рабочая дата в Ташкенте."""
    return FROZEN if FROZEN is not None else datetime.now(LOCAL_TZ).date()
