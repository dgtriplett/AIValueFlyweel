"""The deploy script's config rewriting.

`write_config` edits app.yaml and databricks.yml with targeted regexes rather than
a YAML round-trip, because both files' comments explain what each setting is for
and every Python YAML emitter discards them. That trade means the regexes are
load-bearing: a wrong one silently corrupts the config a customer then deploys.

So these tests run the real functions against the real files and re-parse the
result, asserting both that the intended value changed and that nothing else did.
"""
import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_YAML = os.path.join(ROOT, "app.yaml")
BUNDLE_YAML = os.path.join(ROOT, "databricks.yml")


def _load_deploy():
    """Import scripts/deploy.py by path — it isn't a package module."""
    spec = importlib.util.spec_from_file_location(
        "deploy_script", os.path.join(ROOT, "scripts", "deploy.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = _load_deploy()


def _env_value(text: str, name: str) -> str | None:
    """Read an app.yaml env entry's value, the way the app runtime would."""
    match = re.search(
        rf"-\s*name:\s*{re.escape(name)}\s*\n(?:\s*#[^\n]*\n)*\s*value:\s*(\"[^\"]*\"|'[^']*'|[^\n]*)",
        text)
    if not match:
        return None
    return match.group(1).strip().strip("\"'")


class TestSetYamlEnv(unittest.TestCase):
    def setUp(self):
        with open(APP_YAML) as fh:
            self.original = fh.read()

    def test_sets_an_empty_value(self):
        out = deploy.set_yaml_env(self.original, "PGHOST", "ep-abc.example.com")
        self.assertEqual(_env_value(out, "PGHOST"), "ep-abc.example.com")

    def test_overwrites_an_existing_value(self):
        once = deploy.set_yaml_env(self.original, "ATLAS_SCHEMA", "first")
        twice = deploy.set_yaml_env(once, "ATLAS_SCHEMA", "second")
        self.assertEqual(_env_value(twice, "ATLAS_SCHEMA"), "second")

    def test_is_idempotent(self):
        once = deploy.set_yaml_env(self.original, "PGUSER", "sp-123")
        twice = deploy.set_yaml_env(once, "PGUSER", "sp-123")
        self.assertEqual(once, twice)

    def test_touches_only_the_named_entry(self):
        """The failure this guards: a greedy pattern rewriting neighbouring keys."""
        out = deploy.set_yaml_env(self.original, "PGPORT", "5433")
        for other in ("PGHOST", "PGDATABASE", "SERVING_ENDPOINT", "ATLAS_CATALOG",
                      "ATLAS_SCHEMA", "GENIE_MIRROR_SCHEMA", "DEMO_MODE"):
            self.assertEqual(_env_value(out, other), _env_value(self.original, other),
                             f"{other} changed while setting PGPORT")

    def test_preserves_comments(self):
        """The comments are the reason for hand-editing rather than round-tripping."""
        out = deploy.set_yaml_env(self.original, "PGHOST", "h")
        for line in self.original.splitlines():
            if line.strip().startswith("#"):
                self.assertIn(line, out)

    def test_preserves_line_count(self):
        out = deploy.set_yaml_env(self.original, "ATLAS_CATALOG", "main")
        self.assertEqual(len(out.splitlines()), len(self.original.splitlines()))

    def test_values_are_quoted(self):
        """An unquoted 'on'/'off'/'no' is a YAML boolean, not a string, and
        DEMO_MODE would arrive as True."""
        out = deploy.set_yaml_env(self.original, "DEMO_MODE", "off")
        match = re.search(r"-\s*name:\s*DEMO_MODE\s*\n(?:\s*#[^\n]*\n)*\s*value:\s*(\S+)", out)
        self.assertEqual(match.group(1), '"off"')

    def test_unknown_key_leaves_text_unchanged(self):
        out = deploy.set_yaml_env(self.original, "NOT_A_REAL_SETTING", "x")
        self.assertEqual(out, self.original)

    def test_value_with_special_characters(self):
        out = deploy.set_yaml_env(self.original, "SERVING_ENDPOINT",
                                  "my-endpoint_v2.1")
        self.assertEqual(_env_value(out, "SERVING_ENDPOINT"), "my-endpoint_v2.1")


class TestWarehouseIdRewrite(unittest.TestCase):
    """The warehouse id lives under the resource block, not env, so it takes a
    different pattern — and is the one setting the app cannot work without."""

    def test_sets_the_resource_id(self):
        with open(APP_YAML) as fh:
            text = fh.read()
        out = re.sub(r"(sql_warehouse:\s*\n\s*id:\s*)(\"[^\"]*\"|'[^']*'|[^\n]*)",
                     r'\g<1>"abc123"', text, count=1)
        match = re.search(r"sql_warehouse:\s*\n\s*id:\s*\"([^\"]*)\"", out)
        self.assertEqual(match.group(1), "abc123")

    def test_app_yaml_has_a_warehouse_resource_to_rewrite(self):
        with open(APP_YAML) as fh:
            text = fh.read()
        self.assertIn("sql_warehouse:", text)


class TestBundleVariableRewrite(unittest.TestCase):
    def setUp(self):
        with open(BUNDLE_YAML) as fh:
            self.original = fh.read()

    def _set(self, text, key, value):
        return re.sub(
            rf"(  {re.escape(key)}:\n(?:    [^\n]*\n)*?    default:\s*)([^\n]*)",
            rf"\g<1>{value}", text, count=1)

    def test_sets_a_variable_default(self):
        out = self._set(self.original, "atlas_schema", "my_schema")
        match = re.search(r"  atlas_schema:\n(?:    [^\n]*\n)*?    default:\s*(\S+)", out)
        self.assertEqual(match.group(1), "my_schema")

    def test_touches_only_the_named_variable(self):
        out = self._set(self.original, "atlas_schema", "changed")
        for other in ("serving_endpoint", "genie_mirror_schema", "lakebase_project"):
            before = re.search(
                rf"  {other}:\n(?:    [^\n]*\n)*?    default:\s*(\S+)", self.original)
            after = re.search(
                rf"  {other}:\n(?:    [^\n]*\n)*?    default:\s*(\S+)", out)
            self.assertEqual(before.group(1), after.group(1), f"{other} changed")

    def test_every_variable_deploy_sets_exists_in_the_bundle(self):
        """A typo'd key would be a silent no-op — the deploy would appear to work
        and the app would get the wrong value."""
        for key in ("atlas_catalog", "atlas_schema", "serving_endpoint",
                    "genie_mirror_catalog", "genie_mirror_schema",
                    "lakebase_project", "demo_mode", "warehouse_id"):
            self.assertRegex(self.original, rf"\n  {key}:\n",
                             f"{key} is not declared in databricks.yml")


class TestSettingsParity(unittest.TestCase):
    """app.yaml and databricks.yml must agree, which is the whole reason this
    script writes both rather than documenting a two-file edit."""

    def test_every_env_var_deploy_writes_exists_in_app_yaml(self):
        with open(APP_YAML) as fh:
            text = fh.read()
        for name in ("PGHOST", "PGUSER", "PGDATABASE", "SERVING_ENDPOINT",
                     "ATLAS_CATALOG", "ATLAS_SCHEMA", "GENIE_MIRROR_CATALOG",
                     "GENIE_MIRROR_SCHEMA", "GENIE_SPACE_ID", "DEMO_MODE"):
            self.assertIsNotNone(_env_value(text, name) if f"name: {name}" in text else None,
                                 f"{name} missing from app.yaml")

    def test_app_yaml_ships_with_no_baked_in_environment(self):
        """A committed hostname or warehouse id would deploy one customer's config
        into another's workspace."""
        with open(APP_YAML) as fh:
            text = fh.read()
        for name in ("PGHOST", "PGUSER", "ATLAS_CATALOG", "GENIE_MIRROR_CATALOG"):
            self.assertEqual(_env_value(text, name), "",
                             f"{name} has a baked-in value; it must ship empty")
        match = re.search(r"sql_warehouse:\s*\n\s*id:\s*\"([^\"]*)\"", text)
        self.assertEqual(match.group(1), "", "a warehouse id is baked into app.yaml")

    def test_demo_mode_ships_off(self):
        """Demo Mode can reset a customer's portfolio; it must never ship enabled."""
        with open(APP_YAML) as fh:
            self.assertEqual(_env_value(fh.read(), "DEMO_MODE"), "off")
        with open(BUNDLE_YAML) as fh:
            match = re.search(
                r"  demo_mode:\n(?:    [^\n]*\n)*?    default:\s*(\S+)", fh.read())
        self.assertIn(match.group(1).strip("\"'"), ("off",))


class TestCliContract(unittest.TestCase):
    def test_dry_run_suppresses_execution(self):
        """--dry-run must be trustworthy: `run` returns without spawning anything."""
        original = deploy.DRY_RUN
        try:
            deploy.DRY_RUN = True
            result = deploy.run(["definitely-not-a-real-command", "--flag"])
            self.assertEqual(result.returncode, 0)
        finally:
            deploy.DRY_RUN = original

    def test_ask_requires_a_value_under_yes_when_no_default(self):
        with self.assertRaises(SystemExit):
            deploy.ask("SQL warehouse ID", None, assume_yes=True, required=True)

    def test_ask_returns_default_under_yes(self):
        self.assertEqual(
            deploy.ask("Profile", "DEFAULT", assume_yes=True), "DEFAULT")

    def test_optional_prompt_may_be_blank_under_yes(self):
        self.assertEqual(
            deploy.ask("Genie space", None, assume_yes=True, required=False), "")

    def test_service_principal_field_spellings(self):
        """The field name has moved between CLI versions; pinning one spelling
        breaks the deploy on upgrade."""
        for key in ("service_principal_client_id", "service_principal_id",
                    "service_principal_name"):
            self.assertEqual(deploy.app_service_principal({key: "sp-1"}), "sp-1")
        self.assertIsNone(deploy.app_service_principal({}))

    def test_cache_file_is_gitignored(self):
        """It records workspace-specific values and must not be committed."""
        with open(os.path.join(ROOT, ".gitignore")) as fh:
            self.assertIn(".deploy-cache.json", fh.read())

    def test_cli_version_parses_the_real_banner_format(self):
        """Regression: the original pattern matched the first two numbers in
        "Databricks CLI v0.298.0" and produced (0, 298) only by luck — a banner
        with any other number first would have blocked a valid install."""
        pattern = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
        cases = {
            "Databricks CLI v0.298.0": (0, 298),
            "Databricks CLI v0.239.1": (0, 239),
            "0.240.2": (0, 240),
        }
        for banner, expected in cases.items():
            match = pattern.search(banner)
            self.assertIsNotNone(match, banner)
            self.assertEqual((int(match.group(1)), int(match.group(2))), expected)
            self.assertGreaterEqual((int(match.group(1)), int(match.group(2))),
                                    deploy.MIN_CLI_VERSION, banner)

    def test_old_cli_version_is_rejected(self):
        match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", "Databricks CLI v0.220.0")
        self.assertLess((int(match.group(1)), int(match.group(2))),
                        deploy.MIN_CLI_VERSION)

    def test_color_helpers_degrade_without_a_tty(self):
        """Escape codes in piped output would corrupt logs."""
        original = deploy._COLOR
        try:
            deploy._COLOR = False
            self.assertEqual(deploy.green("ok"), "ok")
        finally:
            deploy._COLOR = original


if __name__ == "__main__":
    unittest.main()
