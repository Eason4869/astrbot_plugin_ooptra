"""Select one persona and multiple read/write sessions independently of control."""

from __future__ import annotations

import re


def parse_umo(value):
    if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("请填写完整 UMO，例如 Bot:GroupMessage:12345678")
    match = re.fullmatch(r"([^:\s]{1,160}):(GroupMessage|FriendMessage):([^\s]{1,256})", value.strip())
    if not match:
        raise ValueError("UMO 格式无效，请使用 实例名:GroupMessage:群ID 或 实例名:FriendMessage:用户ID")
    platform, message_type, session = match.groups()
    return {"umo": value.strip(), "platform_id": platform, "message_type": message_type,
            "session_id": session, "group_id": session if message_type == "GroupMessage" else "",
            "target_user_id": session if message_type == "FriendMessage" else ""}


def binding_key(value):
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value.strip()):
        return value.strip()  # Read/write compatibility for pre-UMO configurations.
    return parse_umo(value)["umo"]


def legacy_alias(key):
    return int(key) if key.isdigit() else key


def select_source(config, room):
    settings = config.get("context_sources") or {}
    if not isinstance(settings, dict) or "rules" not in settings:
        return None  # Legacy unique control binding, until explicitly configured.
    rules = settings["rules"]
    if not isinstance(rules, list):
        raise ValueError("人格与记忆来源配置无效")
    exact, domain = [], []
    for row in rules:
        if not isinstance(row, dict):
            raise ValueError("人格与记忆来源配置无效")
        if row.get("area") != room.get("area"):
            continue
        if row.get("channel") == room.get("channel"):
            exact.append(row)
        elif not row.get("channel"):
            domain.append(row)
    candidates = exact or domain
    if len(candidates) > 1:
        raise ValueError("同一语音范围配置了多个来源，请保留一个")
    if not candidates:
        raise ValueError("当前语音房未选择人格与记忆来源，请在会话对应页面设置")
    selected = candidates[0]
    parse_umo(selected.get("persona_umo"))
    memories = selected.get("memory_umos", [])
    if not isinstance(memories, list) or len(memories) > 8 or any(not isinstance(umo, str) for umo in memories):
        raise ValueError("记忆来源最多选择 8 个互不重复的 UMO")
    for umo in memories:
        parse_umo(umo)
    if len(set(memories)) != len(memories):
        raise ValueError("记忆会话不能重复")
    if (any(parse_umo(umo)["message_type"] == "FriendMessage"
            for umo in [selected["persona_umo"], *memories]) and selected.get("allow_private") is not True):
        raise ValueError("私聊来源需要明确确认将所选私聊上下文用于公共语音房")
    return selected
