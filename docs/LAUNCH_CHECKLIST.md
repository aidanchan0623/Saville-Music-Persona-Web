# Friend-group launch checklist

Use this checklist for the hosted Web edition only. The separate localhost repository remains the complete developer/base project.

## 1. Runtime and storage

- Deploy `ghcr.io/aidanchan0623/saville-music-persona-web:latest` from a successful `main` workflow.
- Run exactly one container, one Uvicorn process, and `WEB_CONCURRENCY=1`.
- Mount a persistent volume at `/var/lib/saville` and confirm it is writable by UID `10001`.
- Allocate enough temporary disk for the configured concurrent uploads in addition to the persistent database.
- Set the platform health check to `/api/ready`.
- Disable autoscaling and overlapping rolling-deploy replicas for this test topology.

## 2. Public security settings

- Use one HTTPS origin for both the frontend and `/api`.
- Keep `SMP_SESSION_COOKIE_SECURE=true` and `SMP_SESSION_COOKIE_SAMESITE=lax`.
- Set `SMP_ALLOWED_HOSTS` to the exact public hostname, without `https://`.
- Leave account connection secrets out of the hosted environment; uploads are the only user input path.
- Generate a long random `SMP_OPERATIONS_TOKEN` in the host secret store.
- Keep `SMP_ACCESS_LOG=false`; separately reduce proxy/provider access-log retention where possible.

## 3. Capacity and cost controls

- Start with `SMP_ANONYMOUS_MAX_CONCURRENT_IMPORTS=2` and `SMP_ANONYMOUS_MAX_CONCURRENT_REPORTS=2`.
- Keep the 100 MB upload and 250,000-event defaults unless the host has enough memory and temporary storage.
- If enabling a hosted writer, set per-session and global budgets before adding its API key.
- Confirm that deterministic report fallback works with the hosted writer disabled.
- Never place an LLM key in the frontend build or a browser-visible environment variable.

## 4. Acceptance checks

- Wait for GitHub CI to pass backend tests, frontend checks, responsive Chromium checks, image smoke tests, and GHCR publication.
- Run `python scripts/hosted_preflight.py https://your-app.example --operations-token YOUR_TOKEN`.
- Open the URL once on an Android phone, an iPhone/iPad if available, and a desktop browser.
- Upload one small Google Takeout sample and one small Spotify extended-history sample in separate fresh browser sessions.
- Confirm a second private/incognito browser cannot see the first browser's profile.
- Use Settings to delete each test session, then confirm refreshing creates an empty new session.
- Restart the container once and confirm `/api/ready` returns 200 and shared public metadata survives on the volume.

## 5. Monitoring and tester disclosure

- Point a free uptime monitor at `/api/ready`; do not give that monitor the operator token.
- Check `/api/ops/status` privately for server errors, rejected work, session volume, and database growth.
- Tell testers that Saville uses an anonymous cookie, temporarily processes their uploaded export, automatically expires private analysis, and offers immediate deletion.
- Tell testers that the hosting platform or HTTPS proxy may process ordinary IP/user-agent access logs even though Saville does not collect product analytics.
- Provide one contact method for deletion or outage questions and pause invitations if error counters or storage growth become abnormal.
