FROM python:3.14.8-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --chown=10001:10001 . /app
USER 10001
EXPOSE 8000
# TEMPORAL (el commit 3 lo reemplaza por gunicorn + Django)
CMD ["python", "stub_server.py"]
