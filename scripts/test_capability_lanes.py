#!/usr/bin/env python3
"""scripts/test_capability_lanes.py — v0.1

registry/capability_lanes.yaml 的 parse/shape check。
不修改 route_model.py 的執行邏輯——lane 不是 runtime 輸入，這裡只驗證
「schema 形狀正確」與「跟既有 registry（model_router.yaml、agents.yaml）
的參照一致性」，避免三份 registry 各自漂移。

執行：.venv/Scripts/python.exe scripts/test_capability_lanes.py
"""
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LANES_PATH = ROOT / "registry" / "capability_lanes.yaml"
ROUTER_PATH = ROOT / "registry" / "model_router.yaml"
AGENTS_PATH = ROOT / "registry" / "agents.yaml"

REQUIRED_FIELDS = [
    "id", "capability", "execution", "provider", "status",
    "cost_tier", "risk_tier", "allowed_agents", "intended_use", "guardrails",
]
EXECUTIONS = {"native", "route_model", "hermes_profile"}
PROVIDERS = {"anthropic", "openrouter", "hermes"}
STATUSES = {"active", "reference"}
COST_TIERS = {"included", "free", "paid", "unknown"}
RISK_TIERS = {"low", "medium", "high"}


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class CapabilityLanesShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load_yaml(LANES_PATH)
        cls.router = load_yaml(ROUTER_PATH)
        cls.agents = load_yaml(AGENTS_PATH)
        cls.lanes = cls.doc.get("lanes", [])
        cls.lane_ids = [lane.get("id") for lane in cls.lanes]

    def test_schema_marker(self):
        self.assertEqual(self.doc.get("schema"), "claudecodeos.capability_lanes.v1")

    def test_has_lanes(self):
        self.assertGreater(len(self.lanes), 0, "至少要有一條 lane")

    def test_lane_ids_unique(self):
        self.assertEqual(len(self.lane_ids), len(set(self.lane_ids)), "lane id 不可重複")

    def test_required_fields_and_enums(self):
        for lane in self.lanes:
            lid = lane.get("id", "<missing id>")
            for field in REQUIRED_FIELDS:
                self.assertIn(field, lane, msg=f"lane '{lid}' 缺少必填欄位 {field}")
            self.assertIn(lane["execution"], EXECUTIONS, msg=f"lane '{lid}' execution 不合法")
            self.assertIn(lane["provider"], PROVIDERS, msg=f"lane '{lid}' provider 不合法")
            self.assertIn(lane["status"], STATUSES, msg=f"lane '{lid}' status 不合法")
            self.assertIn(lane["cost_tier"], COST_TIERS, msg=f"lane '{lid}' cost_tier 不合法")
            self.assertIn(lane["risk_tier"], RISK_TIERS, msg=f"lane '{lid}' risk_tier 不合法")
            self.assertIsInstance(lane["allowed_agents"], list, msg=f"lane '{lid}' allowed_agents 要是清單")
            self.assertGreater(len(lane["allowed_agents"]), 0, msg=f"lane '{lid}' allowed_agents 不可為空")
            self.assertIsInstance(lane["guardrails"], list, msg=f"lane '{lid}' guardrails 要是清單")
            self.assertGreater(len(lane["guardrails"]), 0, msg=f"lane '{lid}' guardrails 不可為空")

    def test_unknown_cost_tier_only_on_reference_lanes(self):
        for lane in self.lanes:
            if lane["cost_tier"] == "unknown":
                self.assertEqual(
                    lane["status"], "reference",
                    msg=f"lane '{lane['id']}'：cost_tier=unknown 只允許出現在 status=reference 的 lane",
                )

    def test_capability_exists_in_model_router(self):
        routes = self.router.get("routes", {})
        for lane in self.lanes:
            self.assertIn(
                lane["capability"], routes,
                msg=f"lane '{lane['id']}' 的 capability='{lane['capability']}' 在 model_router.yaml 不存在",
            )

    def test_every_router_capability_has_a_lane(self):
        covered = {lane["capability"] for lane in self.lanes}
        for capability in self.router.get("routes", {}):
            self.assertIn(
                capability, covered,
                msg=f"model_router.yaml 的 capability '{capability}' 沒有對應任何 lane",
            )

    def test_route_model_lanes_match_router(self):
        """execution=route_model 的 lane 必須跟 model_router.yaml 的對應 route 一致——
        真相來源是 router，lane 只是同步的治理描述，漂移了要馬上發現。"""
        routes = self.router.get("routes", {})
        for lane in self.lanes:
            if lane["execution"] != "route_model":
                continue
            route = routes[lane["capability"]]
            self.assertEqual(route.get("via"), "openrouter",
                             msg=f"lane '{lane['id']}'：execution=route_model 但 router 的 via 不是 openrouter")
            self.assertEqual(
                lane.get("model"), route.get("openrouter_model"),
                msg=f"lane '{lane['id']}' 的 model 跟 router 的 openrouter_model 不同步",
            )

    def test_native_lanes_match_router(self):
        routes = self.router.get("routes", {})
        for lane in self.lanes:
            if lane["execution"] != "native":
                continue
            route = routes[lane["capability"]]
            self.assertEqual(route.get("via"), "native",
                             msg=f"lane '{lane['id']}'：execution=native 但 router 的 via 不是 native")
            self.assertIsNone(lane.get("model"),
                              msg=f"lane '{lane['id']}'：native lane 的 model 應為 null")

    def test_hermes_profile_lanes_have_profile_field(self):
        for lane in self.lanes:
            if lane["execution"] != "hermes_profile":
                continue
            self.assertTrue(lane.get("hermes_profile"),
                            msg=f"lane '{lane['id']}'：execution=hermes_profile 必須填 hermes_profile")
            self.assertIsNone(lane.get("model"),
                              msg=f"lane '{lane['id']}'：hermes_profile lane 不重複記載模型，model 應為 null")

    def test_hermes_profile_lanes_may_be_active_or_reference(self):
        """v0.1 起：scripts/dispatch_domain.py（Domain Execution Router，Phase 1）
        已經能真的執行 hermes_profile lane（明確 --lane opt-in，見 dispatch_domain.py
        的 select_lane：自動選路徑只挑 status=active，但明確指定的 lane 不受此限）。
        「所有 hermes_profile lane 必須是 reference」這個舊斷言因此不再成立——
        status 這個欄位現在的語意是「自動選路徑會不會挑到它」，不是「有沒有
        runtime 能執行它」。這裡只驗證 status 本身合法，不對 hermes_profile lane
        的 status 值做額外限制。目前（Phase 1 完工時點）本檔仍把五條 hermes-*
        lane 全部留在 reference——理由見完工回報，不在這裡重複記載。"""
        for lane in self.lanes:
            if lane["execution"] != "hermes_profile":
                continue
            self.assertIn(lane["status"], STATUSES, msg=f"lane '{lane['id']}' status 不合法")

    def test_allowed_agents_exist_in_agents_registry(self):
        known_agents = {agent["id"] for agent in self.agents.get("agents", [])}
        for lane in self.lanes:
            for agent_id in lane["allowed_agents"]:
                self.assertIn(
                    agent_id, known_agents,
                    msg=f"lane '{lane['id']}' 的 allowed_agents 含未知 agent '{agent_id}'",
                )

    def test_fallback_lane_references_existing_lane(self):
        for lane in self.lanes:
            fallback = lane.get("fallback_lane")
            if fallback is None:
                continue
            self.assertIn(fallback, self.lane_ids,
                          msg=f"lane '{lane['id']}' 的 fallback_lane='{fallback}' 不存在")
            self.assertNotEqual(fallback, lane["id"],
                                msg=f"lane '{lane['id']}' 的 fallback_lane 不可指向自己")


if __name__ == "__main__":
    unittest.main()
