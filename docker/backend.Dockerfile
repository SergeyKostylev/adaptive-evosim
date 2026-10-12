# Backend: FastAPI + Uvicorn (PROJECT_SPEC.md §12)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Спершу лише метадані — шар із залежностями кешується між збірками
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN pip install --no-cache-dir .

COPY backend/tests ./tests

EXPOSE 8000
# Один uvicorn-воркер: паралелізм живе всередині evaluator (multiprocessing)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
