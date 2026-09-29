"""ooptra_client 纯函数单测（不依赖 AstrBot）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ooptra_client import (  # noqa: E402
    channel_rows,
    format_channel_counts,
    format_members,
    format_status,
    looks_like_id,
    looks_like_label,
    resolve_channel,
    resolve_group_mapping,
)


class TestResolveGroupMapping(unittest.TestCase):
    def test_dict_mapping(self):
        m = resolve_group_mapping(
            {"123": {"area": "A", "channel": "C", "label": "L"}}, "123"
        )
        self.assertEqual(m["area"], "A")
        self.assertEqual(m["areas"], ["A"])
        self.assertEqual(m["channel"], "C")
        self.assertEqual(m["label"], "L")

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

    def test_multi_area_preferred(self):
        m = resolve_group_mapping(
            {"123": {"areas": ["A1", "A2"], "channel": "C"}}, "123", preferred_area="A2"
        )
        self.assertEqual(m["area"], "A2")
        self.assertEqual(m["areas"], ["A1", "A2"])

    def test_multi_area_first_when_no_preferred(self):
        m = resolve_group_mapping(
            {"123": {"area": "A1、A2", "channel": "C"}}, "123", preferred_area=""
        )
        self.assertEqual(m["area"], "A1")
        self.assertEqual(m["areas"], ["A1", "A2"])

    def test_multi_area_preferred_not_in_list(self):
        m = resolve_group_mapping(
            {"123": {"areas": ["A1", "A2"]}}, "123", preferred_area="A9"
        )
        self.assertEqual(m["area"], "A1")


class TestChannelHelpers(unittest.TestCase):
    def test_channel_rows(self):
        rows = channel_rows(
            {"channels": [{"id": "c1", "name": "开黑房", "count": 3}, {"id": "c2", "count": 0}]}
        )
        self.assertEqual(rows[0], {"id": "c1", "name": "开黑房", "count": 3})
        self.assertEqual(rows[1]["name"], "c2")

    def test_format_channel_counts_occupied(self):
        text = format_channel_counts(
            {
                "channels": [
                    {"id": "c1", "name": "开黑房", "count": 3},
                    {"id": "c2", "name": "闲聊房", "count": 0},
                    {"id": "c3", "name": "挂机房", "count": 1},
                ]
            }
        )
        self.assertIn("开黑房 3人", text)
        self.assertIn("挂机房 1人", text)
        self.assertNotIn("闲聊房", text)

    def test_format_channel_counts_empty(self):
        text = format_channel_counts({"channels": [{"id": "c1", "name": "空房", "count": 0}]})
        self.assertIn("没有人", text)

    def test_resolve_channel_by_name_and_id(self):
        rows = [
            {"id": "c1", "name": "开黑房", "count": 1},
            {"id": "c2", "name": "闲聊房", "count": 0},
        ]
        self.assertEqual(resolve_channel(rows, "开黑房")["id"], "c1")
        self.assertEqual(resolve_channel(rows, "c2")["id"], "c2")
        self.assertEqual(resolve_channel(rows, "开黑")["id"], "c1")
        self.assertIsNone(resolve_channel(rows, "不存在"))


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
