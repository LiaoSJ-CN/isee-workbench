FROM python:3.11-slim

WORKDIR /app

# Runtime + build dependencies:
#   - libpq-dev / gcc      : psycopg2-binary build (kept for backwards compat)
#   - libpango / libcairo  : weasyprint PDF rendering (批 8.1)
#   - libgdk-pixbuf        : weasyprint image handling
#   - fonts-noto-cjk       : CJK glyph coverage in generated PDFs
#
# CJK fonts are intentionally pulled at image build time so production
# PDFs render Chinese characters without per-deploy font setup.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libpq-dev gcc \
      libpango-1.0-0 libpangoft2-1.0-0 \
      libcairo2 \
      libgdk-pixbuf-2.0-0 \
      fonts-noto-cjk && \
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
