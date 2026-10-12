# Argon benchmark dashboard: one static page behind a tiny Python server.
#
#   docker build -f dashboard/Dockerfile -t argon-dashboard .        (run from the repo root)
#   docker run --rm -e PORT=8080 -p 8080:8080 argon-dashboard
#
# Railway: this folder is picked up through railway.toml at the repo root; Railway sets $PORT itself.
FROM python:3.13-slim

RUN useradd --create-home app
WORKDIR /app
COPY dashboard/index.html dashboard/benchmarks.html dashboard/server.py dashboard/auth.py ./
USER app

ENV PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz', timeout=2)"
CMD ["python", "server.py"]
