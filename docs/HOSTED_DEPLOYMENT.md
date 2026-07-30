# Hosted deployment

Phase 3 packages Saville Music Persona Web as one production container. The container serves the compiled React application and `/api` from the same origin, runs exactly one FastAPI process, and stores SQLite state under `/var/lib/saville`.

## Why one container and one process

The current import coordinators use persistent SQLite job records plus in-process worker threads. Multiple Uvicorn workers or multiple container replicas could accept two jobs for the same session and would not share their in-memory capacity controls. Phase 3 therefore uses this topology:

```mermaid
flowchart LR
  Browser["Anonymous browser"] --> HTTPS["HTTPS host / reverse proxy"]
  HTTPS --> App["One Saville container"]
  App --> Static["Bundled React files"]
  App --> API["FastAPI request thread"]
  API --> Worker["Bounded import worker threads"]
  API --> SQLite["SQLite WAL database"]
  Worker --> SQLite
  SQLite --> Volume["Persistent /var/lib/saville volume"]
```

This is intentionally small and appropriate for friend-group testing. Do not enable platform autoscaling, multiple replicas, Gunicorn workers, or `WEB_CONCURRENCY` above `1`. A later distributed-worker architecture would require a shared queue and a database designed for multiple application instances.

## Build and test locally

Docker Desktop or another Docker-compatible runtime is required.

```powershell
docker compose up --build
```

Open `http://localhost:8000`. The Compose profile sets `SMP_SESSION_COOKIE_SECURE=false` because local HTTP cannot set a Secure cookie. It uses the named volume `saville-data`, so restarting the container does not discard active sessions or the reusable metadata cache.

Stop it with:

```powershell
docker compose down
```

Do not add `--volumes` unless you intentionally want to erase the hosted test database.

## Deploy the container

Any host that accepts an OCI/Docker image can run this edition. The GitHub Actions workflow verifies the backend, frontend, and production container on every push. A successful `main` build publishes:

```text
ghcr.io/aidanchan0623/saville-music-persona-web:latest
```

If a hosting provider cannot pull the image, make the GHCR package public in its package settings or configure that provider with a GitHub package-read token. A public source repository does not guarantee that every newly created container package is public.

Configure the host as follows:

- Container port: `8000`
- Health/readiness path: `/api/ready`
- Replica count: exactly `1`
- Persistent mount: `/var/lib/saville`
- Public URL: HTTPS
- Start command: use the image default command

Minimum environment:

```text
SMP_DEPLOYMENT_MODE=anonymous
SMP_SERVE_FRONTEND=true
SMP_DATA_DIR=/var/lib/saville
SMP_SESSION_COOKIE_SECURE=true
SMP_SESSION_COOKIE_SAMESITE=lax
WEB_CONCURRENCY=1
```

Because the frontend and API are served from one domain, `SameSite=lax` is sufficient and no cross-site API URL is needed. Keep `VITE_API_BASE_URL=/api`, which is already baked into the production image.

Useful resource settings:

```text
SMP_SESSION_TTL_HOURS=24
SMP_SESSION_CLEANUP_INTERVAL_SECONDS=300
SMP_ANONYMOUS_MAX_UPLOAD_BYTES=104857600
SMP_ANONYMOUS_MAX_EVENTS=250000
SMP_ANONYMOUS_UPLOADS_PER_HOUR=4
SMP_ANONYMOUS_MAX_CONCURRENT_IMPORTS=2
SMP_ACCESS_LOG=false
```

## Phase 4 optional hosted report writer

All analytics remain local to Saville's backend and deterministic. The optional writer receives only compact, derived report evidence needed to rewrite six bounded text fields: the selected personality label, strongest calculated signals, known artist/genre allow-lists, and Musical Age resolution state. It does not receive the uploaded archive, listening-event rows, timestamps, source URLs, account identifiers, or browser cookie. Provider output is strict-JSON validated against the supplied allow-lists before use.

Configure these values as hosting secrets/environment variables, never in source control:

```text
SMP_HOSTED_LLM_PROVIDER=openai-compatible
SMP_HOSTED_LLM_BASE_URL=https://api.openai.com/v1
SMP_HOSTED_LLM_API_KEY=your-server-side-secret
SMP_HOSTED_LLM_MODEL=your-compatible-chat-model
SMP_HOSTED_LLM_TIMEOUT_SECONDS=20
SMP_HOSTED_LLM_MAX_OUTPUT_TOKENS=700
SMP_HOSTED_LLM_REQUESTS_PER_SESSION_HOUR=4
SMP_HOSTED_LLM_REQUESTS_GLOBAL_DAY=200
SMP_REPORT_GENERATION_TIMEOUT_SECONDS=35
SMP_ANONYMOUS_MAX_CONCURRENT_REPORTS=2
```

Leaving `SMP_HOSTED_LLM_PROVIDER=disabled`, the key blank, or the model blank makes no remote call. Provider errors, invalid JSON, timeouts, quota exhaustion, and service restarts fail closed to Saville's deterministic writer. `/api/runtime/providers` reports safe capability information but never returns the API key. Report jobs are session-scoped and polled through `/api/report/jobs/{job_id}`.

The adapter targets the commonly supported Chat Completions shape at `/chat/completions`. Verify a different provider's JSON response mode and data-retention terms before enabling it. Hosting-provider infrastructure logs remain a separate privacy concern.

## Phase 4 metadata enrichment

The browser starts metadata enrichment automatically after either import type. Shared caches contain reusable public song, artist, album, artwork, duration, release-year, and genre evidence only; they never contain listening events or a user's rankings. The hierarchy is deliberately conservative:

1. Reuse metadata included by the uploaded provider.
2. Reuse an exact identifier or previously confidence-gated cache match.
3. Resolve a unique exact MusicBrainz artist for broad genre coverage.
4. Resolve unresolved recordings with title, artist, duration, album, and version safeguards.
5. Fetch release-group artwork from Cover Art Archive only after recording identity clears the automatic threshold.

External lookups are bounded by batch size and job deadline. Completed evidence is checkpointed and reused by later anonymous sessions. A failed lookup leaves the current deterministic profile intact.

## Free-hosting trade-off

The container can run on a free container service if it supports the image and at least 100 MB request bodies. Many free services provide only an ephemeral filesystem. Saville will still work there, but a restart may erase active sessions and the shared metadata cache. That failure mode is privacy-safe, but it is not durable.

For durable friend testing, use either a host with a persistent volume mounted at `/var/lib/saville` or a small VM running `docker compose`. The code does not require a paid database, object store, login provider, or LLM during Phase 3.

## Storage behavior

- The SQLite database contains active anonymous sessions and shared public music metadata.
- SQLite runs in WAL mode with a 30-second busy timeout for concurrent request/import threads.
- Uploaded archives are processed from temporary storage and deleted after the job finishes.
- Expired sessions and their derived listening-event rows are deleted automatically.
- A visitor can delete their session immediately from Settings.
- Do not copy the live database off the server as a casual analytics export; it may contain sessions that have not expired yet.

## Privacy and logs

Saville does not add product analytics, user accounts, email addresses, or a developer-facing listening-history dashboard. Uvicorn access logs are disabled by default. The hosting provider or HTTPS proxy may still retain ordinary infrastructure logs such as IP address, user agent, request time, and bandwidth. Configure that provider's retention separately and disclose it before a broader public test.

## Operational checks

- `/api/health` is a lightweight liveness response.
- `/api/ready` verifies the SQLite database is writable and the bundled frontend exists.
- The container health check calls `/api/ready` every 30 seconds.
- Interrupted imports are marked failed at startup; the previous completed profile remains available and the visitor can retry.
- If readiness fails, first check that `/var/lib/saville` exists and is writable by UID `10001`.

## What Phase 3 does not include

Phase 4 adds an optional hosted LLM adapter and credential-free MusicBrainz/Cover Art Archive enrichment. Multi-replica scaling and production analytics/alerting remain Phase 5 work.
