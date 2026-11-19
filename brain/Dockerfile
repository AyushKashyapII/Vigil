FROM python:3.12-slim

# pg_dump/psql/createdb/dropdb -- used by the sandbox to clone and tear
# down disposable databases. Shelled out to via subprocess rather than a
# Python driver, keeping brain's own dependencies (nearly) stdlib-only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The only pip dependencies: OpenTelemetry SDK + OTLP exporter (see
# pyproject.toml). Everything brain does is still stdlib + CLI tools;
# OTel is how brain is observed, and telemetry.setup() degrades to no-ops
# if it's missing.
RUN pip install --no-cache-dir "opentelemetry-sdk>=1.30" "opentelemetry-exporter-otlp-proto-grpc>=1.30"

COPY main.py .
COPY vigil_brain ./vigil_brain

CMD ["python", "main.py"]
