FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Scans the packages installed above and pip-installs the matching
# OpenTelemetry instrumentation for each one it recognises (fastapi,
# sqlalchemy, psycopg2, requests, ...). Must run AFTER requirements are
# installed -- it only instruments what it can see.
RUN opentelemetry-bootstrap -a install

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini seed.py load.py ./

# opentelemetry-instrument sets up the SDK + exporters and patches the
# libraries BEFORE uvicorn imports app.main, which is why no code in app/
# changes. All config comes from OTEL_* env vars (see docker-compose.yml).
# Note: no --reload / --workers here -- both fork processes, and the SDK
# set up in the parent doesn't survive into the children cleanly.
CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
