"""deploy/check_bot_api.py: the read-only check of the bot API tells the causes of a refusal apart
and prints neither the key nor the data of the site."""

import io
import json
import os
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy import check_bot_api as check  # noqa: E402

SECRET = "s3cr3t-value-of-the-bot-key-0123456789"
ENV = {"CASH_ZUMA_BOT_CLIENT_ID": "cash-zuma-bot", "CASH_ZUMA_BOT_CLIENT_SECRET": SECRET}
URL = "https://cash-zuma.example"

ITEM = {"request_id": 5, "number": "CF-00005", "status": "pending", "stage": "check",
        "stage_label": "Проверка реквизитов расчётным бухгалтером", "assignee_user_id": 7,
        "assignee_telegram_id": 910005, "stage_entered_at": "2026-10-02T04:00:00+00:00",
        "company": "Zuma", "company_code": "ZUMA", "amount": "1500000.00", "currency": "UZS",
        "purpose": "Оплата поставщику Secret LLC", "url": URL + "/#requests"}
GROUP = {"chat_id": -1001234567890, "title": "", "companies": ["ZUMA"], "source": "config", "scope_id": "3f1c9a7b2d4e6f80"}


def site(answers):
    """A fake site: path -> (status, json body) or a callable(request) -> Response."""
    def handler(request: httpx.Request) -> httpx.Response:
        answer = answers.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        if callable(answer):
            return answer(request)
        status, body = answer
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handler)


def run(answers, *argv, env=ENV):
    out = io.StringIO()
    code = check.main(["--url", URL, *argv], env=env, out=out, transport=site(answers))
    return code, out.getvalue()


HEALTHY = {"/health": (200, {"status": "ok"}), "/version.json": (200, {"version": "2.14.11"})}


class Verdicts(unittest.TestCase):
    def test_ok_with_a_compatible_site_and_a_waiting_request(self):
        answers = {**HEALTHY, "/api/bot/v1/report-groups": (200, {"items": [GROUP]}),
                   "/api/bot/v1/pending-requests": (200, {"items": [ITEM]})}
        seen = []

        def record(request):
            seen.append(request.headers.get("Authorization", ""))
            return httpx.Response(200, json={"items": [GROUP]})
        answers["/api/bot/v1/report-groups"] = record
        code, out = run(answers, "--request", "cf-00005")
        self.assertEqual(code, 0, out)
        self.assertIn("опубликована версия сайта 2.14.11", out)
        self.assertIn("ключ принят; групп сводки 1, с отпечатком области 1", out)
        self.assertIn("все обязательные поля на месте", out)
        self.assertIn("заявка CF-00005 — статус pending, этап check, компания ZUMA, ответственный user_id 7, его Telegram привязан: да", out)
        self.assertTrue(out.rstrip().endswith("Итог: OK"))
        self.assertTrue(seen and seen[0].startswith("Basic "))  # the key went to the site …
        for hidden in (SECRET, "1500000", "Secret LLC", "910005", "Basic "):
            self.assertNotIn(hidden, out)  # … and nowhere else

    def test_a_request_absent_from_the_queue_is_explained_not_denied(self):
        answers = {**HEALTHY, "/api/bot/v1/report-groups": (200, {"items": []}),
                   "/api/bot/v1/pending-requests": (200, {"items": []})}
        code, out = run(answers, "--request", "CF-00009")
        self.assertEqual(code, 0)
        self.assertIn("очередь пуста", out)
        self.assertIn("заявки CF-00009 в списке нет", out)
        self.assertIn("не значит, что заявки нет в программе", out)

    def test_key_not_configured_on_the_site(self):
        answers = {**HEALTHY, "/api/bot/v1/report-groups": (503, {"detail": "API бота на сайте не настроен."})}
        code, out = run(answers)
        self.assertEqual(code, 1)
        self.assertIn("не заданы BOT_CLIENT_ID / BOT_CLIENT_SECRET", out)
        self.assertTrue(out.rstrip().endswith("Итог: KEY_NOT_CONFIGURED"))

    def test_database_unavailable_is_not_a_missing_key(self):
        answers = {**HEALTHY, "/api/bot/v1/report-groups": (503, {"detail": "База данных временно недоступна. Повторите запрос; не создавайте дубликат документа."})}
        code, out = run(answers)
        self.assertEqual(code, 1)
        self.assertTrue(out.rstrip().endswith("Итог: DATABASE_UNAVAILABLE"))
        self.assertNotIn("KEY_NOT_CONFIGURED", out)

    def test_wrong_key_throttle_and_missing_route(self):
        for status, body, verdict in (
            (401, {"detail": "Неверный ключ бота."}, "KEY_REJECTED"),
            (429, {"detail": "Слишком много попыток. Повторите через 10 минут."}, "THROTTLED"),
            (404, {"detail": "Not Found"}, "ROUTE_MISSING"),
            (502, {}, "SERVER_ERROR"),
        ):
            with self.subTest(status=status):
                answers = {**HEALTHY, "/api/bot/v1/report-groups": (status, body)}
                code, out = run(answers)
                self.assertEqual(code, 1)
                self.assertTrue(out.rstrip().endswith("Итог: " + verdict), out)

    def test_an_old_site_without_company_code_is_incompatible_not_empty(self):
        old = {k: v for k, v in ITEM.items() if k != "company_code"}
        answers = {"/health": (200, {"status": "ok"}), "/version.json": (200, {"version": "2.14.6"}),
                   "/api/bot/v1/report-groups": (200, {"items": [GROUP]}),
                   "/api/bot/v1/pending-requests": (200, {"items": [old]})}
        code, out = run(answers, "--request", "CF-00005")
        self.assertEqual(code, 1)
        self.assertIn("старше 2.14.10", out)
        self.assertIn("нет обязательных полей: company_code", out)
        self.assertNotIn("очередь пуста", out)
        self.assertTrue(out.rstrip().endswith("Итог: INCOMPATIBLE_SCHEMA"))

    def test_html_from_a_waking_instance_is_incompatible_schema_not_ok(self):
        answers = {**HEALTHY, "/api/bot/v1/report-groups": lambda r: httpx.Response(200, text="<html>starting</html>")}
        code, out = run(answers)
        self.assertEqual(code, 1)
        self.assertTrue(out.rstrip().endswith("Итог: INCOMPATIBLE_SCHEMA"))

    def test_timeout_and_no_connection(self):
        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        def down(request):
            raise httpx.ConnectError("refused", request=request)
        code, out = run({"/health": slow})
        self.assertEqual(code, 1)
        self.assertIn("/health: TIMEOUT", out)
        self.assertTrue(out.rstrip().endswith("Итог: TIMEOUT"))
        code, out = run({"/health": down})
        self.assertTrue(out.rstrip().endswith("Итог: UNREACHABLE"))

    def test_person_and_group_checks(self):
        answers = {**HEALTHY, "/api/bot/v1/report-groups": (200, {"items": [GROUP]}),
                   "/api/bot/v1/pending-requests": (200, {"items": [ITEM]}),
                   "/api/bot/v1/telegram-users/910003": (200, {"linked": True, "user_id": 3, "name": "Иван Секретов", "is_admin": False, "items": []}),
                   "/api/bot/v1/access": lambda r: httpx.Response(200, json=(
                       {"checked_at": "x", "group": {"chat_id": -1001234567890, "allowed": True, "companies": ["ZUMA"], "scope_id": "3f1c9a7b2d4e6f80"}}
                       if "chat_id" in r.url.params else
                       {"checked_at": "x", "user": {"linked": True, "user_id": 3, "is_admin": False, "companies": ["ZUMA"]}}))}
        code, out = run(answers, "--request", "CF-00005", "--telegram-user-id", "910003", "--chat-id", "-1001234567890")
        self.assertEqual(code, 0, out)
        self.assertIn("telegram-users: HTTP 200", out)
        self.assertIn("личная очередь сотрудника: заявки CF-00005 в списке нет", out)
        self.assertIn("не ждёт действия этого сотрудника", out)
        self.assertIn("access (сотрудник): HTTP 200", out)
        self.assertIn("компании: ZUMA", out)
        self.assertIn("access (группа): HTTP 200", out)
        self.assertIn("сводка разрешена: да", out)
        self.assertNotIn("Иван", out)

    def test_missing_settings_are_refused_before_any_call(self):
        code, out = run({}, env={})
        self.assertEqual(code, 2)
        self.assertIn("Ключ бота не задан", out)
        out2 = io.StringIO()
        code = check.main([], env=ENV, out=out2, transport=site({}))
        self.assertEqual(code, 2)
        self.assertIn("Адрес сайта не задан", out2.getvalue())

    def test_the_key_is_never_taken_from_the_command_line(self):
        parser = check.build_parser()
        self.assertFalse(any("secret" in a.dest or "client" in a.dest for a in parser._actions))


class RealModule(unittest.TestCase):
    def test_script_help_runs(self):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        import subprocess
        done = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "deploy" / "check_bot_api.py"), "--help"],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--request", done.stdout)


if __name__ == "__main__":
    unittest.main()
