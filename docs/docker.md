# Running Lead desk in Docker

One container runs everything: the dashboard, the API, the Meta webhooks and
the background worker (replies, follow-ups, alerts, the lead mirror, daily
backups). Your data lives in a Docker volume, so stopping, updating or
rebuilding the container never loses it.

Without Docker everything still works as described in `docs/RUNLOG.md`;
Docker is the simpler way to run it on an always-on machine.

## What you need

- **Windows:** Docker Desktop (WSL 2 backend), 4 GB of free memory.
- **Linux server:** Docker Engine with the Compose plugin.
- Your `backend/.env` from the earlier weeks. Docker uses the same file.

## First start

1. In `backend/.env`, set a long random `JWT_SECRET`; the container refuses to
   start with the default one. Generate one with
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. If this is a fresh installation with no account yet, also add these to
   `backend/.env`:
   ```
   INITIAL_USER_EMAIL=you@example.com
   INITIAL_USER_PASSWORD=a-long-password
   INITIAL_BUSINESS_NAME=Sharma Kitchen Store
   ```
   On the first start the account is created and the catalog
   (`CATALOG_PATH`, the sample by default) is loaded. Both happen only once.
3. From the project folder (where `docker-compose.yml` is):
   ```powershell
   docker compose up -d --build
   ```
   The first build takes a few minutes: it installs Python packages and builds
   the dashboard.
4. Open http://127.0.0.1:8000 and sign in.

Check that it is healthy:
```powershell
docker compose ps          # STATUS should say (healthy)
docker compose logs app    # start-up messages start with [bootstrap]
```

## Moving the data you already have into Docker

If you ran Weeks 1-5 without Docker, your leads are in `backend\data\app.db`. The container
starts with an empty database in its own volume. To bring your data along (tested in Week 6):

1. Stop the server you started with `uvicorn` (Ctrl+C). Then, in the `backend` folder with the
   venv active, make a clean copy:
   ```powershell
   python -m scripts.backup now        # prints: Backed up to ...\data\backups\app-20261022-103405.db
   ```
2. Leave `INITIAL_USER_EMAIL` / `INITIAL_USER_PASSWORD` out of `.env`; your existing account
   comes with the data. From the project folder, start once so the volume exists, then stop:
   ```powershell
   docker compose up -d --build
   docker compose stop app
   ```
3. Copy the backup in. Use the file name printed in step 1:
   ```powershell
   docker compose run --rm --no-deps --user root -v ./backend/data/backups:/old:ro --entrypoint sh app -c "cp /old/app-20261022-103405.db /data/app.db && rm -f /data/app.db-wal /data/app.db-shm && chown 10001:10001 /data/app.db"
   docker compose start app
   ```
4. Sign in with your usual account. Data from an earlier week is upgraded to this week's version
   on start; `docker compose logs app` then shows `[bootstrap] database is up to date`.

From then on, always start the app with Docker. Running `uvicorn` as well would work on the old
file, not on the one in the volume.

## Everyday commands

| What | Command |
|---|---|
| Start / stop | `docker compose up -d` / `docker compose down` (data is kept) |
| Watch the logs | `docker compose logs -f app` |
| Update after `git pull` | `docker compose up -d --build` (migrations run on start) |
| Demo conversations | `docker compose exec app python -m scripts.simulate demo` |
| Follow-ups planned | `docker compose exec app python -m scripts.nurture` |
| Create another account | `docker compose exec app python -m scripts.create_user --email ravi@example.com` |
| Evaluation | `docker compose exec app python -m scripts.evaluate` |
| Back up now / list | `docker compose exec app python -m scripts.backup` / `... backup list` |
| Shell inside | `docker compose exec app sh` |

Every script from the run log works the same way after
`docker compose exec app`.

## Where things are

| Inside the container | On your computer | What |
|---|---|---|
| `/data/app.db` | volume `leadgen_leadgen-data` | the database |
| `/data/backups/` | same volume | daily backups (newest 14) |
| `/exports/leads_mirror.csv` | `exports\leads_mirror.csv` in the project folder | the lead mirror, opens in Excel |
| `/app/backend/secrets/` | `backend\secrets\` (read-only) | Google service-account key, if you use Sheets |

Copy a backup to your computer:
```powershell
docker compose exec app python -m scripts.backup list
docker compose cp app:/data/backups/app-20261022-100037.db .
```

Restore a backup (the current database is kept as `app.db.before-restore-...`):
```powershell
docker compose stop app
docker compose run --rm app python -m scripts.backup restore app-20261022-100037.db --yes
docker compose start app
```

Load your own catalog file:
```powershell
docker compose cp my_catalog.json app:/data/catalog.json
docker compose exec app python -m scripts.load_catalog --file /data/catalog.json
```

## Settings in Docker

The container reads `backend/.env`. Compose overrides only what must differ
inside a container:
- `DATABASE_URL`, `BACKUP_DIR`, `FRONTEND_DIST`, `SHEETS_CSV_PATH` point into
  the container.
- `OLLAMA_BASE_URL` points to Ollama on your computer
  (`http://host.docker.internal:11434`), because `localhost` inside a
  container is the container itself.

Everything else (Meta tokens, templates, alerts, follow-up times) comes from
your `.env` unchanged. After changing `.env`, run `docker compose up -d` to
apply it.

## Ollama

- **On your computer (recommended on Windows, uses your GPU):** nothing to do.
  The container reaches it through `host.docker.internal`.
- **In a container instead:**
  ```powershell
  docker compose -f docker-compose.yml -f docker-compose.ollama.yml up -d --build
  docker compose -f docker-compose.yml -f docker-compose.ollama.yml exec ollama ollama pull llama3.1:8b
  ```
  For an NVIDIA GPU, uncomment the `deploy` block in
  `docker-compose.ollama.yml`.

## Webhooks from Meta (ngrok)

Nothing changes: ngrok on your computer forwards to port 8000, which the
container publishes:
```powershell
ngrok http --url=<your-static-domain> 8000
```

## Opening the dashboard from your phone

The port is published on `127.0.0.1` only, so the dashboard is not open to
your network. To reach it from a phone on the same Wi-Fi, change `ports` in
`docker-compose.yml` to `"8000:8000"`, run `docker compose up -d`, and open
`http://<your PC's IP>:8000`. Through your ngrok domain it works from anywhere
(sign-in required).

## On a Linux server

- The container runs as user 10001, not root. Docker creates `./exports` owned
  by root, so the lead mirror cannot be written there. The start-up log says
  so; fix it once with:
  ```bash
  sudo chown -R 10001:10001 exports
  ```
  Docker Desktop on Windows and macOS does not need this.
- Keep port 8000 behind ngrok or a reverse proxy with HTTPS. Never publish it
  without TLS.

## Only one container

The background worker runs inside the server process. Run exactly one `app`
container: never `--scale app=2`, and no second copy against the same volume.
Several workers would not double-send (every message has a unique key), but
nothing is gained from them. A queue-based split is in the next-phase plan.

## Behind a company proxy or registry mirror

The base images are build arguments. To use a mirror, put this in a `.env`
file next to `docker-compose.yml` (not `backend/.env`):
```
NODE_IMAGE=my-registry.example.com/library/node:24-slim
PYTHON_IMAGE=my-registry.example.com/library/python:3.12-slim
```
If your network re-signs HTTPS traffic, the base images must trust your
company certificate for `pip install` and `npm ci` to work.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Container exits at once; log says `JWT_SECRET is missing, the default or shorter than 32 characters` | Set a long random `JWT_SECRET` in `backend/.env` (First start, step 1) |
| `env file backend/.env not found` | Copy `backend/.env.example` to `backend/.env` and fill it in |
| Sign-in fails on a fresh install; log says `no account yet` | Add `INITIAL_USER_EMAIL` / `INITIAL_USER_PASSWORD` and run `docker compose up -d`, or `docker compose exec app python -m scripts.create_user --email ...` |
| Replies are always the catalog list | Ollama is not reachable: is it running on your PC? `docker compose exec app python -m scripts.llm_smoke` |
| `exports\leads_mirror.csv` never appears | Linux host: the chown above. Windows: is it open in Excel? It refreshes within a minute after closing |
| Port 8000 already in use | Stop the non-Docker server (`uvicorn`) first, or change the left side of `ports` (e.g. `"127.0.0.1:8080:8000"`) |
| Build fails at `npm ci` or `pip install` with certificate errors | A proxy re-signs HTTPS; see "Behind a company proxy" |
| Webhooks from Meta are rejected (403) | `META_APP_SECRET` in `backend/.env` must be your app's secret; then `docker compose up -d` |
