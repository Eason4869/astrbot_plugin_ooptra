"""ooptra_client 纯函数单测（不依赖 AstrBot）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ooptra_client import (  # noqa: E402
    format_members,
    format_status,
    looks_like_id,
    looks_like_label,
    resolve_group_mapping,
)


class TestResolveGroupMapping(unittest.TestCase):
    def test_dict_mapping(self):
        m = resolve_group_mapping(
            {"123": {"area": "A", "channel": "C", "label": "L"}}, "123"
        )
        self.assertEqual(m, {"area": "A", "channel": "C", "label": "L"})

    def test_int_key_compat(self):
        m = resolve_group_mapping({123: {"area": "A", "channel": "C"}}, "123")
        self.assertEqual(m["area"], "A")

    def test_string_compact(self):
        m = resolve_group_mapping({"9": "area1:chan1"}, "9")
        self.assertEqual(m["area"], "area1")
        self.assertEqual(m["channel"], "chan1")

    def test_string_hash(self):
        m = resolve_group_mapping({"9": "area1#chan1"}, "9")
        self.assertEqual(m["channel"], "chan1")

    def test_missing(self):
        self.assertIsNone(resolve_group_mapping({}, "1"))
        self.assertIsNone(resolve_group_mapping(None, "1"))
        self.assertIsNone(resolve_group_mapping({"1": {}}, None))


class TestIdHeuristics(unittest.TestCase):
    def test_label_has_cjk(self):
        self.assertTrue(looks_like_label("开黑房"))
        self.assertFalse(looks_like_label("room-a"))

    def test_id_safe(self):
        self.assertTrue(looks_like_id("6ad0261bc4eb41e882692531981a0972"))
        self.assertTrue(looks_like_id("chan-uid-xxx"))
        self.assertFalse(looks_like_id("开黑房"))
        self.assertFalse(looks_like_id(""))
        self.assertFalse(looks_like_id("bad id with space"))


class TestFormat(unittest.TestCase):
    def test_members(self):
        text = format_members(
            {
                "count": 2,
                "members": [
                    {"name": "小明", "mic": False},
                    {"name": "小红", "mic": True, "speaker": 1},
                ],
            },
            label="开黑房",
        )
        self.assertIn("开黑房", text)
        self.assertIn("小明", text)
        self.assertIn("闭麦", text)
        self.assertNotIn("闭麦", text.split("小红")[1])

    def test_members_count_mismatch(self):
        text = format_members({"count": 5, "members": [{"name": "甲"}]})
        self.assertIn("在线 5 人", text)
        self.assertIn("列表 1 人", text)

    def test_members_hm_fields(self):
        # Oopz m/hm：1=闭麦/闭听，0=正常
        text = format_members({"members": [{"name": "乙", "m": 1, "hm": 1}]})
        self.assertIn("闭麦", text)
        self.assertIn("闭听", text)

        text_ok = format_members({"members": [{"name": "丙", "m": 0, "hm": 0}]})
        self.assertNotIn("闭麦", text_ok)
        self.assertNotIn("闭听", text_ok)

    def test_status_joined(self):
        text = format_status(
            {"joined": True, "area": "A", "channel": "C", "state": "playing"}
        )
        self.assertIn("已在房", text)
        self.assertIn("A / C", text)

    def test_status_idle(self):
        self.assertIn("未进房", format_status({"joined": False}))


if __name__ == "__main__":
    unittest.main()
