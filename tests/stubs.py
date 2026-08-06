"""Import-time stubs for optional runtime dependencies.

`server.db` imports `asyncpg`, and the LLM paths import `openai` / `aiohttp`.
Those are real runtime dependencies of the app (see requirements.txt) but they
are irrelevant to the pure-logic tests here, and requiring them would mean the
suite can only run somewhere with a full install and network access.

Importing this module BEFORE any `server.*` import registers minimal stand-ins in
`sys.modules` for whichever of them is genuinely missing. If the real package is
installed, it is left completely alone — so this never masks the real library in
a full environment, and CI with a real install exercises the real code paths.
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
    mod = types.ModuleType("asyncpg")

    # server/db.py references these classes in an `except` tuple, so they have to
    # be real exception types, not sentinels.
    class InvalidAuthorizationSpecificationError(Exception):
        pass

    class InvalidPasswordError(Exception):
        pass

    class Pool:  # pragma: no cover - identity only; tests never open a pool
        pass

    async def create_pool(*args, **kwargs):  # pragma: no cover
        raise RuntimeError("asyncpg is stubbed in tests; use tests.fakedb.FakeDB")

    mod.InvalidAuthorizationSpecificationError = InvalidAuthorizationSpecificationError
    mod.InvalidPasswordError = InvalidPasswordError
    mod.Pool = Pool
    mod.create_pool = create_pool
    return mod


def _aiohttp() -> types.ModuleType:
    mod = types.ModuleType("aiohttp")

    class ClientSession:  # pragma: no cover
        def __init__(self, *a, **k):
            raise RuntimeError("aiohttp is stubbed in tests")

    mod.ClientSession = ClientSession
    return mod


def _openai() -> types.ModuleType:
    mod = types.ModuleType("openai")

    class AsyncOpenAI:  # pragma: no cover
        def __init__(self, *a, **k):
            raise RuntimeError("openai is stubbed in tests")

    mod.AsyncOpenAI = AsyncOpenAI
    return mod


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


def install() -> None:
    _ensure("asyncpg", _asyncpg)
    _ensure("aiohttp", _aiohttp)
    _ensure("openai", _openai)
    _ensure("multipart", _multipart)


install()
