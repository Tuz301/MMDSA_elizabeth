# The application image. One image, three roles: the web process, the Celery
# worker and the Celery beat scheduler, selected by CONTAINER_ROLE at run
# time, so the three processes can never drift onto different code.

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# psycopg ships binary wheels, so no compiler is needed; libpq5 covers the
# fallback path and curl serves the container health check.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The application never runs as root. Static files are collected at build
# time so the runtime filesystem can stay read-only.
RUN python manage.py collectstatic --noinput --settings=config.settings.base \
    && useradd --create-home --shell /usr/sbin/nologin mmdsa \
    && chown -R mmdsa:mmdsa /app
USER mmdsa

EXPOSE 8000

COPY --chown=mmdsa:mmdsa docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
