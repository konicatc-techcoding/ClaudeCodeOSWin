#!/usr/bin/env python3
"""scripts/test_route_model.py — v0.1

route_model.py 的最小測試，重點驗證 resolve_prompt_path() 的路徑邊界檢查
（見 INTEGRATION_TEST.md 測試 3 揪出的中風險安全發現）。

執行：.venv/Scripts/python.exe scripts/test_route_model.py
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import route_model  # noqa: E402


class ResolvePromptPathTests(unittest.TestCase):
    def test_allows_file_inside_project(self):
        target = route_model.ROOT / "scripts" / "requirements.txt"
        self.assertTrue(target.is_file(), "測試前提：這個檔案應該存在")
        resolved = route_model.resolve_prompt_path(str(target))
        self.assertEqual(resolved, target.resolve())

    def test_allows_relative_path_inside_project(self):
        resolved = route_model.resolve_prompt_path("scripts/requirements.txt")
        self.assertEqual(resolved, (route_model.ROOT / "scripts" / "requirements.txt").resolve())

    def test_rejects_path_traversal_outside_project(self):
        with self.assertRaises(SystemExit):
            route_model.resolve_prompt_path("../../../../../../etc/passwd")

    def test_rejects_absolute_path_outside_project(self):
        with self.assertRaises(SystemExit):
            route_model.resolve_prompt_path("/etc/passwd")

    def test_rejects_home_directory_secrets(self):
        with self.assertRaises(SystemExit):
            route_model.resolve_prompt_path(str(Path.home() / ".ssh" / "id_rsa"))

    def test_rejects_nonexistent_file_inside_project(self):
        with self.assertRaises(SystemExit):
            route_model.resolve_prompt_path("scripts/this_file_does_not_exist.txt")

    def test_allows_file_in_project_temp_subdir(self):
        with tempfile.TemporaryDirectory(dir=route_model.ROOT) as tmpdir:
            f = Path(tmpdir) / "prompt.txt"
            f.write_text("hello", encoding="utf-8")
            resolved = route_model.resolve_prompt_path(str(f))
            self.assertEqual(resolved, f.resolve())


class ResolveRouteTests(unittest.TestCase):
    """capability-based routing：驗證 registry/model_router.yaml 真的長這樣，
    不是用假設定檔——這份 registry 現在是五個領域 default_capability 的
    真相來源，改壞了要馬上發現。
    """

    def setUp(self):
        self.config = route_model.load_config()

    def test_complex_coding_routes_to_native(self):
        # 2026-07-20：OpenRouter provider 路徑已移除（OPENROUTER_API_KEY 從未
        # 真正設定過，實務上從未打通）。complex_coding 這個 capability key
        # 仍保留在 model_router.yaml——registry/capability_lanes.yaml 的
        # hermes-nemocoding／hermes-gptcoding 兩條 active lane 仍引用它做
        # capability 標記——但 via 已改成 native，不再對外呼叫。
        route = route_model.resolve_route(self.config, "complex_coding")
        self.assertEqual(route["via"], "native")

    def test_bulk_research_routes_to_native(self):
        # 同上，bulk_research 仍保留給 hermes-financialresearch／
        # hermes-intelligence 兩條 active lane 引用，但 via 已改成 native。
        route = route_model.resolve_route(self.config, "bulk_research")
        self.assertEqual(route["via"], "native")

    def test_google_ecosystem_no_longer_a_known_capability(self):
        # google_ecosystem 沒有任何 lane 或 default_capability 依賴它，
        # 2026-07-20 隨 OpenRouter provider 路徑整個移除。
        self.assertNotIn("google_ecosystem", self.config.get("routes", {}))

    def test_claude_native_routes_to_native(self):
        route = route_model.resolve_route(self.config, "claude_native")
        self.assertEqual(route["via"], "native")
        self.assertEqual(route["model"], "claude")

    def test_unknown_capability_falls_back_to_default_native(self):
        route = route_model.resolve_route(self.config, "totally_made_up_capability")
        self.assertEqual(route["via"], "native")
        self.assertEqual(route["model"], self.config.get("default", "claude"))


class AgentsRegistryConsistencyTests(unittest.TestCase):
    """兩份 registry 之間的參照完整性：agents.yaml 的 default_capability
    一定要是 model_router.yaml 裡真的存在的 capability，不然 subagent
    查到的名字在 route_model.py 裡只會落到 fallback，不會噴錯，
    但那是靜默的設定錯誤，值得測。
    """

    def test_every_active_domain_has_default_capability_that_exists(self):
        import yaml

        agents_config = yaml.safe_load(
            (route_model.ROOT / "registry" / "agents.yaml").read_text(encoding="utf-8")
        )
        router_config = route_model.load_config()
        known_capabilities = set(router_config.get("routes", {}).keys())

        for agent in agents_config["agents"]:
            if agent.get("status") != "active":
                continue
            self.assertIn(
                "default_capability", agent,
                msg=f"{agent['id']} 是 active 但沒有 default_capability",
            )
            self.assertIn(
                agent["default_capability"], known_capabilities,
                msg=f"{agent['id']} 的 default_capability='{agent['default_capability']}' "
                    f"在 model_router.yaml 裡不存在",
            )


class MainNonNativeViaFailsVisibleTests(unittest.TestCase):
    """2026-07-20：OpenRouter provider 路徑（call_openrouter）已整個移除，
    main() 目前只認得 via=native；registry/model_router.yaml 現在也確實不再
    有任何 via 不是 native 的 route。這裡用合成 config（monkeypatch
    load_config）驗證：萬一 registry 之後又出現 via 不是 native 的 route，
    main() 會 fail-visible（sys.exit）而不是嘗試呼叫一個已經不存在的
    call_openrouter 函式而炸成 AttributeError。
    """

    def test_non_native_via_exits_with_clear_message(self):
        fake_config = {"default": "claude", "routes": {"some_capability": {"via": "some-other-provider"}}}
        prompt_path = route_model.ROOT / "scripts" / "requirements.txt"
        with mock.patch.object(route_model, "load_config", return_value=fake_config), \
             mock.patch.object(sys, "argv", ["route_model.py", "some_capability", str(prompt_path)]):
            with self.assertRaises(SystemExit) as ctx:
                route_model.main()
        self.assertIn("via", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
