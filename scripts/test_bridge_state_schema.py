#!/usr/bin/env python3
"""scripts/test_bridge_state_schema.py — v0.1

registry/bridge_state_schema.yaml 的 parse/shape check（格式契約的把關，
不是 runtime 測試——bridge 本身是 Stage 2 才實作）。

執行：.venv/Scripts/python.exe scripts/test_bridge_state_schema.py
"""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "registry" / "bridge_state_schema.yaml"
LANES_PATH = ROOT / "registry" / "capability_lanes.yaml"

EXPECTED_FIELDS = {
    "session_id", "source_profile", "session_source", "import_status", "memory_type",
    "useful_chat", "selected_capability_lane", "decision_reason", "imported_inbox_path",
    "processed_path", "first_seen_at", "last_seen_at", "updated_at", "retry_count",
    "error_reason", "event_id", "event_id_range",
}
EXPECTED_STATUS_VALUES = {"discovered", "skipped", "to_inbox", "imported", "failed", "needs_review"}
EXPECTED_MEMORY_TYPES = {"procedural", "semantic", "episodic", "none"}
VALID_TYPES = {"string", "enum", "bool", "int"}


class BridgeStateSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.fields = cls.doc.get("fields", {})

    def test_schema_marker_and_status(self):
        self.assertEqual(self.doc.get("schema"), "claudecodeos.bridge_state.v1")
        self.assertEqual(self.doc.get("status"), "definition-only",
                         "Stage 1 收斂階段只允許 definition-only；Stage 2 實作時才改")

    def test_all_expected_fields_present(self):
        self.assertEqual(set(self.fields.keys()), EXPECTED_FIELDS)

    def test_field_shapes(self):
        for name, spec in self.fields.items():
            self.assertIn(spec.get("type"), VALID_TYPES, msg=f"欄位 {name} 的 type 不合法")
            self.assertIn("required", spec, msg=f"欄位 {name} 缺 required")
            self.assertIsInstance(spec["required"], bool, msg=f"欄位 {name} 的 required 要是 bool")
            self.assertTrue(spec.get("description"), msg=f"欄位 {name} 缺 description")
            if spec["type"] == "enum":
                self.assertTrue(spec.get("values"), msg=f"enum 欄位 {name} 缺 values")

    def test_import_status_enum_values(self):
        self.assertEqual(set(self.fields["import_status"]["values"]), EXPECTED_STATUS_VALUES)

    def test_memory_type_enum_values(self):
        self.assertEqual(set(self.fields["memory_type"]["values"]), EXPECTED_MEMORY_TYPES)

    def test_idempotency_key_is_required(self):
        self.assertTrue(self.fields["event_id"]["required"], "event_id 是去重依據，必填")

    def test_retry_count_is_required_int(self):
        """retry_count 是 bridge 層級的匯入嘗試次數——誠實型別 int、必填；
        與 hermes/db.py jobs.attempts（job 執行重試）完全不同層，description
        必須明文劃清分工。"""
        spec = self.fields["retry_count"]
        self.assertEqual(spec["type"], "int")
        self.assertTrue(spec["required"], "retry_count 必填（default 0）")
        self.assertIn("attempts", spec["description"],
                      "description 必須明文與 jobs.attempts 劃清分工")

    def test_timestamp_fields_declare_iso8601(self):
        """三個 timestamp 欄位（first_seen_at/last_seen_at/updated_at）都是
        string、必填，且 description 宣告 ISO 8601 格式。"""
        for name in ("first_seen_at", "last_seen_at", "updated_at"):
            spec = self.fields[name]
            self.assertEqual(spec["type"], "string", msg=f"{name} 應為 string")
            self.assertTrue(spec["required"], msg=f"{name} 必填")
            self.assertIn("ISO 8601", spec["description"],
                          msg=f"{name} 的 description 須宣告 ISO 8601 格式")

    def test_path_fields_are_optional(self):
        """imported_inbox_path／processed_path 為 optional（僅特定 import_status
        下才有值；processed_path 只是追蹤快取，目錄位置才是唯一真相）。"""
        self.assertFalse(self.fields["imported_inbox_path"]["required"])
        self.assertFalse(self.fields["processed_path"]["required"])
        self.assertIn("目錄位置", self.fields["processed_path"]["description"],
                      "processed_path 的 description 須明文目錄位置為唯一真相")

    def test_lane_reference_field_aligns_with_lanes_registry(self):
        """selected_capability_lane 引用 capability_lanes.yaml 的 lane id——
        兩份 registry 要能互相對得上（這裡只驗證 lanes registry 存在且可解析、
        有 id 可供引用；實際值的驗證是 Stage 2 runtime 的事）。"""
        lanes_doc = yaml.safe_load(LANES_PATH.read_text(encoding="utf-8"))
        lane_ids = [lane["id"] for lane in lanes_doc.get("lanes", [])]
        self.assertGreater(len(lane_ids), 0)
        self.assertFalse(self.fields["selected_capability_lane"]["required"],
                         "selected_capability_lane 是選填（無法對應時省略）")


if __name__ == "__main__":
    unittest.main()
