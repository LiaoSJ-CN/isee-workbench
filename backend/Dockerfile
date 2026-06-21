FROM python:3.11-slim

WORKDIR /app

# psycopg2 needs libpq-dev at build time; keep libpq5 at runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create directories the app writes to at runtime and a non-root user.
# Running as root is a container-escape risk — even a minimal breakout
# yields full root inside the container (SEC-12).
RUN mkdir -p generated_reports logs static && \
    useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
