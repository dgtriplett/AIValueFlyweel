"""App-level smoke test: the FastAPI app imports and exposes every route.

Unit tests exercise modules in isolation, which misses the failures that actually
break a deploy: a router that was written but never included, two routers whose
paths collide, a syntax error in a module nothing else imports, or a handler whose
type annotations FastAPI can't resolve into a schema. Importing the real `app`
object catches all of those in under a second, without a database or a warehouse.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401  (must precede any server.* import)

# Import the app with discovery unconfigured, which is the state a fresh install
# starts in — the app must import and serve regardless.
os.environ.pop("ATLAS_CATALOG", None)
os.environ.pop("PGHOST", None)

from app import app  # noqa: E402


def _paths() -> set[str]:
    return {route.path for route in app.routes if hasattr(route, "path")}


def _methods(path: str) -> set[str]:
    out: set[str] = set()
    for route in app.routes:
        if getattr(route, "path", None) == path:
            out |= set(getattr(route, "methods", set()) or set())
    return out


class TestAppImports(unittest.TestCase):
    def test_app_object_exists(self):
        self.assertEqual(app.title, "Grid Atlas")

    def test_health_endpoint_registered(self):
        self.assertIn("/api/health", _paths())


class TestPortfolioRoutesSurvivedTheFork(unittest.TestCase):
    """The fork must not have dropped any pre-existing capability."""

    def test_core_routes_present(self):
        paths = _paths()
        for path in ("/api/use-cases", "/api/data-assets", "/api/lobs",
                     "/api/value-records", "/api/roadmap-items",
                     "/api/value-assumptions", "/api/analytics/dashboard",
                     "/api/joint-funding/opportunities",
                     "/api/data-sources/recommendations", "/api/agents/recommend",
                     "/api/agents/roadmap", "/api/agents/detect-dependencies",
                     "/api/dependencies/requires", "/api/dependencies/enables",
                     "/api/use-cases/{uc_id}/readiness",
                     "/api/use-cases/{uc_id}/unlocks",
                     "/api/live/sync", "/api/genie/ask",
                     "/api/onboarding/export.xlsx", "/api/onboarding/import"):
            self.assertIn(path, paths, f"{path} disappeared in the fork")

    def test_spa_catch_all_registered_last(self):
        """The SPA fallback matches everything; if it were registered before an
        API route, that route would be shadowed and return index.html."""
        order = [r.path for r in app.routes if hasattr(r, "path")]
        if "/{full_path:path}" in order:
            self.assertEqual(order[-1], "/{full_path:path}")


class TestDomainRoutes(unittest.TestCase):
    def test_domain_crud_registered(self):
        paths = _paths()
        self.assertIn("/api/domains", paths)
        self.assertIn("/api/domains/{domain_id}", paths)
        self.assertIn("/api/domains/{domain_id}/assets", paths)
        self.assertIn("/api/domains/gaps", paths)

    def test_use_case_domain_routes_registered(self):
        """These live on a second router in domains.py; forgetting to include it
        is an easy and silent mistake."""
        self.assertIn("/api/use-cases/{use_case_id}/domains", _paths())
        methods = _methods("/api/use-cases/{use_case_id}/domains")
        self.assertIn("GET", methods)
        self.assertIn("PUT", methods)

    def test_coverage_matrix_registered(self):
        self.assertIn("/api/domains/coverage-matrix", _paths())

    def test_literal_domain_routes_precede_the_id_route(self):
        """/domains/gaps and /domains/coverage-matrix must be declared before
        /domains/{domain_id}, or the literal path is captured as an id and 422s."""
        order = [r.path for r in app.routes if hasattr(r, "path")]
        id_route = order.index("/api/domains/{domain_id}")
        for literal in ("/api/domains/gaps", "/api/domains/coverage-matrix"):
            self.assertLess(order.index(literal), id_route, literal)

    def test_gaps_route_not_shadowed_by_the_id_route(self):
        """/domains/gaps must be declared before /domains/{domain_id}, or the
        literal path is captured as an id and 422s on int parsing."""
        order = [r.path for r in app.routes if hasattr(r, "path")]
        self.assertLess(order.index("/api/domains/gaps"),
                        order.index("/api/domains/{domain_id}"))


class TestGenerationRoutes(unittest.TestCase):
    def test_generation_and_confirm_registered(self):
        paths = _paths()
        for path in ("/api/generate/use-cases",
                     "/api/generate/use-cases/commit",
                     "/api/generate/use-cases/{preview_id}",
                     "/api/generate/research",
                     "/api/generate/previews",
                     "/api/generate/cleanup",
                     "/api/confirm/{token}"):
            self.assertIn(path, paths, f"{path} not registered")

    def test_confirm_supports_read_and_apply(self):
        methods = _methods("/api/confirm/{token}")
        self.assertIn("GET", methods)    # re-read a card after a reload
        self.assertIn("POST", methods)   # consume it

    def test_commit_is_not_shadowed_by_the_preview_id_route(self):
        """`/use-cases/commit` and `/use-cases/{preview_id}` overlap textually.
        They coexist today only because the parameterized route is GET-only. If
        someone adds a GET to commit, or declares {preview_id} first for POST,
        commit silently becomes a preview lookup — so pin both facts."""
        order = [r.path for r in app.routes if hasattr(r, "path")]
        commit_methods = _methods("/api/generate/use-cases/commit")
        preview_methods = _methods("/api/generate/use-cases/{preview_id}")
        self.assertEqual(commit_methods & preview_methods, set(),
                         "commit and {preview_id} now share an HTTP method; declare "
                         "the literal path before the parameterized one")
        if commit_methods & preview_methods:
            self.assertLess(order.index("/api/generate/use-cases/commit"),
                            order.index("/api/generate/use-cases/{preview_id}"))


class TestIngestionRoutes(unittest.TestCase):
    def test_pipeline_stages_registered(self):
        paths = _paths()
        for path in ("/api/ingestion/bootstrap",
                     "/api/ingestion/upload/schemas",
                     "/api/ingestion/upload/tables",
                     "/api/ingestion/upload/columns",
                     "/api/ingestion/enrich/schemas",
                     "/api/ingestion/enrich/tables",
                     "/api/ingestion/canonicalize",
                     "/api/ingestion/attribute",
                     "/api/ingestion/summary",
                     "/api/ingestion/aliases",
                     "/api/ingestion/runs"):
            self.assertIn(path, paths, f"{path} not registered")


class TestOpenApiSchema(unittest.TestCase):
    def test_schema_generates(self):
        """Forces FastAPI to resolve every handler's request/response models. A
        bad annotation raises here rather than at runtime on first request."""
        schema = app.openapi()
        self.assertIn("openapi", schema)
        self.assertGreater(len(schema["paths"]), 40)

    def test_no_duplicate_operation_ids(self):
        """Duplicate operation ids break generated clients and signal an
        accidentally double-registered router."""
        seen: dict[str, str] = {}
        duplicates = []
        for path, operations in app.openapi()["paths"].items():
            for method, spec in operations.items():
                operation_id = spec.get("operationId")
                if not operation_id:
                    continue
                if operation_id in seen:
                    duplicates.append(f"{operation_id} ({seen[operation_id]} vs {method} {path})")
                seen[operation_id] = f"{method} {path}"
        self.assertEqual(duplicates, [], f"duplicate operationIds: {duplicates}")


class TestDegradedStartup(unittest.TestCase):
    def test_imports_without_discovery_configured(self):
        """A fresh install has no ATLAS_CATALOG. Importing must not raise, and
        the summary endpoint must report the unconfigured state rather than 500."""
        import asyncio

        from server.routes import ingestion

        result = asyncio.run(ingestion.summary())
        self.assertFalse(result["configured"])

    def test_discovery_guard_raises_conflict_not_crash(self):
        from fastapi import HTTPException

        from server.routes import ingestion

        with self.assertRaises(HTTPException) as caught:
            ingestion._require_discovery()
        self.assertEqual(caught.exception.status_code, 409)
        # The message must tell the operator how to fix it.
        self.assertIn("ATLAS_CATALOG", str(caught.exception.detail))


class TestNewFeatureRoutes(unittest.TestCase):
    """The features merged from the BHE catalog."""

    def test_all_registered(self):
        paths = _paths()
        for path in (
            # company research + assumption calibration
            "/api/research/company", "/api/research/assumptions",
            "/api/research/apply", "/api/research/runs",
            # value flow + glossary
            "/api/flow/sankey", "/api/flow/glossary",
            # classification rules
            "/api/rules", "/api/rules/test", "/api/rules/seed",
            # artifact inventory
            "/api/artifacts", "/api/artifacts/sync",
            "/api/artifacts/unattributed",
            # chat
            "/api/chat", "/api/chat/conversations", "/api/chat/tools/list",
        ):
            self.assertIn(path, paths, f"{path} not registered")

    def test_literal_paths_precede_their_parameterized_siblings(self):
        """A literal segment declared after a parameterized one is captured as an
        id and 404s/422s. This has bitten /domains/gaps, /generate/use-cases/commit,
        and /chat/tools/list — so assert the whole class of hazard at once."""
        order = [r.path for r in app.routes if hasattr(r, "path")]
        pairs = [
            ("/api/chat/tools/list", "/api/chat/{conversation_id}"),
            ("/api/domains/gaps", "/api/domains/{domain_id}"),
            ("/api/domains/coverage-matrix", "/api/domains/{domain_id}"),
            ("/api/artifacts/unattributed", "/api/artifacts/{artifact_id}"),
            ("/api/artifacts/sync", "/api/artifacts/{artifact_id}"),
            ("/api/rules/test", "/api/rules/{rule_id}"),
            ("/api/rules/seed", "/api/rules/{rule_id}"),
        ]
        for literal, parameterized in pairs:
            if literal in order and parameterized in order:
                self.assertLess(order.index(literal), order.index(parameterized),
                                f"{literal} is shadowed by {parameterized}")

    def test_research_apply_goes_through_the_confirm_gate(self):
        from server import confirm as cf
        from server.routes import generate
        self.assertIn(cf.INTENT_APPLY_RESEARCH, generate._EXECUTORS)

    def test_chat_write_tools_only_propose(self):
        """A chat-driven write must land in the same gate as any other agent
        write — the chat is the least predictable caller in the app."""
        from server import chat_tools as ct
        writers = [t for t in ct.TOOLS.values() if t.writes]
        self.assertTrue(writers, "no write tools registered")
        for tool in writers:
            self.assertIn("propose", tool.name,
                          f"{tool.name} writes but is not named as a proposal")
            self.assertIn("confirm", tool.description.lower(),
                          f"{tool.name} does not tell the model it needs confirmation")


if __name__ == "__main__":
    unittest.main()
