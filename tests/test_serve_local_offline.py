"""Regression coverage for the local harness's no-network contract."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestServeLocalOffline(unittest.TestCase):
    def test_importable_real_clients_are_forcibly_replaced(self):
        """Installed dependencies and ambient credentials must not enable calls."""
        script = textwrap.dedent(
            """
            import asyncio
            import sys
            import types

            class WouldCallNetwork:
                def __init__(self, *args, **kwargs):
                    raise AssertionError("would have used an installed network client")

            installed_openai = types.ModuleType("openai")
            installed_openai.AsyncOpenAI = WouldCallNetwork
            installed_aiohttp = types.ModuleType("aiohttp")
            installed_aiohttp.ClientSession = WouldCallNetwork
            installed_asyncpg = types.ModuleType("asyncpg")
            sys.modules.update({
                "openai": installed_openai,
                "aiohttp": installed_aiohttp,
                "asyncpg": installed_asyncpg,
            })

            sys.path.insert(0, "tests")
            import serve_local

            assert not serve_local.ONLINE
            assert sys.modules["openai"] is not installed_openai
            assert sys.modules["aiohttp"] is not installed_aiohttp
            assert sys.modules["asyncpg"] is not installed_asyncpg
            assert sys.modules["openai"].__offline_stub__ is True
            assert sys.modules["aiohttp"].__offline_stub__ is True

            for name in (
                "DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_CONFIG_PROFILE",
                "DATABRICKS_WAREHOUSE_ID", "GENIE_SPACE_ID",
            ):
                assert name not in serve_local.os.environ

            from server import config, llm
            assert config.get_workspace_host() == "https://offline.invalid"
            assert config.get_oauth_token() == "offline-token"

            async def exercise_fakes():
                client = llm.get_llm_client()
                model = await client.chat.completions.create(
                    model="would-be-billable", messages=[])
                assert model.choices[0].message.content == (
                    "Offline model response (no network).")

                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://workspace/api/2.0/genie/spaces/space/start-conversation"
                    ) as response:
                        started = await response.json()
                    async with session.get(
                        "https://workspace/api/2.0/genie/spaces/space/"
                        "conversations/offline-conversation/messages/offline-message"
                    ) as response:
                        completed = await response.json()
                assert started["conversation_id"] == "offline-conversation"
                assert completed["attachments"][0]["text"]["content"] == (
                    "Offline Genie response (no network).")

            asyncio.run(exercise_fakes())
            """
        )
        env = os.environ.copy()
        env.update({
            "DATABRICKS_HOST": "https://real-workspace.example",
            "DATABRICKS_TOKEN": "real-looking-token",
            "DATABRICKS_CONFIG_PROFILE": "DEFAULT",
            "DATABRICKS_WAREHOUSE_ID": "real-warehouse",
            "GENIE_SPACE_ID": "real-space",
        })
        env.pop("AI_VALUE_FLYWHEEL_LOCAL_ONLINE", None)

        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
