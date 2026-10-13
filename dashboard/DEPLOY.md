# Deploying the Krypton demo (Docker, Railway)

The image serves the demo page, the benchmark page, GitHub sign-in and repository analysis from one small Python server.

## 1. Register the GitHub OAuth App (once per environment)

GitHub → Settings → Developer settings → OAuth Apps → New OAuth App.

| Field | Value |
|---|---|
| Homepage URL | your public address, e.g. `https://krypton.up.railway.app` |
| Authorization callback URL | `https://krypton.up.railway.app/auth/github/callback` |

An OAuth App accepts one callback URL, so use a separate app for local testing (`http://localhost:8080/auth/github/callback`).
Create a client secret and keep it out of chat, commits and screenshots.

## 2. Variables (Railway → Variables, or a git-ignored `.env` locally)

| Variable | Value |
|---|---|
| `GITHUB_CLIENT_ID` | from the OAuth App |
| `GITHUB_CLIENT_SECRET` | from the OAuth App |
| `BASE_URL` | the public address from step 1, no trailing slash. Must be `https://…` in production (cookies are then marked Secure) |
| `SESSION_SECRET` | a long random string: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `GITHUB_SCOPES` | optional. Default `read:user repo` lets visitors analyze private repositories; `read:user public_repo` limits it to public ones |

`PORT` is set by Railway. Sessions are held in memory, so keep one instance (`numReplicas = 1` in `railway.toml`); a restart signs everyone out.

## 3. Deploy

Railway builds from the Git repository, so everything the image needs must be committed and pushed:
`dashboard/` (pages, `server.py`, `auth.py`, `repo_fetch.py`, `scanner.py`, `scan_worker.py`, `Dockerfile`), `argon/pyproject.toml`, `argon/argon/`, `railway.toml` and `.dockerignore`.

Locally, from the repository root:

```
docker build -f dashboard/Dockerfile -t krypton-dashboard .
docker run --rm -p 8080:8080 --env-file dashboard/.env krypton-dashboard
```

Check `/healthz` (should say `ok`) and `/api/session` (`"configured": true` once the variables are set).

## 4. What the analysis does with a repository

The server downloads the repository archive into memory using the visitor's own GitHub token, scores the Python, JavaScript and TypeScript files in a separate process, and returns numbers only. Nothing is written to disk or run, and nothing is stored. Limits: 40 MB download, 3,000 source files, 20 MB of source; two analyses at a time, one per visitor every 15 seconds.

## Tests

```
cd dashboard
python -m unittest test_scan test_oauth
```
