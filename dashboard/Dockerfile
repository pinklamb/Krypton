# Krypton demo and benchmark pages behind a tiny Python server, with GitHub sign-in and repository analysis.
# Repositories are fetched into memory with the visitor's own GitHub access, parsed here into a syntax tree by `krypton` (the public client),
# and only the tree is sent to the Krypton scoring server (KRYPTON_SCORE_URL). The scoring math is not in this image or this repository.
#
#   docker build -f dashboard/Dockerfile -t krypton-dashboard .        (run from the repo root)
#   docker run --rm -e PORT=8080 -e KRYPTON_SCORE_URL=https://YOUR-SERVER -p 8080:8080 krypton-dashboard
#
# Railway: picked up through railway.toml at the repo root; Railway sets $PORT itself.
# Sign-in needs GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, BASE_URL and SESSION_SECRET (see dashboard/DEPLOY.md). GitHub does the authenticating.
#
# The client is built on bookworm so it matches the runtime image's glibc. No Rust source or compiler reaches the final image.
FROM rust:1-bookworm AS build
WORKDIR /src
COPY client/Cargo.toml client/Cargo.lock ./
COPY client/src ./src
RUN cargo build --release --locked

FROM python:3.13-slim

RUN useradd --create-home app
WORKDIR /app
COPY --from=build /src/target/release/krypton /usr/local/bin/krypton

COPY dashboard/index.html dashboard/benchmarks.html dashboard/security.html dashboard/server.py dashboard/auth.py dashboard/repo_fetch.py dashboard/scanner.py dashboard/scan_worker.py ./
USER app

ENV PORT=8080 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 KRYPTON_BIN=/usr/local/bin/krypton
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz', timeout=2)"
CMD ["python", "server.py"]
