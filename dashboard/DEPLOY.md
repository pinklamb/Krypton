# Deploying Krypton (Docker, Railway)

Two services:

- **The scoring server** (private repository): scores trees, and since login is required, issues and checks Krypton tokens.
- **The site** (this folder): serves the landing page, the docs, the savings screen and "see your own tree", with GitHub sign-in. A connected visitor's repository is read into memory with their read-only access, turned into a tree here, and only the tree goes to the scoring server. Nothing is kept.

## 1. The scoring server

Variables on its Railway service:

| Variable | Value |
|---|---|
| `KRYPTON_JWT_SECRET` | 32 or more random bytes, for example `python -c "import secrets; print(secrets.token_hex(32))"`. **Required: without it the server does not start.** |
| `KRYPTON_GITHUB_CLIENT_ID` | the GitHub App's client id (step 2), so `krypton login` knows which app to log in with |

Then mint the site's service token once, anywhere `KRYPTON_JWT_SECRET` is set (for example a one-off Railway shell):

```
krypton-server --mint website 365
```

Keep the printed token for step 3. Changing `KRYPTON_JWT_SECRET` later signs everyone out, the site included, until you mint a new one.

## 2. Register the GitHub App (once per environment)

GitHub → Settings → Developer settings → **GitHub Apps** → New GitHub App.

| Field | Value |
|---|---|
| Homepage URL | your site's public address, e.g. `https://krypton.up.railway.app` |
| Callback URL | `https://krypton.up.railway.app/auth/github/callback` |
| Request user authorization (OAuth) during installation | on |
| Enable Device Flow | **on** (this is what `krypton login` uses in the terminal) |
| Webhook | off |
| Repository permissions | **Contents: Read-only**, Metadata: Read-only. Nothing else. |
| Where can this app be installed | Any account |

Create a client secret and keep it out of chat, commits and screenshots. Note the app's slug (the last part of `github.com/apps/<slug>`).

The permissions are what make "read-only" true: a visitor's token can read the repositories they chose, and nothing else. They choose which repositories when they install the app (the site's "Choose repositories" link).

## 3. The site

Variables on its Railway service (or a git-ignored `dashboard/.env` locally, see `.env.example`):

| Variable | Value |
|---|---|
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | from the GitHub App |
| `GITHUB_APP_SLUG` | the app's slug, for the "Choose repositories" link |
| `BASE_URL` | the site's public address, no trailing slash, `https://…` in production (cookies are then Secure) |
| `SESSION_SECRET` | a long random string |
| `KRYPTON_SCORE_URL` | the scoring server's address; on Railway, its private address `http://<service>.railway.internal:8080` keeps the demo's traffic inside the project |
| `KRYPTON_SERVICE_TOKEN` | the token minted in step 1 |

Sessions are held in memory, so keep one instance (`numReplicas = 1` in `railway.toml`). The deploy log prints `GitHub sign-in: on; scoring: on` once both are set.

Locally, from the repository root:

```
docker build -f dashboard/Dockerfile -t krypton-site .
docker run --rm -p 8080:8080 --env-file dashboard/.env krypton-site
```

Railway builds from the Git repository, so `site/`, `client/` (built inside the image), `dashboard/server.py`, `auth.py`, `repo_fetch.py`, `scanner.py`, `scan_worker.py`, `Dockerfile`, `railway.toml` and `.dockerignore` must be committed and pushed.

## 4. What happens to a visitor's repository

1. The visitor connects GitHub (read-only: Contents and Metadata) and picks a repository they gave the app.
2. The site downloads the repository's archive into memory with their token, keeps only the Python, JavaScript and TypeScript files (40 MB download, 3,000 files, 20 MB of source), and has `krypton` parse them into a tree in a separate process. Nothing is written to disk or run.
3. Only the tree goes to the scoring server, with the site's service token. The page gets numbers back: purity, files, lines, the worst functions by name, and an outline of the tree to draw.
4. Two analyses run at a time, one per visitor every 15 seconds. Sign-out revokes the GitHub token with GitHub; the site forgets every session after an hour.

## 5. The GitHub Action and `krypton login`

- People log in once with `krypton login` (or the first `krypton setup`): a short code they approve at github.com/login/device.
- The Action logs in as the repository with the run's own identity: workflows need `permissions: id-token: write`, and no secret.

## Tests

```
cd dashboard
python -m unittest test_oauth test_scan
```
