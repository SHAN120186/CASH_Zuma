"""Рабочие дни и минимальные сроки заявок по приоритету.

Неделя Пн–Пт рабочая, Сб и Вс выходные. Настраиваемый календарь холдинга (calendar_days)
добавляет праздники и рабочие выходные. Срок считается в рабочих днях строго после
сегодняшнего дня, поэтому текущая дата никогда не подходит.
"""
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select
from .db import CalendarDay

LEAD_DAYS = {'normal': 7, 'high': 3, 'urgent': 1}
PRIORITY_LABELS = {'normal': 'обычного', 'high': 'высокого', 'urgent': 'срочного'}
MAX_SCAN = 400


def calendar_overrides(s, start, end):
    return {row.day: row.kind for row in s.scalars(select(CalendarDay).where(CalendarDay.day >= start, CalendarDay.day <= end))}


def is_working_day(day, overrides):
    kind = overrides.get(day)
    if kind == 'holiday':
        return False
    if kind == 'workday':
        return True
    return day.weekday() < 5


def add_working_days(s, start, n):
    """N-й рабочий день строго после ``start``."""
    overrides = calendar_overrides(s, start, start + timedelta(days=MAX_SCAN))
    day, left = start, n
    for _ in range(MAX_SCAN):
        day += timedelta(days=1)
        if is_working_day(day, overrides):
            left -= 1
            if left == 0:
                return day
    raise HTTPException(500, 'Календарь рабочих дней заполнен неверно: нет рабочих дней на год вперёд.')


def earliest_due(s, priority, today):
    return add_working_days(s, today, LEAD_DAYS[priority])


def due_minimums(s, today):
    return {p: str(earliest_due(s, p, today)) for p in LEAD_DAYS}


def enforce_lead_time(s, priority, due, today):
    earliest = earliest_due(s, priority, today)
    if due <= today:
        raise HTTPException(422, 'Плановая дата заявки не может быть сегодняшней или прошедшей.')
    if due < earliest:
        raise HTTPException(422, f'Для {PRIORITY_LABELS[priority]} приоритета нужно не меньше {LEAD_DAYS[priority]} '
                                 f'рабочих дней: ближайшая допустимая дата {earliest.strftime("%d.%m.%Y")}.')
