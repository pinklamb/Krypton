# The Krypton site (landing, docs, savings, "see your own tree") behind a tiny Python server with GitHub sign-in.
# A connected visitor's repository is fetched into memory with their own read-only GitHub access, parsed here into a syntax tree by
# `krypton` (the public client), and only the tree goes to the Krypton scoring server (KRYPTON_SCORE_URL, with KRYPTON_SERVICE_TOKEN).
# Nothing from the repository is written to disk, run or kept. The scoring maths is not in this image or this repository.
#
#   docker build -f dashboard/Dockerfile -t krypton-site .        (from the repo root)
#   docker run --rm -p 8080:8080 --env-file dashboard/.env krypton-site
#
# Railway: picked up through railway.toml at the repo root; Railway sets $PORT itself. Settings: dashboard/DEPLOY.md.
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
COPY dashboard/server.py dashboard/auth.py dashboard/repo_fetch.py dashboard/scanner.py dashboard/scan_worker.py ./
COPY site ./site
USER app

ENV PORT=8080 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 KRYPTON_BIN=/usr/local/bin/krypton SITE_DIR=/app/site
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz', timeout=2)"
CMD ["python", "server.py"]
