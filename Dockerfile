FROM node:24-bookworm-slim AS frontend
WORKDIR /frontend
RUN npm install -g pnpm@11.19.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
COPY release.json /release.json
RUN pnpm run build

FROM postgres:17-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 DATA_DIR=/app/data BACKUP_DIR=/app/backups
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt && useradd --uid 10001 --create-home zuma
COPY --chown=zuma:zuma app ./app
COPY --from=frontend --chown=zuma:zuma /app/static/erp ./app/static/erp
COPY --chown=zuma:zuma manage.py settings_loader.py worker.py release.json ./
COPY --chown=zuma:zuma deploy/entrypoint.sh ./entrypoint.sh
COPY --chown=zuma:zuma deploy/render_start.py ./deploy/render_start.py
RUN mkdir -p /app/data /app/backups && chown -R zuma:zuma /app/data /app/backups && chmod 755 entrypoint.sh
USER zuma
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "172.29.113.3", "--no-access-log"]
