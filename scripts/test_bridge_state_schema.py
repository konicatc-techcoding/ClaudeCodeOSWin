#!/usr/bin/env python3
"""scripts/test_bridge_state_schema.py — v0.2（Stage 2.4d-1）

registry/bridge_state_schema.yaml 的 parse/shape check（格式契約的把關）。
v2 起同時涵蓋 bridge_sessions（22 欄）與 bridge_cursors（5 欄、複合主鍵）。
runtime 行為（DDL 對齊、migration、create_episode）的測試在
hermes/test_bridge_state.py，這裡只把關 yaml 契約本身。

執行：.venv/Scripts/python.exe scripts/test_bridge_state_schema.py
"""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "registry" / "bridge_state_schema.yaml"
LANES_PATH = ROOT / "registry" / "capability_lanes.yaml"

EXPECTED_FIELDS = {
    # 17 欄（v1 原樣）
    "session_id", "source_profile", "session_source", "import_status", "memory_type",
    "useful_chat", "selected_capability_lane", "decision_reason", "imported_inbox_path",
    "processed_path", "first_seen_at", "last_seen_at", "updated_at", "retry_count",
    "error_reason", "event_id", "event_id_range",
    # 5 新欄（v2，Stage 2.4d episode capture）
    "episode_seq", "capture_trigger", "first_message_id", "last_message_id",
    "source_content_hash",
}
EXPECTED_STATUS_VALUES = {"discovered", "skipped", "to_inbox", "imported", "failed", "needs_review"}
EXPECTED_MEMORY_TYPES = {"procedural", "semantic", "episodic", "none"}
EXPECTED_CAPTURE_TRIGGERS = {"ended", "inactivity", "manual", "legacy"}
EXPECTED_CURSOR_FIELDS = {
    "source_profile", "session_id", "last_captured_message_id",
    "last_episode_seq", "updated_at",
}
VALID_TYPES = {"string", "enum", "bool", "int"}


class BridgeStateSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.fields = cls.doc.get("fields", {})
        cls.cursors = cls.doc.get("bridge_cursors", {})
        cls.cursor_fields = cls.cursors.get("fields", {})

    def _assert_field_shape(self, name, spec):
        self.assertIn(spec.get("type"), VALID_TYPES, msg=f"欄位 {name} 的 type 不合法")
        self.assertIn("required", spec, msg=f"欄位 {name} 缺 required")
        self.assertIsInstance(spec["required"], bool, msg=f"欄位 {name} 的 required 要是 bool")
        self.assertTrue(spec.get("description"), msg=f"欄位 {name} 缺 description")
        if spec["type"] == "enum":
            self.assertTrue(spec.get("values"), msg=f"enum 欄位 {name} 缺 values")

    def test_schema_marker_and_status(self):
        """v2 marker（Stage 2.4d：真的有 migration——加欄＋新表，見提案 §1.3）。
        status 不再是 definition-only：Stage 2.2 起已有 runtime 寫入者
        （bridge_state.py／scanner／importer）。"""
        self.assertEqual(self.doc.get("schema"), "claudecodeos.bridge_state.v2")
        self.assertEqual(self.doc.get("status"), "active",
                         "v2 起有 runtime 寫入者與存量資料，definition-only 已不成立")

    def test_all_expected_fields_present(self):
        self.assertEqual(set(self.fields.keys()), EXPECTED_FIELDS,
                         "bridge_sessions 必須恰為 22 欄（17 原樣＋5 episode 欄）")

    def test_field_shapes(self):
        for name, spec in self.fields.items():
            self._assert_field_shape(name, spec)

    def test_import_status_enum_values(self):
        self.assertEqual(set(self.fields["import_status"]["values"]), EXPECTED_STATUS_VALUES)

    def test_memory_type_enum_values(self):
        self.assertEqual(set(self.fields["memory_type"]["values"]), EXPECTED_MEMORY_TYPES)

    def test_idempotency_key_is_required(self):
        self.assertTrue(self.fields["event_id"]["required"], "event_id 是去重依據，必填")

    def test_event_id_declares_three_namespaces_and_profile_reservation(self):
        """v2 的 event_id description 必須記載三層 namespace（session/訊息/episode）
        與未來 profile namespace 預留（"hermes/" 前綴本階段 fail-closed 拒絕，
        提案 §2、§6.1）。"""
        desc = self.fields["event_id"]["description"]
        for needle in ("hermes:<session_id>", "..", "hermes/", "fail-closed"):
            self.assertIn(needle, desc,
                          msg=f"event_id description 缺 namespace 記載：{needle!r}")

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

    # ---------- v2 新欄（episode capture，提案 §1.2） ----------

    def test_capture_trigger_enum_values(self):
        spec = self.fields["capture_trigger"]
        self.assertEqual(spec["type"], "enum")
        self.assertEqual(set(spec["values"]), EXPECTED_CAPTURE_TRIGGERS)

    def test_episode_fields_optional_with_conditional_rule_documented(self):
        """五個 episode 欄在 schema 層都是 optional（legacy 列 NULL），條件必填
        （episode 列必填）由 repository 驗證——description 必須明文 episode／
        legacy 兩種列的條件（比照 error_reason／imported_inbox_path 慣例）。"""
        for name in ("episode_seq", "capture_trigger", "first_message_id",
                     "last_message_id", "source_content_hash"):
            spec = self.fields[name]
            self.assertFalse(spec["required"],
                             msg=f"{name} 在 schema 層必須 optional（legacy 列 NULL）")
            self.assertIn("episode", spec["description"],
                          msg=f"{name} 的 description 須說明 episode 列語義")
            self.assertIn("legacy", spec["description"],
                          msg=f"{name} 的 description 須說明 legacy 列 NULL 語義")

    def test_boundary_fields_are_int(self):
        for name in ("first_message_id", "last_message_id"):
            self.assertEqual(self.fields[name]["type"], "int",
                             msg=f"{name} 是 Hermes rowid，型別 int")

    def test_source_content_hash_declares_pure_function_semantics(self):
        desc = self.fields["source_content_hash"]["description"]
        self.assertIn("SHA-256", desc,
                      "source_content_hash 須宣告 SHA-256（提案 §4.5 純函式定義）")

    # ---------- bridge_cursors（v2 新表，提案 §1.2） ----------

    def test_cursor_table_block_present_with_composite_pk(self):
        """bridge_cursors 區塊存在，複合主鍵恰為 (source_profile, session_id)
        ——順序也要對（不同 profile 的同名 session 絕不共用 cursor，§6.1）。"""
        self.assertTrue(self.cursors, "yaml 缺 bridge_cursors 區塊")
        self.assertEqual(self.cursors.get("primary_key"),
                         ["source_profile", "session_id"])

    def test_cursor_fields_present_and_all_required(self):
        self.assertEqual(set(self.cursor_fields.keys()), EXPECTED_CURSOR_FIELDS)
        for name, spec in self.cursor_fields.items():
            self._assert_field_shape(name, spec)
            self.assertTrue(spec["required"],
                            msg=f"bridge_cursors.{name} 全欄必填（純簿記表，無 optional 欄）")

    def test_cursor_semantics_documented(self):
        """cursor 的關鍵語義必須明文：last_captured_message_id 只前進不後退；
        本表沒有 import_status（session 層級不設狀態機，提案 §4.2）。"""
        self.assertIn("只前進不後退",
                      self.cursor_fields["last_captured_message_id"]["description"])
        self.assertNotIn("import_status", set(self.cursor_fields.keys()),
                         "bridge_cursors 不得有 import_status——session 層級不設狀態機")


if __name__ == "__main__":
    unittest.main()
