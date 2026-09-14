# Проверка первой поставки ZUMA Cash Flow

Проверено в среде сборки: Python 3.13.5, SQLite + FastAPI TestClient. Ниже журнал реального запуска. Это функциональные проверки, не независимый аудит безопасности.

## Серверные сценарии

```text
test_01_login_and_protected_api (__main__.SiteTests.test_01_login_and_protected_api) ... ok
test_02_csrf_and_cross_origin (__main__.SiteTests.test_02_csrf_and_cross_origin) ... ok
test_03_soft_budget_needs_reason (__main__.SiteTests.test_03_soft_budget_needs_reason) ... ok
test_04_hard_budget_blocks (__main__.SiteTests.test_04_hard_budget_blocks) ... ok
test_05_payment_no_double_count (__main__.SiteTests.test_05_payment_no_double_count) ... ok
test_06_duplicate_reference_rollback (__main__.SiteTests.test_06_duplicate_reference_rollback) ... ok
test_07_transfer_excluded_from_cashflow (__main__.SiteTests.test_07_transfer_excluded_from_cashflow) ... ok
test_08_receipt_forecast_and_fact (__main__.SiteTests.test_08_receipt_forecast_and_fact) ... ok
test_09_employee_permissions_and_privacy (__main__.SiteTests.test_09_employee_permissions_and_privacy) ... ok
test_10_director_cannot_approve_own (__main__.SiteTests.test_10_director_cannot_approve_own) ... ok
test_11_dates_negative_and_insufficient (__main__.SiteTests.test_11_dates_negative_and_insufficient) ... ok
test_12_currency_separation (__main__.SiteTests.test_12_currency_separation) ... ok
test_13_storno_reopens_request (__main__.SiteTests.test_13_storno_reopens_request) ... ok
test_14_excel_snapshot_isolated_and_idempotent (__main__.SiteTests.test_14_excel_snapshot_isolated_and_idempotent) ... ok
test_15_revoked_session_and_logout (__main__.SiteTests.test_15_revoked_session_and_logout) ... ok
test_16_login_rate_limit (__main__.SiteTests.test_16_login_rate_limit) ... ok
test_17_reschedule_removes_approval (__main__.SiteTests.test_17_reschedule_removes_approval) ... ok
test_18_exact_minor_units_and_csv (__main__.SiteTests.test_18_exact_minor_units_and_csv) ... ok

----------------------------------------------------------------------
Ran 18 tests in 16.812s

OK
```

## Интерфейс

Chromium: desktop 1440×1000 и mobile 390×844. HTML/CSS/JS отрисованы офлайн, обращения интерфейса передавались реальному FastAPI TestClient в изолированной БД. Сетевой проход браузер → опубликованный HTTPS-сайт не проверен. Ошибок JavaScript: 0.

Открыты разделы: обзор, календарь, заявки, счета, операции, лимиты, FinModel, отчёт, пользователи, аудит, профиль. На проверенных мобильных страницах внешняя ширина документа равна 390 px; широкие таблицы прокручиваются внутри.

Через реальные формы интерфейса выполнены: создание счёта с тестовым остатком 1000.00 → заявка 100.25 → утверждение → подтверждение оплаты. Проверенный остаток 899.75. Это ИЗОЛИРОВАННЫЕ тестовые данные, не сведения ZUMA; в поставку эти счета/пользователи/пароли/БД не включены.

JavaScript: `node --check app/static/app.js` — успешно. Python: `compileall` — успешно.

## Не проверено здесь

Windows BAT на Windows, установка библиотек на вашем компьютере, фактическая сеть телефона, Docker, PostgreSQL, Caddy/HTTPS, сервисный аккаунт Google Drive, нагрузка, восстановление после аппаратного сбоя, полный независимый аудит безопасности. Конфигурации предоставлены для администраторской настройки и приёмки, не как доказательство этих проверок.

## Состав и конфиденциальность

В поставке нет готовых пользователей, паролей, ключей, сессий и рабочей БД. При первом запуске они создаются на машине владельца. Приложена реальная копия FinModel, поэтому весь архив конфиденциальный. Исходный XLSX не изменялся.
