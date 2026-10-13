# Krypton demo and benchmark pages behind a tiny Python server, with GitHub sign-in and repository analysis.
#
#   docker build -f dashboard/Dockerfile -t krypton-dashboard .        (run from the repo root)
#   docker run --rm -e PORT=8080 -p 8080:8080 krypton-dashboard
#
# Railway: picked up through railway.toml at the repo root; Railway sets $PORT itself.
# Sign-in needs GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, BASE_URL and SESSION_SECRET (see dashboard/DEPLOY.md).
FROM python:3.13-slim

RUN useradd --create-home app
WORKDIR /app
# the analysis engine, installed from this repository (the sign-in demo analyzes visitors' repositories with it)
COPY krypton/pyproject.toml /build/krypton/pyproject.toml
COPY krypton/krypton /build/krypton/krypton
RUN pip install --no-cache-dir /build/krypton && rm -rf /build

COPY dashboard/index.html dashboard/benchmarks.html dashboard/server.py dashboard/auth.py dashboard/repo_fetch.py dashboard/scanner.py dashboard/scan_worker.py ./
USER app

ENV PORT=8080 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz', timeout=2)"
CMD ["python", "server.py"]
