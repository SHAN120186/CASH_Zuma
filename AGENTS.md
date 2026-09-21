# Project delivery workflow

The user wants every completed update saved to GitHub and available for review.

- Preserve unrelated edits and financial data. Run the relevant tests and build the Vue frontend before delivery.
- Read `CASHFLOW_RULES_RU.md` before changing plans, imports, approvals, balances or reports. It distinguishes implemented rules from the still-undefined automatic scenario thresholds.
- Before editing shared work, fetch `origin`, integrate the current `origin/main` without discarding other agents' commits, and never create a competing repository for this product.
- Update `release.json` with a new version, date and concise Russian release notes.
- Commit the finished source, tests, documentation and `app/static/erp` build. Push the current branch to its existing upstream; never force-push or rewrite history.
- This repository is PUBLIC. Never stage credentials, config.env/config.local.env, databases, financial spreadsheets, backups, runtime logs or the private `design/` analysis. Check the staged file list and diff before committing.
- After a successful push, update a running temporary preview with `.venv-local/Scripts/python.exe deploy/preview.py --update`. It publishes the committed version and keeps the current URL. If no preview is running, use `START_WEB_SERVER.bat` when a temporary internet preview is requested.
- Verify `/health`, `/version.json` and the visible UI after publishing. Report the commit link, version and preview URL, and state any failed checks or unavailable services accurately.
- Preview URLs are temporary and work while this computer, the application and tunnel are running. Do not promise always-on hosting or publish financial data without login protection.
- Git commits and explicit preview updates are the delivery mechanism. Do not create scheduled tasks for this workflow unless requested.
