FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 DATA_DIR=/app/data
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt && useradd --uid 10001 --create-home zuma
COPY --chown=zuma:zuma app ./app
COPY --chown=zuma:zuma source ./source
COPY --chown=zuma:zuma manage.py settings_loader.py ./
COPY --chown=zuma:zuma deploy/entrypoint.sh ./entrypoint.sh
RUN mkdir -p /app/data /app/backups && chown -R zuma:zuma /app/data /app/backups && chmod 755 entrypoint.sh
USER zuma
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "172.29.113.3", "--no-access-log"]
