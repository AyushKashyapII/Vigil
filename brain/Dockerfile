FROM python:3.12-slim

# pg_dump/psql/createdb/dropdb -- used by the sandbox to clone and tear
# down disposable databases. Shelled out to via subprocess rather than a
# Python driver, keeping brain's own dependencies at stdlib-only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY main.py .
COPY vigil_brain ./vigil_brain

CMD ["python", "main.py"]
