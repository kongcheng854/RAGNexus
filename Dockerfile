FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv/ragnexus

COPY pyproject.toml .
COPY app ./app
RUN pip install --no-cache-dir ".[parsers]"

COPY alembic.ini .
COPY migrations ./migrations
COPY scripts ./scripts

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
