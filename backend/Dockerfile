FROM python:3.14.8-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY requirements.txt .
# Solo wheels y hashes verificados: cualquier paquete fuera del lockfile o alterado falla.
RUN pip install --require-hashes --only-binary=:all: -r requirements.txt

FROM base AS test
COPY requirements-dev.txt .
RUN pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
COPY --chown=10001:10001 . .
USER 10001
CMD ["pytest"]

FROM base AS runtime
COPY --chown=10001:10001 manage.py ./
COPY --chown=10001:10001 config ./config
COPY --chown=10001:10001 apps ./apps
USER 10001
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", \
     "--worker-tmp-dir", "/tmp", "--access-logfile", "-", "--error-logfile", "-"]
