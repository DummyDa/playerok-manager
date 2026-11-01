FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 TZ=Europe/Volgograd
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt
RUN python -c "from tls_requests import Client; c=Client(); c.close()"
RUN python -c "import certifi,importlib.util,pathlib,shutil; p=pathlib.Path(next(iter(importlib.util.find_spec('playerokapi').submodule_search_locations)))/'cacert.pem'; shutil.copyfile(certifi.where(),p) if not p.exists() else None"
COPY backend ./backend
COPY catalog ./catalog
COPY scripts ./scripts
COPY alembic.ini ./
RUN mkdir -p data/logs catalog/data/images && chown -R 10001:10001 data catalog/data
USER 10001:10001
RUN python -c "from tls_requests import Client; c=Client(); c.close()"
CMD ["python", "scripts/run_api.py"]
