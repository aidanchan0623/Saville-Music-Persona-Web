from __future__ import annotations

import argparse
import json
import sys
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


REQUIRED_SECURITY_HEADERS = {
    "content-security-policy",
    "permissions-policy",
    "referrer-policy",
    "x-content-type-options",
    "x-frame-options",
}


def fail(message: str) -> None:
    raise RuntimeError(message)


def request(opener, base_url: str, path: str, *, method: str = "GET", headers: dict[str, str] | None = None):
    target = urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))
    req = Request(target, method=method, headers=headers or {}, data=b"" if method in {"POST", "DELETE"} else None)
    try:
        response = opener.open(req, timeout=20)
        return response.status, response.headers, response.read()
    except HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def json_body(body: bytes, label: str) -> dict:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"{label} did not return valid JSON: {exc}")
    if not isinstance(payload, dict):
        fail(f"{label} returned a non-object JSON payload")
    return payload


def assert_security_headers(headers, label: str) -> None:
    available = {name.casefold() for name in headers.keys()}
    missing = sorted(REQUIRED_SECURITY_HEADERS - available)
    if missing:
        fail(f"{label} is missing security headers: {', '.join(missing)}")


def run(
    base_url: str,
    *,
    operations_token: str | None,
    allow_insecure_cookie: bool,
    minimum_upload_mib: int,
) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        fail("Base URL must be an absolute http(s) URL")
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    status, headers, body = request(opener, base_url, "/api/health")
    health = json_body(body, "Health probe")
    if status != 200 or not health.get("ok") or health.get("version") != "0.6.0":
        fail(f"Health probe failed: HTTP {status}, payload={health}")
    assert_security_headers(headers, "Health probe")

    status, _, body = request(opener, base_url, "/api/ready")
    ready = json_body(body, "Readiness probe")
    if status != 200 or not ready.get("ok") or ready.get("workerTopology") != "single-process":
        fail(f"Readiness probe failed: HTTP {status}, payload={ready}")
    configured_upload_bytes = int((ready.get("limits") or {}).get("maxUploadBytes") or 0)
    required_upload_bytes = minimum_upload_mib * 1024 * 1024
    if configured_upload_bytes < required_upload_bytes:
        fail(
            "Readiness upload limit is too small: "
            f"configured={configured_upload_bytes} bytes, required={required_upload_bytes} bytes"
        )

    status, headers, body = request(opener, base_url, "/")
    if status != 200 or b"Saville Music Persona" not in body:
        fail(f"Bundled frontend failed: HTTP {status}")
    assert_security_headers(headers, "Frontend")

    status, headers, body = request(opener, base_url, "/api/session")
    session = json_body(body, "Anonymous session")
    if status != 200 or not session.get("anonymous") or session.get("accountConnectionsEnabled"):
        fail(f"Anonymous session boundary failed: HTTP {status}, payload={session}")
    cookie = headers.get("Set-Cookie", "").casefold()
    if "httponly" not in cookie or "samesite=" not in cookie:
        fail("Anonymous session cookie is missing HttpOnly or SameSite")
    if parsed.scheme == "https" and "secure" not in cookie:
        fail("HTTPS deployment issued a session cookie without Secure")
    if parsed.scheme == "http" and not allow_insecure_cookie:
        fail("Refusing to certify an HTTP deployment; use HTTPS or the local smoke-test override")

    origin = f"{parsed.scheme}://{parsed.netloc}"
    status, _, body = request(opener, base_url, "/api/auth/setup", method="POST", headers={"Origin": origin})
    account_setup = json_body(body, "Disabled account route")
    if status != 403 or "disabled" not in json.dumps(account_setup).casefold():
        fail(f"Account-connection route is not disabled: HTTP {status}, payload={account_setup}")

    if operations_token:
        status, _, body = request(
            opener,
            base_url,
            "/api/ops/status",
            headers={"X-Saville-Ops-Token": operations_token},
        )
        operations = json_body(body, "Operator status")
        if status != 200 or operations.get("privacy", {}).get("containsListeningHistory") is not False:
            fail(f"Operator status failed its privacy contract: HTTP {status}, payload={operations}")

    status, _, body = request(opener, base_url, "/api/session", method="DELETE", headers={"Origin": origin})
    deleted = json_body(body, "Session deletion")
    if status != 200 or not deleted.get("deleted"):
        fail(f"Session deletion failed: HTTP {status}, payload={deleted}")

    print("Saville hosted preflight passed: API, frontend, privacy boundary, security headers, and deletion are ready.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify a Saville hosted deployment without uploading listening data.")
    parser.add_argument("base_url", help="Deployment origin, for example https://saville.example.com")
    parser.add_argument("--operations-token", help="Optional token used to verify the private aggregate status endpoint")
    parser.add_argument(
        "--minimum-upload-mib",
        type=int,
        default=300,
        help="Minimum advertised upload capacity to certify (default: 300 MiB)",
    )
    parser.add_argument(
        "--allow-insecure-cookie",
        action="store_true",
        help="Allow HTTP only for local/container smoke tests; never use this for a public deployment",
    )
    args = parser.parse_args()
    try:
        run(
            args.base_url,
            operations_token=args.operations_token,
            allow_insecure_cookie=args.allow_insecure_cookie,
            minimum_upload_mib=max(1, args.minimum_upload_mib),
        )
    except (RuntimeError, URLError) as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
