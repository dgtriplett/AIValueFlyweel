"""Import-time stubs for optional runtime dependencies.

`server.db` imports `asyncpg`, and the LLM paths import `openai` / `aiohttp`.
Those are real runtime dependencies of the app (see requirements.txt) but they
are irrelevant to the pure-logic tests here, and requiring them would mean the
suite can only run somewhere with a full install and network access.

Importing this module BEFORE any `server.*` import registers minimal stand-ins in
`sys.modules` for whichever of them is genuinely missing. If the real package is
installed, it is left completely alone — so this never masks the real library in
a full environment, and CI with a real install exercises the real code paths.

The local UI harness is the deliberate exception: it calls ``install(force=True)``
to guarantee that its documented offline mode cannot reach real services merely
because the optional packages happen to be installed.
"""
from __future__ import annotations

import sys
import types


def _ensure(name: str, build) -> None:
    """Register a stub for `name` only if the real module can't be imported."""
    try:
        __import__(name)
        return
    except ImportError:
        pass
    sys.modules[name] = build()


def _asyncpg() -> types.ModuleType:
    """A stand-in exposing exactly the asyncpg surface `server/db.py` touches.

    THE FAILURE MODE THIS GUARDS
    ----------------------------
    `server/db.py` builds its `except` tuples at MODULE scope, so every exception
    class it names is resolved at import time. A name missing here is therefore not a
    quiet gap in one test — it is an `AttributeError` while importing `server.db`,
    which cascades into an ImportError for every module that transitively imports it.
    That collapsed the stdlib-only suite from ~1090 collected tests to 709 with 56
    `_FailedTest` import errors across unrelated modules (test_limits, test_logging,
    test_value_engine, ...), and the failures pointed at those modules rather than at
    the real cause.

    It is invisible in an environment with real asyncpg installed, because `_ensure`
    correctly leaves the real package alone — which is exactly how it was missed. The
    stdlib-only path is what `scripts/check.py` and CI run.

    So: when `server/db.py` starts referencing a new `asyncpg.X`, it must be added
    here, and it must be a real Exception subclass (an `except` clause rejects
    anything else with a TypeError). The inheritance below deliberately mirrors real
    asyncpg 0.31.0 rather than making everything a bare Exception, because code that
    catches a BASE class must still catch the stubbed subclasses:

        PostgresError            <- PostgresConnectionError
        PostgresError            <- InvalidAuthorizationSpecificationError
                                        <- InvalidPasswordError
        Exception                <- InterfaceError

    Getting that wrong would make the stub quietly disagree with production about
    which handler wins.
    """
    mod = types.ModuleType("asyncpg")

    class PostgresError(Exception):
        """Base for server-reported errors, as in real asyncpg."""

    class InterfaceError(Exception):
        """Driver-side misuse / closed connection. NOT a PostgresError upstream."""

    class PostgresConnectionError(PostgresError):
        """Connection lost at the protocol level."""

    class InvalidAuthorizationSpecificationError(PostgresError):
        pass

    class InvalidPasswordError(InvalidAuthorizationSpecificationError):
        """Subclasses the auth error upstream, so the auth-retry tuple catches it."""

    class UndefinedTableError(PostgresError):
        """SQLSTATE 42P01. Carries the sqlstate attribute the real driver sets.

        `accounts.is_missing_relation()` requires a positive 42P01 and deliberately
        does not guess from the class name, so the stub must carry the real signal or
        tests exercising the pre-migration path would silently assert fail-closed.
        """

        sqlstate = "42P01"

    class UniqueViolationError(PostgresError):
        """SQLSTATE 23505, used by the glossary duplicate-term path."""

        sqlstate = "23505"

    class Pool:  # pragma: no cover - identity only; tests never open a pool
        pass

    async def create_pool(*args, **kwargs):  # pragma: no cover
        raise RuntimeError("asyncpg is stubbed in tests; use tests.fakedb.FakeDB")

    mod.PostgresError = PostgresError
    mod.InterfaceError = InterfaceError
    mod.PostgresConnectionError = PostgresConnectionError
    mod.InvalidAuthorizationSpecificationError = InvalidAuthorizationSpecificationError
    mod.InvalidPasswordError = InvalidPasswordError
    mod.UndefinedTableError = UndefinedTableError
    mod.UniqueViolationError = UniqueViolationError
    mod.Pool = Pool
    mod.create_pool = create_pool
    return mod


def _aiohttp() -> types.ModuleType:
    mod = types.ModuleType("aiohttp")
    mod.__offline_stub__ = True

    class Response:
        status = 200

        def __init__(self, method: str, url: str):
            self.method = method
            self.url = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def json(self):
            if self.method == "GET" and "/messages/" in self.url:
                return {
                    "status": "COMPLETED",
                    "attachments": [{
                        "text": {"content": "Offline Genie response (no network)."}
                    }],
                }
            if self.method == "POST" and (
                self.url.endswith("/start-conversation")
                or "/messages" in self.url
            ):
                return {
                    "conversation_id": "offline-conversation",
                    "message": {"id": "offline-message"},
                }
            return {}

    class ClientSession:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def get(self, url, **kwargs):
            return Response("GET", url)

        def post(self, url, **kwargs):
            return Response("POST", url)

    mod.ClientSession = ClientSession
    return mod


def _openai() -> types.ModuleType:
    mod = types.ModuleType("openai")
    mod.__offline_stub__ = True

    class Completions:
        async def create(self, **kwargs):
            content = (
                "{}" if kwargs.get("response_format")
                else "Offline model response (no network)."
            )
            message = types.SimpleNamespace(content=content, tool_calls=[])
            choice = types.SimpleNamespace(message=message)
            return types.SimpleNamespace(choices=[choice])

    class AsyncOpenAI:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.chat = types.SimpleNamespace(completions=Completions())

    mod.AsyncOpenAI = AsyncOpenAI
    return mod


def offline_llm_client():
    """Return the deterministic OpenAI-compatible client used by serve_local."""
    return _openai().AsyncOpenAI(api_key="offline", base_url="https://offline.invalid")


def offline_workspace_client():
    """Return a non-networking stand-in for config.get_workspace_client()."""
    config = types.SimpleNamespace(
        host="https://offline.invalid",
        token="offline-token",
        authenticate=lambda: {"Authorization": "Bearer offline-token"},
    )
    current_user = types.SimpleNamespace(
        me=lambda: types.SimpleNamespace(
            user_name="offline-local", display_name="Offline Local"
        )
    )
    unavailable = types.SimpleNamespace(
        create=_offline_workspace_operation,
        upload=_offline_workspace_operation,
        download=_offline_workspace_operation,
    )
    return types.SimpleNamespace(
        config=config,
        current_user=current_user,
        files=unavailable,
        volumes=unavailable,
    )


def _offline_workspace_operation(*args, **kwargs):
    raise RuntimeError("Databricks workspace operations are disabled in offline mode")


def _multipart() -> types.ModuleType:
    """Satisfy FastAPI's import-time probe for python-multipart.

    FastAPI calls `ensure_multipart_is_installed()` while BUILDING a route that
    declares `UploadFile`, so a missing package raises at import time — before any
    request. It is a real runtime dependency (declared in requirements.txt); this
    stub only lets the route table be constructed so route wiring can be asserted
    without installing it. FastAPI checks for the `multipart.multipart`
    submodule and a __version__, so both are provided.
    """
    mod = types.ModuleType("multipart")
    mod.__version__ = "0.0.20"
    submodule = types.ModuleType("multipart.multipart")

    def parse_options_header(value):  # pragma: no cover - never called in tests
        raise RuntimeError("python-multipart is stubbed in tests")

    submodule.parse_options_header = parse_options_header
    mod.multipart = submodule
    mod.parse_options_header = parse_options_header
    sys.modules["multipart.multipart"] = submodule
    return mod


def _quiet_logging() -> None:
    """Silence app logging during tests.

    Importing app.py calls logging_setup.configure(), which is correct at runtime
    but makes test output unreadable — and warnings about unapplied migrations look
    like failures when they are the expected state under a fake database. Tests that
    assert on log OUTPUT install their own handler, so this only suppresses the
    incidental noise.
    """
    import logging
    logging.disable(logging.CRITICAL)


def install(*, force: bool = False) -> None:
    def force_install(name: str, build) -> None:
        sys.modules[name] = build()

    installer = force_install if force else _ensure
    installer("asyncpg", _asyncpg)
    installer("aiohttp", _aiohttp)
    installer("openai", _openai)
    _ensure("multipart", _multipart)
    _quiet_logging()


install()
