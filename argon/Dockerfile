# Argon: structural-complexity scoring for JS/TS/Python.
#
#   docker build -t argon .
#   docker run --rm -v "$PWD:/repo" argon .                    # scan the mounted repo
#   docker run --rm -v "$PWD:/repo" argon . --base origin/main --json
FROM python:3.13-slim

# git: --base diffs and .gitignore inheritance both shell out to it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

WORKDIR /build
COPY pyproject.toml ./
COPY argon ./argon
RUN pip install --no-cache-dir .

RUN useradd --create-home argon
USER argon
WORKDIR /repo

ENTRYPOINT ["argon"]
CMD ["."]
