FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 leadfinder \
    && mkdir -p /app/data /app/exports /app/.sessions \
    && chown -R leadfinder:leadfinder /app

USER leadfinder

EXPOSE 8000
CMD ["uvicorn", "leadfinder.api:app", "--host", "0.0.0.0", "--port", "8000"]
