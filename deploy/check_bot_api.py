"""Read-only check of the bot API of a running Cash Zuma site: why the bot gets, or does not get, its data.

    python deploy/check_bot_api.py --url https://cash-zuma.onrender.com [--request CF-00005]
                                   [--telegram-user-id 123456789] [--chat-id -1001234567890]

The key is read from the environment only (the bot's names CASH_ZUMA_BOT_CLIENT_ID /
CASH_ZUMA_BOT_CLIENT_SECRET, or the site's BOT_CLIENT_ID / BOT_CLIENT_SECRET), never from the
command line, and it is never printed. Every call is a GET; nothing on the site changes except
its own journal entries for bot reads.

Printed: HTTP status, duration, the server's own short error text, the published version, and
whether the answers carry the fields the bot needs. Never printed: amounts, purposes, names,
account numbers, Telegram ids of other people, tokens or raw response bodies.

The last line is one of these verdicts, so the owner can paste the output as it is:
  OK                      the key is accepted, the schema is compatible, the queue was read
  KEY_NOT_CONFIGURED      the site answers 503 «API бота на сайте не настроен»: BOT_CLIENT_ID /
                          BOT_CLIENT_SECRET are missing on the site or the secret is shorter than 32
  KEY_REJECTED            401: the bot's key differs from the site's
  THROTTLED               429: eight wrong keys from this address within 10 minutes; wait
  ROUTE_MISSING           404 on a bot route: an older site version is published, or the URL is wrong
  DATABASE_UNAVAILABLE    503 «База данных временно недоступна»: the site runs, its database does not
  SERVER_ERROR            another 5xx from the site
  INCOMPATIBLE_SCHEMA     200 without the fields the bot requires (company_code since site 2.14.10)
  TIMEOUT / UNREACHABLE   no answer in time (a sleeping free instance wakes up in about a minute) or
                          no connection at all
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Any, Callable, Sequence, TextIO

import httpx

REQUIRED_SITE = (2, 14, 10)  # the first site whose pending items carry company_code
ITEM_FIELDS = ("request_id", "number", "status", "stage", "assignee_user_id", "assignee_telegram_id",
               "stage_entered_at", "company", "company_code", "amount", "currency")
GROUP_FIELDS = ("chat_id", "companies", "source")
TIMEOUT = httpx.Timeout(70.0, connect=15.0)  # a free Render instance takes up to a minute to wake


class Report:
    def __init__(self, out: TextIO):
        self.out = out
        self.verdict = "OK"

    def line(self, text: str) -> None:
        print(text, file=self.out)

    def fail(self, verdict: str, text: str) -> None:
        self.line(text)
        if self.verdict == "OK":
            self.verdict = verdict


def version_tuple(text: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())
    return tuple(int(x) for x in match.groups()) if match else None


def safe_detail(response: httpx.Response) -> str:
    """The server's own short error text (a plain string, bounded); never a body of data."""
    try:
        body = response.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail[:160] if isinstance(detail, str) else ""


def classify(response: httpx.Response) -> tuple[str, str]:
    """The verdict for a refused bot call and the text to print for it."""
    status, detail = response.status_code, safe_detail(response)
    if status == 503 and "не настроен" in detail:
        return "KEY_NOT_CONFIGURED", "503: на сайте не заданы BOT_CLIENT_ID / BOT_CLIENT_SECRET или секрет короче 32 символов"
    if status == 503 and "База данных" in detail:
        return "DATABASE_UNAVAILABLE", "503: сайт работает, база данных не ответила (Neon спит или недоступна)"
    if status == 401:
        return "KEY_REJECTED", "401: ключ бота не совпадает с ключом на сайте"
    if status == 429:
        return "THROTTLED", "429: с этого адреса было 8 неверных ключей за 10 минут — подождите 10 минут"
    if status == 404:
        return "ROUTE_MISSING", "404: маршрута нет — опубликована старая версия сайта или адрес не сайт Cash Zuma"
    if status >= 500:
        return "SERVER_ERROR", f"{status}: ошибка сервера" + (f" — {detail}" if detail else "")
    return "SERVER_ERROR", f"{status}: неожиданный ответ" + (f" — {detail}" if detail else "")


def get(client: httpx.Client, path: str, params: dict[str, str] | None = None) -> tuple[httpx.Response | None, str, float]:
    started = time.monotonic()
    try:
        response = client.get(path, params=params)
    except httpx.TimeoutException:
        return None, "TIMEOUT", time.monotonic() - started
    except httpx.TransportError as exc:
        return None, f"UNREACHABLE ({exc.__class__.__name__})", time.monotonic() - started
    return response, "", time.monotonic() - started


def check_items(items: Any, report: Report, label: str) -> list[dict[str, Any]]:
    """Whether every item carries the fields the bot requires; returns the usable items."""
    if not isinstance(items, list):
        report.fail("INCOMPATIBLE_SCHEMA", f"{label}: в ответе нет списка items")
        return []
    missing: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            missing.add("(строка не объект)")
            continue
        missing |= {f for f in ITEM_FIELDS if f not in item}
        code = item.get("company_code")
        if not isinstance(code, str) or not code.strip():
            missing.add("company_code (пустой)")
    if missing:
        report.fail("INCOMPATIBLE_SCHEMA",
                    f"{label}: строк {len(items)}, нет обязательных полей: {', '.join(sorted(missing))} — "
                    "бот 0.2.x такие заявки не принимает (нужен сайт 2.14.10 или новее)")
    else:
        report.line(f"{label}: строк {len(items)}, все обязательные поля на месте")
    return [i for i in items if isinstance(i, dict)]


def describe_request(items: list[dict[str, Any]], number: str, report: Report, label: str) -> bool:
    """Whether the request is in these items; prints only its stage, status and company code."""
    wanted = number.strip().upper()
    digits = wanted.split("-")[-1].lstrip("0") or "0"
    rows = [i for i in items if str(i.get("number", "")).upper() == wanted or str(i.get("request_id")) == digits]
    if not rows:
        report.line(f"{label}: заявки {wanted} в списке нет")
        return False
    for row in rows:
        linked = "да" if row.get("assignee_telegram_id") is not None else "нет"
        report.line(f"{label}: заявка {wanted} — статус {row.get('status')}, этап {row.get('stage')}, "
                    f"компания {row.get('company_code')}, ответственный user_id {row.get('assignee_user_id')}, "
                    f"его Telegram привязан: {linked}")
    return True


def run(args: argparse.Namespace, env: dict[str, str], out: TextIO, transport: Any = None) -> int:
    report = Report(out)
    url = (args.url or env.get("CASH_ZUMA_API_URL") or env.get("PUBLIC_ORIGIN") or "").rstrip("/")
    client_id = env.get("CASH_ZUMA_BOT_CLIENT_ID") or env.get("BOT_CLIENT_ID") or ""
    secret = env.get("CASH_ZUMA_BOT_CLIENT_SECRET") or env.get("BOT_CLIENT_SECRET") or ""
    if not url:
        report.fail("UNREACHABLE", "Адрес сайта не задан: --url или CASH_ZUMA_API_URL")
    elif not url.startswith("https://") and not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
        report.fail("UNREACHABLE", "Адрес сайта должен начинаться с https://")
    if not client_id or not secret:
        report.fail("KEY_REJECTED", "Ключ бота не задан в окружении (CASH_ZUMA_BOT_CLIENT_ID / CASH_ZUMA_BOT_CLIENT_SECRET)")
    if report.verdict != "OK":
        report.line(f"Итог: {report.verdict}")
        return 2
    if len(secret) < 32:
        report.line("Внимание: секрет короче 32 символов — такой секрет сайт не принимает (ответит 503 «не настроен»)")
    report.line(f"Сайт: {url}")

    with httpx.Client(base_url=url, timeout=TIMEOUT, trust_env=False, transport=transport,
                      headers={"User-Agent": "cash-zuma-check-bot-api"}) as anonymous:
        response, error, took = get(anonymous, "/health")
        if response is None:
            report.fail(error.split(" ")[0], f"/health: {error} за {took:.1f} с — сайт не ответил")
            report.line(f"Итог: {report.verdict}")
            return 1
        report.line(f"/health: HTTP {response.status_code} за {took:.1f} с")
        response, error, took = get(anonymous, "/version.json")
        version = None
        if response is not None and response.status_code == 200:
            try:
                version = str(response.json().get("version", ""))
            except (ValueError, AttributeError):
                version = None
        if version:
            parsed = version_tuple(version)
            newer = parsed is not None and parsed >= REQUIRED_SITE
            report.line(f"/version.json: опубликована версия сайта {version} за {took:.1f} с"
                        + ("" if newer else f" — старше {'.'.join(map(str, REQUIRED_SITE))}, бот 0.2.x с ней несовместим"))
        else:
            report.line(f"/version.json: версия не прочитана ({error or ('HTTP ' + str(response.status_code))})")

    with httpx.Client(base_url=url, timeout=TIMEOUT, trust_env=False, transport=transport,
                      auth=(client_id, secret), headers={"User-Agent": "cash-zuma-check-bot-api"}) as bot:
        response, error, took = get(bot, "/api/bot/v1/report-groups")
        if response is None:
            report.fail(error.split(" ")[0], f"report-groups: {error} за {took:.1f} с")
            report.line(f"Итог: {report.verdict}")
            return 1
        if response.status_code != 200:
            verdict, text = classify(response)
            report.fail(verdict, f"report-groups: {text} ({took:.1f} с)")
            report.line(f"Итог: {report.verdict}")
            return 1
        try:
            groups = response.json().get("items")
        except (ValueError, AttributeError):
            groups = None
        if not isinstance(groups, list) or any(not isinstance(g, dict) or any(f not in g for f in GROUP_FIELDS) for g in groups):
            report.fail("INCOMPATIBLE_SCHEMA", f"report-groups: HTTP 200, но без полей {', '.join(GROUP_FIELDS)} ({took:.1f} с)")
        else:
            with_scope = sum(1 for g in groups if g.get("scope_id"))
            report.line(f"report-groups: HTTP 200 за {took:.1f} с, ключ принят; групп сводки {len(groups)}, "
                        f"с отпечатком области {with_scope}")

        response, error, took = get(bot, "/api/bot/v1/pending-requests")
        items: list[dict[str, Any]] = []
        if response is None:
            report.fail(error.split(" ")[0], f"pending-requests: {error} за {took:.1f} с")
        elif response.status_code != 200:
            verdict, text = classify(response)
            report.fail(verdict, f"pending-requests: {text} ({took:.1f} с)")
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            report.line(f"pending-requests: HTTP 200 за {took:.1f} с")
            items = check_items(payload.get("items") if isinstance(payload, dict) else None, report, "pending-requests")
            if not items and report.verdict == "OK":
                report.line("pending-requests: очередь пуста — сейчас нет заявок, ждущих действия от сотрудника с привязанным Telegram")
            if args.request:
                if not describe_request(items, args.request, report, "pending-requests"):
                    report.line("  Это не значит, что заявки нет в программе: в список не попадают черновики, оплаченные и "
                                "закрытые заявки, а также заявки, чей ответственный на текущем этапе не привязал Telegram")

        if args.telegram_user_id is not None:
            tid = args.telegram_user_id
            response, error, took = get(bot, f"/api/bot/v1/telegram-users/{tid}")
            if response is None:
                report.fail(error.split(" ")[0], f"telegram-users: {error} за {took:.1f} с")
            elif response.status_code != 200:
                verdict, text = classify(response)
                report.fail(verdict, f"telegram-users: {text} ({took:.1f} с)")
            else:
                try:
                    user = response.json()
                except ValueError:
                    user = {}
                linked = bool(user.get("linked")) if isinstance(user, dict) else False
                report.line(f"telegram-users: HTTP 200 за {took:.1f} с; привязан: {'да' if linked else 'нет'}; "
                            f"администратор: {'да' if user.get('is_admin') else 'нет'}")
                mine = check_items(user.get("items") if isinstance(user, dict) else None, report, "telegram-users.items") if linked else []
                if linked and args.request:
                    if not describe_request(mine, args.request, report, "личная очередь сотрудника"):
                        report.line("  Заявка не ждёт действия этого сотрудника: не его этап, он автор или редактор, "
                                    "либо у него нет роли в компании заявки")
            response, error, took = get(bot, "/api/bot/v1/access", {"telegram_user_id": str(tid)})
            if response is None:
                report.fail(error.split(" ")[0], f"access: {error} за {took:.1f} с")
            elif response.status_code != 200:
                verdict, text = classify(response)
                report.fail(verdict, f"access (сотрудник): {text} ({took:.1f} с)")
            else:
                try:
                    user = response.json().get("user") or {}
                except (ValueError, AttributeError):
                    user = {}
                companies = user.get("companies") if isinstance(user.get("companies"), list) else []
                report.line(f"access (сотрудник): HTTP 200 за {took:.1f} с; привязан: {'да' if user.get('linked') else 'нет'}; "
                            f"компании: {', '.join(map(str, companies)) or '—'}")

        if args.chat_id is not None:
            response, error, took = get(bot, "/api/bot/v1/access", {"chat_id": str(args.chat_id)})
            if response is None:
                report.fail(error.split(" ")[0], f"access (группа): {error} за {took:.1f} с")
            elif response.status_code != 200:
                verdict, text = classify(response)
                report.fail(verdict, f"access (группа): {text} ({took:.1f} с)")
            else:
                try:
                    group = response.json().get("group") or {}
                except (ValueError, AttributeError):
                    group = {}
                companies = group.get("companies") if isinstance(group.get("companies"), list) else []
                report.line(f"access (группа): HTTP 200 за {took:.1f} с; сводка разрешена: {'да' if group.get('allowed') else 'нет'}; "
                            f"компании: {', '.join(map(str, companies)) or '—'}; отпечаток области: "
                            f"{'есть' if group.get('scope_id') else 'нет'}")

    report.line(f"Итог: {report.verdict}")
    return 0 if report.verdict == "OK" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python deploy/check_bot_api.py",
                                     description="Проверка API бота на работающем сайте Cash Zuma (только чтение)")
    parser.add_argument("--url", help="адрес сайта, например https://cash-zuma.onrender.com (или CASH_ZUMA_API_URL)")
    parser.add_argument("--request", help="номер заявки, например CF-00005: есть ли она в очереди и у кого")
    parser.add_argument("--telegram-user-id", type=int, help="Telegram ID согласованного сотрудника: привязка, очередь, права")
    parser.add_argument("--chat-id", type=int, help="ID согласованной тестовой группы: разрешена ли ей сводка")
    return parser


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None, out: TextIO | None = None,
         transport: Any = None, parser: Callable[[], argparse.ArgumentParser] = build_parser) -> int:
    args = parser().parse_args(argv)
    return run(args, dict(os.environ if env is None else env), out or sys.stdout, transport)


if __name__ == "__main__":
    sys.exit(main())
