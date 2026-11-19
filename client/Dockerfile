# The Krypton client as a local Docker image: nothing to install but Docker.
#
#   docker build -t krypton .                                        (once, from this folder)
#   docker run -it --rm -p 127.0.0.1:7777:7777 -v "$PWD:/repo" krypton
#                                                    (once per repository: setup asks its questions here, or gives a link to a local settings page)
#   docker run --rm -v "$PWD:/repo" krypton scan .     (from the repository you want scored)
#
# The program is the image's entry point, so everything after `krypton` is a normal command (scan, review, restore, order, ...).
# git is in the image because --base, order and --log read your repository's history.
# Krypton is the container's first process, which ignores Ctrl+C as a signal, so during a wait it reads Ctrl+C and Esc as keys itself
# (src/cancel.rs); that needs `docker run -it`, as the krypton helper does.
FROM rust:1-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'
COPY --from=build /src/target/release/krypton /usr/local/bin/krypton
WORKDIR /repo
# the settings page of `krypton setup` listens on every interface inside the container; publish it as -p 127.0.0.1:7777:7777 so only this computer reaches it
ENV KRYPTON_LISTEN=0.0.0.0:7777
# the Krypton token from `krypton login` lives here; the README's helper keeps it in the krypton-home volume so it survives each run
ENV KRYPTON_HOME=/krypton-home
EXPOSE 7777
ENTRYPOINT ["krypton"]
