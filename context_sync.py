"""Resolve existing QQ bindings and serve Ooptra context RPC without posting chat messages."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path

from .ooptra_client import resolve_group_mapping
from .session_sources import parse_umo, select_source
from .sync_state import SyncState
from .voice_memory import MemoryUnavailable, discover

logger = logging.getLogger(__name__)


class ContextSync:
    def __init__(self, plugin, *, state_path: Path | None = None):
        self.plugin = plugin
        self.state = SyncState(state_path)
        self.task: asyncio.Task | None = None
        self.error = ""
        self.last_operation = ""
        self._lock = asyncio.Lock()
        self._jobs: set[asyncio.Task] = set()
        self.client_id = ""

    def start(self):
        if self.task is None:
            self.client_id = self.state.get("client_id") or uuid.uuid4().hex
            self.state.put("client_id", self.client_id)
            self.task = asyncio.create_task(self._worker(), name="ooptra-astrbot-context")

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        for job in self._jobs:
            job.cancel()
        if self._jobs:
            await asyncio.gather(*self._jobs, return_exceptions=True)
        self._jobs.clear()

    def observe(self, event):
        umo = str(event.unified_msg_origin)
        try:
            parsed = parse_umo(umo)
        except ValueError:
            return
        group = str(event.get_group_id() or "")
        record = {**parsed, "group_id": group, "bot_id": str(event.get_self_id()),
                  "platform_name": event.get_platform_name(),
                  "label": umo, "member_session": str(event.get_session_id()) != group}
        self.state.put("session:" + umo, record)
        sessions = self.state.get("sessions") or {}
        sessions[umo] = {"umo": umo, "name": umo}
        self.state.put("sessions", dict(list(sessions.items())[-256:]))
        if group and event.get_platform_name() == "aiocqhttp":
            self.state.put("source:" + event.get_platform_id() + ":" + group,
                           {"bot_id": event.get_self_id(), "umo": umo,
                            "member_session": str(event.get_session_id()) != group})

    def platforms(self):
        manager = getattr(self.plugin.context, "platform_manager", None)
        return [{"id": platform.meta().id, "name": platform.meta().id}
                for platform in manager.get_insts() if platform.meta().id] if manager else []

    async def source_options(self, platform_id, kind, area="", group_id=""):
        if kind not in {"qq", "friend", "oopz"}:
            raise ValueError("请选择真实 QQ 群或 Oopz 文字频道")
        manager = getattr(self.plugin.context, "platform_manager", None)
        platform = next((item for item in manager.get_insts()
                         if item.meta().id == platform_id and item.meta().name == "aiocqhttp"), None) if manager else None
        if platform is None:
            raise ValueError("请选择当前启用的 AstrBot OneBot 平台实例")
        try:
            client = platform.get_client()
            login = await asyncio.wait_for(client.call_action(action="get_login_info"), 3)
            bot_id = str(login["user_id"])
            if kind == "oopz":
                data = await self.plugin.client.text_channels(area)
                if str(data.get("bot_id", "")) != bot_id:
                    raise ValueError("所选平台不是当前 Ooptra 的 WS 连接，请选择对应的 OneBot 平台")
                groups = [{"id": str(row["group_id"]), "name": str(row["name"]),
                           "text_channel": row["id"], "text_area": area}
                          for row in data.get("channels", [])]
            else:
                rows = await asyncio.wait_for(client.call_action(action="get_friend_list" if kind == "friend" else "get_group_list"), 3)
                if kind == "friend":
                    rows = [{"group_id": row["user_id"], "group_name": row.get("remark") or row.get("nickname") or row["user_id"]} for row in rows]
                groups = [{"id": str(row["group_id"]), "name": str(row.get("group_name") or row["group_id"])}
                          for row in rows if not (row.get("extra") or {}).get("oopz_area_id")]
            for row in groups:
                row["umo"] = f"{platform_id}:{'FriendMessage' if kind == 'friend' else 'GroupMessage'}:{row['id']}"
                row["kind"] = "oopz" if kind == "oopz" else "qq"
            if group_id:
                groups = [row for row in groups if row["id"] == group_id]
            return {"ok": True, "groups": groups[:200], "total": len(groups), "bot_id": bot_id}
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("无法读取所选平台的会话，请检查连接状态；Oopz 来源还需要更新配套 Ooptra") from exc

    def _provider(self):
        rows = discover(self.plugin.context)
        selected = str(self.plugin.config.get("context_memory_provider") or "auto")
        if selected == "none":
            return "", None, "已选择只同步人格"
        if not rows and selected == "auto":
            return "", None, "未检测到受支持的记忆插件"
        if selected == "auto" and len(rows) > 1:
            return "", None, "检测到多个记忆插件，请选择主记忆插件"
        row = next((row for row in rows if selected == "auto" or row["id"] == selected), None)
        if row is None:
            return "", None, "所选记忆插件未启用"
        return row["id"], row.get("adapter"), row.get("error", "")

    def describe(self):
        _, _, error = self._provider()
        manager = getattr(self.plugin.context, "persona_manager", None)
        personas = [{"id": row["name"], "name": row["name"]}
                    for row in getattr(manager, "personas_v3", []) if row.get("name")]
        return {"enabled": bool(self.plugin.config.get("context_sync_enabled", False)),
                "personas": personas, "providers": [
                    {key: value for key, value in row.items() if key != "adapter"}
                    for row in discover(self.plugin.context)], "memory_error": error,
                "error": self.error, "last_operation": self.last_operation,
                "persona_id": self.plugin.config.get("context_persona_id", ""),
                "memory_provider": self.plugin.config.get("context_memory_provider", "auto")}

    def _binding(self, room):
        if not isinstance(room, dict) or not room.get("area") or not room.get("channel"):
            raise ValueError("当前语音房间信息不完整")
        mappings = self.plugin.config.get("group_map") or {}
        matches = []
        for group in dict.fromkeys(str(key) for key in mappings):
            row = resolve_group_mapping(mappings, str(group), preferred_area=room["area"])
            if row and row["area"] == room["area"] and row.get("channel", "") in ("", room["channel"]):
                matches.append(str(group))
        if not matches:
            raise ValueError("当前语音频道没有绑定 QQ 群")
        if len(matches) != 1:
            raise ValueError("当前语音频道绑定了多个 QQ 群，请保留唯一来源绑定")
        return matches[0]

    def _source(self, group, explicit=None):
        mappings = self.plugin.config.get("group_map") or {}
        row = mappings.get(group, mappings.get(int(group), {}))
        override = explicit if explicit is not None else row if isinstance(row, dict) else {}
        selected = str(override.get("platform_id") or self.plugin.config.get("context_platform_id") or "")
        manager = getattr(self.plugin.context, "platform_manager", None)
        platforms = manager.get_insts() if manager else []
        metas = [platform.meta() for platform in platforms if platform.meta().name == "aiocqhttp"
                 and (not selected or platform.meta().id == selected)]
        if len(metas) != 1:
            raise ValueError("无法唯一确定 QQ 平台实例，请配置 QQ 平台 ID")
        meta = metas[0]
        observed = self.state.get("source:" + meta.id + ":" + group) or {}
        if (explicit is not None and override.get("bot_id") and observed.get("bot_id")
                and str(override["bot_id"]) != str(observed["bot_id"])):
            raise ValueError("来源平台登录身份已变化，请重新保存人格与记忆来源")
        if observed.get("member_session"):
            raise ValueError("绑定 QQ 群启用了成员独立会话，无法自动选择唯一群上下文")
        return {"group_id": group, "platform_id": meta.id,
                "umo": observed.get("umo") or f"{meta.id}:GroupMessage:{group}",
                "bot_id": observed.get("bot_id") or override.get("bot_id", ""),
                "source_kind": override.get("kind", "qq"),
                "source_label": override.get("label", ""),
                "explicit_source": explicit is not None,
                "voice_persona_id": override.get("persona_id", "")}

    async def _persona(self, scope, *, follow_group=False):
        context = self.plugin.context
        manager = getattr(context, "persona_manager", None)
        if manager is None:
            raise ValueError("当前 AstrBot 不支持人格管理接口")
        raw = ({"persona_id": scope.get("voice_persona_id", "")} if scope.get("explicit_source") else
               (self.plugin.config.get("group_map") or {}).get(scope["group_id"], {}))
        selected = "" if follow_group else str(
            (raw.get("persona_id") if isinstance(raw, dict) else "") or
            ("" if scope.get("explicit_source") else self.plugin.config.get("context_persona_id")) or "")
        if selected:
            persona = next((row for row in manager.personas_v3 if row["name"] == selected), None)
            if persona is None:
                raise ValueError("所选人格已删除或不可用，请重新选择")
            return selected, str(persona.get("prompt") or "")
        conversation_persona = None
        conversations = getattr(context, "conversation_manager", None)
        if conversations:
            cid = await conversations.get_curr_conversation_id(scope["umo"])
            if cid:
                conv = await conversations.get_conversation(scope["umo"], cid)
                conversation_persona = getattr(conv, "persona_id", None)
        selected, persona, _, _ = await manager.resolve_selected_persona(
            umo=scope["umo"], conversation_persona_id=conversation_persona, platform_name=scope.get("platform_name", "aiocqhttp"))
        return "" if selected in (None, "[%None]") else selected, str((persona or {}).get("prompt") or "")

    def scope_for_umo(self, umo):
        parsed = parse_umo(umo)
        manager = getattr(self.plugin.context, "platform_manager", None)
        platform = next((item for item in manager.get_insts() if item.meta().id == parsed["platform_id"]), None) if manager else None
        if platform is None:
            raise ValueError("UMO 对应的平台实例未启用：" + parsed["platform_id"])
        observed = self.state.get("session:" + parsed["umo"]) or {}
        mapping = (self.plugin.config.get("group_map") or {}).get(umo, {})
        kind = mapping.get("kind", "session") if isinstance(mapping, dict) else "session"
        return {**parsed, "group_id": observed.get("group_id", parsed["group_id"]),
                "bot_id": observed.get("bot_id", ""), "platform_name": platform.meta().name,
                "source_kind": kind, "source_label": mapping.get("label", umo) if isinstance(mapping, dict) else umo,
                "explicit_source": True,
                "voice_persona_id": mapping.get("persona_id", "") if isinstance(mapping, dict) else ""}

    def available_sources(self, area, channel):
        result = []
        mappings = self.plugin.config.get("group_map") or {}
        for key in dict.fromkeys(str(item) for item in mappings):
            row = resolve_group_mapping(mappings, key, preferred_area=area)
            if not row or row["area"] != area or row["channel"] not in ("", channel):
                continue
            try:
                scope = self.scope_for_umo(key) if ":" in key else self._source(key)
            except ValueError:
                continue
            result.append({"umo": scope["umo"], "name": row.get("label") or scope["umo"]})
        return result

    def session_plan(self, room):
        rule = select_source(self.plugin.config, room)
        if rule is None:
            key = self._binding(room)
            if not key.isdigit():
                raise ValueError("完整 UMO 映射需要在会话对应页面明确选择人格与记忆来源")
            scope = self._source(key)
            return scope, [scope], ""
        allowed = {item["umo"] for item in self.available_sources(room["area"], room["channel"])}
        targets = [rule["persona_umo"], *rule.get("memory_umos", [])]
        if any(umo not in allowed for umo in targets):
            raise ValueError("来源会话已解绑或不覆盖当前语音范围，请重新选择")
        scope = self.scope_for_umo(rule["persona_umo"])
        scope["voice_persona_id"] = rule.get("persona_id", "")
        memories = [self.scope_for_umo(umo) for umo in rule.get("memory_umos", [])]
        route_id = hashlib.sha256(json.dumps(rule, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return scope, memories, route_id

    async def recall_all(self, adapter, scopes, payload, event_id):
        async def recall(scope):
            try:
                result = await adapter.recall(scope, payload, event_id)
                if result.get("ok") is not True:
                    raise MemoryUnavailable(result.get("error", "记忆查询失败"))
                return {"umo": scope["umo"], "ok": True, "context": str(result.get("context") or "")[:2000]}
            except Exception:
                return {"umo": scope["umo"], "ok": False, "error": "该会话记忆查询失败或无权限"}
        results = await asyncio.gather(*(recall(scope) for scope in scopes))
        text = "\n".join("[来源 " + row["umo"] + "]\n" + str(row["context"])[:2000]
                         for row in results if row["ok"] and row["context"])
        return {"ok": any(row["ok"] for row in results), "context": text[:12000],
                "partial": any(not row["ok"] for row in results), "targets": results,
                "error": "部分或全部记忆来源不可用" if any(not row["ok"] for row in results) else ""}

    async def write_all(self, adapter, scopes, payload, event_id, operation):
        results = []
        for scope in scopes:
            target_id = event_id + ":" + hashlib.sha256(scope["umo"].encode()).hexdigest()[:12]
            saved = self.state.get("target:" + target_id)
            if saved is not None:
                results.append(saved)
                continue
            clean_payload = dict(payload)
            clean_payload.pop("qq_user_id", None)
            speaker = str(payload.get("speaker") or "")
            links = (self.plugin.config.get("context_identity_links") or {}) if scope.get("source_kind", "qq") == "qq" else {}
            user = links.get(speaker, "") if speaker else ""
            if user:
                if not str(user).isdigit():
                    raise ValueError("成员关联必须填写 QQ 号")
                clean_payload["qq_user_id"] = str(user)
            try:
                if operation == "remember":
                    result = await adapter.remember(scope, clean_payload, target_id)
                else:
                    def stage(role, done=False, target=target_id):
                        key = f"stage:{target}:{role}"
                        if done:
                            self.state.put(key, True)
                        return self.state.get(key) is True
                    result = await adapter.ingest(scope, clean_payload, target_id, stage)
                row = {"umo": scope["umo"], "ok": result.get("ok") is True,
                       "state": result.get("state", ""), "error": result.get("error", "")}
                if row["ok"]:
                    self.state.put("target:" + target_id, row)
            except Exception:
                row = {"umo": scope["umo"], "ok": False, "error": "该会话写回失败或未获授权"}
            results.append(row)
        ok = bool(results) and all(row["ok"] for row in results)
        return {"ok": ok, "state": ("stored" if operation == "remember" else "captured") if ok else "partial",
                "retry": not ok and operation in {"commit", "remember"}, "targets": results,
                "error": "" if ok else "部分记忆写回失败；已成功的目标会保留回执，重试时跳过"}

    async def handle(self, event):
        if isinstance(event, dict) and event.get("operation") == "bindings":
            try:
                payload = event.get("payload") or {}
                action = payload.get("action")
                panel = self.plugin.panel
                if action == "list":
                    return {"ok": True, **panel.bootstrap()}
                if action == "save":
                    return await panel.save_binding(payload)
                if action == "delete":
                    return await panel.delete_binding(payload)
                if action in {"source-save", "source-delete", "source-options"}:
                    return await panel.sources(payload)
                raise ValueError("不支持的绑定操作")
            except (ValueError, TypeError, AttributeError) as exc:
                return {"ok": False, "error": str(exc)[:200]}
        if not self.plugin.config.get("context_sync_enabled", False):
            return {"ok": False, "error": "人格与记忆同步已关闭"}
        try:
            if not isinstance(event, dict) or event.get("operation") not in {"prepare", "recall", "remember", "commit"}:
                raise ValueError("不支持的桥接请求")
            room = event.get("room")
            if not isinstance(room, dict) or not room.get("area") or not room.get("channel"):
                raise ValueError("当前语音房间信息不完整")
            scope, memories, route_id = self.session_plan(room)
            provider, adapter, memory_error = self._provider()
            operation = event["operation"]
            if operation == "prepare":
                scope["persona_id"], persona = await self._persona(scope)
                for memory in memories:
                    memory["memory_persona_id"], _ = await self._persona(memory, follow_group=True)
                memory_context = ""
                if not memories:
                    memory_error = "尚未选择记忆会话，仅同步人格"
                if adapter and memories:
                    try:
                        recall = await asyncio.wait_for(self.recall_all(adapter, memories,
                            {"query": "当前会话最近的共同经历、约定和稳定偏好"}, "initial"), 1.5)
                        memory_context = recall.get("context", "") if recall.get("ok") is True else ""
                        if recall.get("partial"):
                            memory_error = recall["error"]
                    except Exception:
                        memory_error = "初始记忆加载失败，可在对话中重新查询"
                return {"ok": True, **scope, "persona": persona, "provider": provider if adapter and memories else "",
                        "memory_error": memory_error, "memory_context": memory_context,
                        "memory_scopes": memories, "memory_route_id": route_id}
            old = event.get("scope") or {}
            if (any(old.get(key) != scope.get(key) for key in ("group_id", "umo", "platform_id"))
                    or (old.get("memory_route_id") or "") != route_id):
                raise ValueError("人格或记忆来源已变化，请刷新语音上下文；旧记录不会写入新目标")
            if not adapter or old.get("provider") != provider:
                raise MemoryUnavailable(memory_error or "记忆插件已变化，请刷新语音上下文")
            if not memories:
                raise ValueError("尚未选择记忆会话")
            old_scopes = old.get("memory_scopes") or [{**scope,
                "memory_persona_id": old.get("memory_persona_id", old.get("persona_id", ""))}]
            if [item.get("umo") for item in old_scopes] != [item["umo"] for item in memories]:
                raise ValueError("记忆来源列表已变化，请刷新语音上下文")
            for memory, previous in zip(memories, old_scopes, strict=True):
                current, _ = await self._persona(memory, follow_group=True)
                if current != previous.get("memory_persona_id", ""):
                    raise ValueError("来源会话人格已变化，请刷新语音上下文")
                if previous.get("bot_id") and memory.get("bot_id") != previous["bot_id"]:
                    raise ValueError("来源平台登录身份已变化，请刷新语音上下文")
                memory["memory_persona_id"] = current
            payload = dict(event.get("payload") or {})
            event_id = str(event.get("request_id") or "")
            if not event_id or len(event_id) > 100:
                raise ValueError("桥接请求缺少有效轮次 ID")
            if operation == "recall":
                return await self.recall_all(adapter, memories, payload, event_id)
            async with self._lock:
                saved = self.state.get("result:" + event_id)
                if saved is not None:
                    return saved
                result = await self.write_all(adapter, memories, payload, event_id, operation)
                if result["ok"]:
                    self.state.put("result:" + event_id, result)
                return result
        except MemoryUnavailable as exc:
            return {"ok": False, "retry": event.get("operation") in {"commit", "remember"}, "error": str(exc)[:200]}
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)[:200]}

    async def _serve_event(self, event):
        try:
            result = await asyncio.wait_for(self.handle(event),
                18 if event.get("operation") in {"bindings", "commit", "remember"} else 3)
        except Exception as exc:
            logger.warning("Ooptra memory operation failed: %s", type(exc).__name__)
            result = {"ok": False, "retry": event.get("operation") in {"commit", "remember"},
                      "error": "记忆插件执行超时或失败"}
        self.error = "" if result.get("ok") is True else result.get("error", "")
        self.last_operation = event.get("operation", "")
        try:
            await self.plugin.client.context_result(event["request_id"], result, self.client_id)
        except Exception as exc:
            self.error = f"桥接结果回传失败：{type(exc).__name__}"

    async def _worker(self):
        while True:
            try:
                events = await self.plugin.client.context_poll(self.client_id)
                for event in events:
                    if len(self._jobs) >= 8:
                        await asyncio.wait(self._jobs, return_when=asyncio.FIRST_COMPLETED)
                    job = asyncio.create_task(self._serve_event(event))
                    self._jobs.add(job)
                    job.add_done_callback(self._jobs.discard)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = f"桥接连接失败：{type(exc).__name__}"
                await asyncio.sleep(2)
