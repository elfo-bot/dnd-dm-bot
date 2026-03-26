from __future__ import annotations

import json
import re
from typing import Any, Dict

from telegram import Update
from telegram.ext import ContextTypes

from db import combat as combat_db
from db.characters import get_characters, add_xp, update_character
from handlers.character import handle_levelup


TAG_PATTERN = re.compile(r"\[(SYNC|XP|GIVE):(\{.*?\})\]")


async def _apply_sync(campaign_id: str, payload: Dict[str, Any]) -> None:
    """更新戰場上實體的 HP / 座標。"""
    combat = combat_db.get_active_combat(campaign_id)
    if not combat:
        return

    entities = combat_db.get_entities(combat["id"])
    by_name = {e["name"]: e for e in entities}

    for ent in payload.get("entities", []):
        name = ent.get("name")
        if not name or name not in by_name:
            continue
        db_ent = by_name[name]
        updates: Dict[str, Any] = {}
        if "hp" in ent:
            updates["hp"] = ent["hp"]
        if "x" in ent:
            updates["x"] = ent["x"]
        if "y" in ent:
            updates["y"] = ent["y"]
        if updates:
            combat_db.update_entity(db_ent["id"], updates)


async def _apply_xp(
    campaign_id: str,
    payload: Dict[str, Any],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """根據 [XP:{...}] 標籤為指定角色加 XP 並觸發升級流程。"""
    chars = get_characters(campaign_id)
    by_name = {c["name"]: c for c in chars}

    for name, amount in payload.items():
        if not isinstance(amount, int):
            continue
        char = by_name.get(name)
        if not char:
            continue
        before_level = char.get("level", 1)
        updated_char, leveled_up = add_xp(char["id"], amount)
        if leveled_up or updated_char.get("level", before_level) > before_level:
            # 觸發既有升級流程（會更新 HP / 特性 / 法術 / 物品等）
            await handle_levelup(update, context, updated_char)


async def _apply_give(campaign_id: str, payload: Dict[str, Any]) -> None:
    """根據 [GIVE:{...}] 標籤為指定角色加入物品到背包。"""
    chars = get_characters(campaign_id)
    by_name = {c["name"]: c for c in chars}

    for name, items in payload.items():
        if not isinstance(items, list):
            continue
        char = by_name.get(name)
        if not char:
            continue
        inv = list(char.get("inventory", []))
        inv.extend(str(i) for i in items)
        update_character(char["id"], {"inventory": inv})


async def apply_tags(
    campaign: dict,
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> str:
    """解析 DM 回覆中的 [SYNC:...]/[XP:...]/[GIVE:...] 標籤並更新資料庫，同時從文字中移除標籤。

    傳回給玩家顯示的純敘事文字。
    """
    if not text:
        return text

    campaign_id = campaign["id"]
    matches = list(TAG_PATTERN.finditer(text))
    if not matches:
        return text

    for m in matches:
        kind = m.group(1)
        raw_json = m.group(2)
        try:
            payload = json.loads(raw_json)
        except Exception:
            continue

        if kind == "SYNC":
            await _apply_sync(campaign_id, payload)
        elif kind == "XP":
            await _apply_xp(campaign_id, payload, update, context)
        elif kind == "GIVE":
            await _apply_give(campaign_id, payload)

    # 將這些技術性標籤從玩家可見文字中移除
    clean = TAG_PATTERN.sub("", text).strip()
    return clean

