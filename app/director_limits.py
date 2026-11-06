"""Совместимый ответ старым клиентам: верхний запрет директора отменён владельцем."""
LIMIT_FIELDS = {cur: 'director_limit_' + cur.lower() for cur in ('UZS', 'USD', 'EUR')}


def apply_default_director_limit(category):
    # Имя сохранено для существующих точек создания/импорта статей.
    from .director_thresholds import apply_default_director_threshold
    return apply_default_director_threshold(category)


def limits_json(category):
    return {cur: None for cur in LIMIT_FIELDS}


def request_director_limit(s, request, currency, batch=None):
    return {'currency': currency, 'items': [], 'exceeded': False, 'reason': ''}
