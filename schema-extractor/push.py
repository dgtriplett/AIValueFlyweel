"""Upload the extract straight to AI Value Flywheel, instead of by hand.

WHY
---
The sweep already produces exactly the three files the app wants. Making someone find
them in ./output/, open the app, navigate to the right screen and pick each one from a
file dialog is four steps of clerical work in the middle of an otherwise one-command
job — and the step where a stale CSV from last month gets uploaded by mistake.

`--push https://<app-host>` posts them directly. The manual path is untouched: the CSVs
are still written to ./output/ first, so an air-gapped estate (or anyone who wants to
inspect the metadata before it leaves the machine) works exactly as before. Push is
strictly additive.

AUTH
----
No bespoke token. A Databricks App accepts a workspace OAuth token as a bearer, and the
extractor is already holding an authenticated WorkspaceClient for every workspace it
swept — so the token comes from the SDK's own credential chain (`~/.databrickscfg`
profile, or the browser login the sweep already performed). Nothing new to issue, store,
or leak, and the app sees the human's identity rather than a shared secret.

The token is minted for the workspace the APP runs in, which is not necessarily one of
the swept workspaces: --push takes the app's host and authenticates against that.

RATE LIMITS ARE REAL HERE
-------------------------
The upload endpoints are classed 'sweep' — burst 2, four per minute — because each one
does minutes of warehouse work. Three files therefore CANNOT be pushed back-to-back: the
third gets a 429. That is the server being correct, not a bug to route around, so this
honours the `Retry-After` header it sends. Discovered by reading server/limits.py; the
obvious implementation (three requests in a loop) fails on the third file every time.

STDLIB ONLY
-----------
urllib, not requests: the extractor's only dependency is databricks-sdk, and the whole
point of a downloadable script is that `pip install -r requirements.txt` is short and
boring. A multipart body is ~30 lines to build by hand and worth it.
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# Which local file feeds which endpoint. Order matters: schemas before tables before
# columns, because the app's ingestion MERGEs each level onto the one above it, and a
# column row whose table has not landed yet is dropped.
UPLOADS = (
    ("all_schemas.csv", "/api/ingestion/upload/schemas", True),
    ("all_tables.csv", "/api/ingestion/upload/tables", True),
    ("all_columns.csv", "/api/ingestion/upload/columns", False),
)

# The server allows 4 sweep-class requests a minute. 20s spacing keeps a three-file push
# comfortably inside that without relying on the retry path at all — the retry is there
# for a shared account where someone else is also sweeping.
_SPACING_SECONDS = 20
_MAX_ATTEMPTS = 4
_TIMEOUT_SECONDS = 600

# Matches MAX_UPLOAD_BYTES in server/routes/ingestion.py. Checked locally so a 60MB file
# fails in a second with a clear message instead of after a long upload.
_MAX_BYTES = 64 * 1024 * 1024


class PushError(RuntimeError):
    """Upload failed. The message is written to be read by the person who ran it."""


def _token(host: str) -> str:
    """A bearer token for `host`, from the SDK's normal credential chain."""
    try:
        from databricks.sdk.core import Config
    except ImportError as exc:  # pragma: no cover - checked earlier by the CLI
        raise PushError("databricks-sdk is required for --push. "
                        "Run: pip install -r requirements.txt") from exc

    try:
        config = Config(host=host)
        headers = config.authenticate()
    except Exception as exc:  # noqa: BLE001 - the SDK raises many auth types
        raise PushError(
            f"Could not authenticate to {host}: {exc}\n"
            "  Log in first (databricks auth login --host " + host + "),\n"
            "  or set DATABRICKS_TOKEN, or omit --push and upload the CSVs by hand."
        ) from exc

    authorization = headers.get("Authorization") if headers else None
    if not authorization:
        raise PushError(
            f"The Databricks SDK returned no Authorization header for {host}. "
            "Check that a profile or DATABRICKS_TOKEN matches that host.")
    return authorization


def _multipart(path: Path) -> tuple[bytes, str]:
    """A multipart/form-data body with the file under the field name `file`.

    Field name is fixed by the server (`file: UploadFile = File(...)`); sending it under
    any other name is a 422 that names the field but not the mistake.
    """
    boundary = f"----AIValueFlywheel{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(path.name)[0] or "text/csv"
    body = b"".join((
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        .encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ))
    return body, f"multipart/form-data; boundary={boundary}"


# Substrings that mean a 5xx will fail identically no matter how many times it is sent.
# The app reports a misconfiguration as 502 (it is proxying a failed warehouse call), so
# treating every 5xx as transient turns "you have not run the bootstrap" into four
# attempts and a minute of waiting before the message appears. Found by pushing to the
# live app before its discovery tables existed.
_PERMANENT_5XX_MARKERS = (
    "TABLE_OR_VIEW_NOT_FOUND",
    "SCHEMA_NOT_FOUND",
    "PERMISSION_DENIED",
    "UNAUTHORIZED_ACCESS",
    "not configured",
    "does not exist",
)


def _is_permanent(detail: str) -> bool:
    return any(marker.lower() in detail.lower() for marker in _PERMANENT_5XX_MARKERS)


def _post(url: str, path: Path, authorization: str) -> dict:
    """One upload, retrying only what can actually succeed on a second attempt.

    A 4xx other than 429 is a real rejection — wrong file, bad columns — and retrying it
    just delays the message. A 5xx is retried only if it does not name a permanent cause.
    """
    body, content_type = _multipart(path)
    last_error = ""

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Authorization", authorization)
        request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as error:
            detail = (error.read().decode(errors="replace") or "").strip()
            try:                       # the app returns {"detail": "..."}
                detail = json.loads(detail).get("detail", detail)
            except ValueError:
                pass

            if error.code == 429:
                # The server tells us how long to wait; trust it rather than guessing.
                wait = int(error.headers.get("Retry-After") or _SPACING_SECONDS)
                if attempt < _MAX_ATTEMPTS:
                    print(f"    rate limited; waiting {wait}s "
                          f"(attempt {attempt}/{_MAX_ATTEMPTS})")
                    time.sleep(wait)
                    continue
            elif (500 <= error.code < 600 and attempt < _MAX_ATTEMPTS
                    and not _is_permanent(detail)):
                print(f"    server error {error.code}; retrying in {_SPACING_SECONDS}s")
                time.sleep(_SPACING_SECONDS)
                continue
            elif 500 <= error.code < 600 and _is_permanent(detail):
                raise PushError(
                    f"HTTP {error.code} from {url}\n  {detail}\n"
                    "  This will not succeed on a retry. If the discovery tables are "
                    "missing, open the app and run Get started → step 1 (or "
                    "POST /api/ingestion/bootstrap).") from error
            elif error.code in (401, 403):
                raise PushError(
                    f"{error.code} from the app. The token authenticated but this "
                    "identity may not have CAN_USE on the app itself.\n"
                    f"  {detail}") from error

            raise PushError(f"HTTP {error.code} from {url}\n  {detail}") from error
        except urllib.error.URLError as error:
            last_error = str(error.reason)
            if attempt < _MAX_ATTEMPTS:
                print(f"    {last_error}; retrying in {_SPACING_SECONDS}s")
                time.sleep(_SPACING_SECONDS)
                continue
            raise PushError(
                f"Could not reach {url}: {last_error}\n"
                "  Check the --push host, and that you can open the app in a browser."
            ) from error

    raise PushError(f"Gave up after {_MAX_ATTEMPTS} attempts: {last_error}")


def normalize_host(host: str) -> str:
    """Accept what someone will actually paste: with or without scheme, with a path."""
    host = (host or "").strip()
    if not host:
        raise PushError("--push needs the app's URL, e.g. "
                        "--push https://my-app.aws.databricksapps.com")
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    # Strip any path someone copied out of the address bar (".../console/#catalog").
    scheme, _, rest = host.partition("://")
    return f"{scheme}://{rest.split('/')[0]}"


def push(output_dir: Path, host: str, *, dry_run: bool = False) -> int:
    """Upload whatever the sweep produced. Returns a process exit code.

    Never raises: this runs after a successful extract, and the CSVs are already safely
    on disk. A failed push must print how to finish the job by hand, not lose the run
    behind a traceback.
    """
    try:
        host = normalize_host(host)
        present = []
        for name, endpoint, required in UPLOADS:
            path = output_dir / name
            if not path.exists():
                if required:
                    raise PushError(
                        f"{path} not found. --push uploads what the sweep wrote, so run "
                        "the extract first (or drop --push).")
                print(f"  skip {name} (not produced; --no-columns?)")
                continue
            size = path.stat().st_size
            if size > _MAX_BYTES:
                raise PushError(
                    f"{name} is {size / 1024 / 1024:.0f}MB, over the app's "
                    f"{_MAX_BYTES // 1024 // 1024}MB limit. Split workspaces.txt and "
                    "push each batch separately.")
            present.append((name, endpoint, path, size))

        print(f"\npushing {len(present)} file(s) to {host}")
        if dry_run:
            for name, endpoint, _, size in present:
                print(f"  would POST {name} ({size / 1024:.0f}KB) -> {endpoint}")
            return 0

        authorization = _token(host)
        for index, (name, endpoint, path, size) in enumerate(present):
            # Spacing between uploads, not before the first one.
            if index:
                print(f"  pacing {_SPACING_SECONDS}s (the upload endpoints are "
                      "rate-limited)")
                time.sleep(_SPACING_SECONDS)
            print(f"  {name} ({size / 1024:.0f}KB) -> {endpoint}")
            result = _post(f"{host}{endpoint}", path, authorization)
            print(f"    ok: {result.get('rows_written', '?')} row(s) written "
                  f"of {result.get('rows_in_file', '?')} in file")

        print("\nUploaded. In the app: Get started → enrich and canonicalize the "
              "inventory.")
        return 0
    except PushError as error:
        print(f"\nPUSH FAILED: {error}", file=sys.stderr)
        print(f"\nThe CSVs are still in {output_dir} — upload them by hand from "
              "AI Value Flywheel → Get started → step 2A.", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nPush interrupted. The CSVs are still on disk.", file=sys.stderr)
        return 130


def host_from_env() -> str | None:
    """Allow the host to come from the environment, for scheduled runs."""
    return os.environ.get("AI_VALUE_FLYWHEEL_HOST") or None
