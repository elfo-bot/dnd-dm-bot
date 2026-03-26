from __future__ import annotations
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from db import campaigns, events as events_db
from db.characters import get_character_by_user, update_character, add_xp
from db import npcs as npc_db
from db import episodes as episodes_db
from db import combat as combat_db
from dm import context_builder, memory_manager
from dm.deepseek_client import chat
from dm import tag_processor
import config
import html
import json
import re

# 玩家捷徑：bot 只展開成下面文字當「玩家訊息」，走一般 build_context + chat，唔另開獨立 AI 流程
GRID_SHORTCUT_EXPANDED = """根據戰況列出符號對照同 grid，所有人物和物件以 emoji 顯示。
輸出：10 行、每行剛好 10 個字元；角色、敵人、場上重要物件用合適 emoji 佔一格；空位用 🟫。
唔好顯示座標軸、唔好重複之前敘事全文、唔好問下一步。

輸出必須分兩部分，**順序固定：先 LEGEND 後 GRID**，用下面標記逐字包住（唔好省略標記）：

===COORDINATES===
(隱藏思考：請先喺度列出每個角色/物件喺 10x10 grid 入面嘅 (x,y) 座標，x由左至右1-10，y由上至下1-10)

===LEGEND===
（純文字：逐行列出格線入面每個用到嘅 emoji 代表咩、HP（如有）、特殊狀態（有先有））

===GRID===
（只放 10 行格線，每行 10 個字元）

除咗 ===LEGEND=== 同 ===GRID=== 兩段內容外唔好加其他說明。"""

CONTINUE_SHORTCUT_EXPANDED = (
    "玩家輸入「繼續」。請只輸出你上一段作為 DM 嘅敘事嘅直接接續："
    "唔好重複上文、唔好前言後語、時間不要流逝、唔好用 JSON code block、唔好提「戰鬥狀態」。"
)

UPDATE_SHORTCUT_TEMPLATE = (
    "玩家輸入「更新：…」（系統已展開）。"
    "你只可以輸出一個 ```json``` code block，內容係要同步到資料庫嘅欄位；"
    "唔可以輸出任何解釋、故事、格線或其他文字。\n"
    "可用欄位：hp, max_hp, inventory, xp, conditions, npcs, episode, episodes, world_state。\n\n"
    "更新要求：\n{payload}"
)


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if "```" in t:
        t = re.sub(r"```[\w]*\s*", "", t)
        t = re.sub(r"```", "", t).strip()
    return t


def _split_grid_legend_response(text: str) -> tuple[str, str]:
    """從 DM 回覆拆出格線與符號對照。回傳 (grid_part, legend_part)。

    新順序：===LEGEND=== 先、===GRID=== 後（li < gi）。
    仍相容舊順序：===GRID=== 先、===LEGEND=== 後（gi < li）。
    """
    t = text.strip()
    g_tag, l_tag = "===GRID===", "===LEGEND==="
    gi, li = t.find(g_tag), t.find(l_tag)
    if gi != -1 and li != -1:
        if li < gi:
            # LEGEND 先，GRID 後
            legend_part = t[li + len(l_tag) : gi].strip()
            grid_part = t[gi + len(g_tag) :].strip()
            return grid_part, legend_part
        # GRID 先，LEGEND 後（舊版）
        grid_part = t[gi + len(g_tag) : li].strip()
        legend_part = t[li + len(l_tag) :].strip()
        return grid_part, legend_part
    return _strip_code_fences(t), ""


async def _reply_long_html_pre(update: Update, text: str) -> None:
    """Telegram HTML：等寬 <pre> 顯示格線。"""
    if not text:
        return
    body = _strip_code_fences(text).strip()
    if not body:
        return
    max_len = 3500
    for i in range(0, len(body), max_len):
        chunk = body[i : i + max_len]
        safe = html.escape(chunk)
        try:
            await update.message.reply_text(f"<pre>{safe}</pre>", parse_mode="HTML")
        except BadRequest:
            await update.message.reply_text(chunk)


async def _reply_long_plain(update: Update, text: str) -> None:
    """純文字分段（Telegram 4096 上限）。"""
    if not text:
        return
    max_len = 4096
    for i in range(0, len(text), max_len):
        await update.message.reply_text(text[i : i + max_len])


async def _reply_long_markdown(update: Update, text: str) -> None:
    """Send long DM messages in chunks of <=4096 chars."""
    if not text:
        return
    # 預留少少 buffer，避免撞到 Telegram 4096 字限制
    max_len = 3500
    for i in range(0, len(text), max_len):
        chunk = text[i : i + max_len]
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except BadRequest:
            # 無論咩 Markdown 解析錯誤，一律 fallback 做純文字，
            # 確保唔會因為格式問題令整個回覆失敗。
            await update.message.reply_text(chunk)


JSON_BLOCK_RE = re.compile(r"```json\s*([\s\S]*?)```", re.IGNORECASE)


def _apply_json_delta(
    campaign_id: str | None,
    char: dict | None,
    text: str,
) -> tuple[str, dict | None]:
    """從 DM 回覆中抽出首個 ```json``` 區塊，套用到玩家角色卡與 NPC 清單，並回傳純敘事文字。

    - 如果冇角色（例如未建立角色），只會處理 NPC 更新。
    - 玩家角色部分：只處理 hp、max_hp、inventory、conditions、xp。
    - NPC 部分：處理 delta["npcs"] 陣列中每個 NPC 的 name/status/location/role。
    - Episode 部分：處理 delta["episode"] 或 delta["episodes"]，達成時自動推進下一集／結束戰役。
    - world_state 部分：處理 delta["world_state"]，將資訊寫入 campaigns.world_state 表。
    """
    if not text:
        return text, None

    m = JSON_BLOCK_RE.search(text)
    if not m:
        return text, None

    raw_json = m.group(1).strip()
    try:
        delta = json.loads(raw_json)
    except Exception:
        # JSON 壞咗就當作普通文字處理
        return text, None

    # ── 玩家角色更新（如有） ─────────────────────────────────────────────
    if char is not None:
        char_id = char["id"]
        current_char = dict(char)

        # 先處理 XP：用差額方式，保持等級＆熟練加值邏輯
        if isinstance(delta.get("xp"), int) and isinstance(current_char.get("xp"), int):
            new_xp = delta["xp"]
            cur_xp = current_char.get("xp", 0)
            diff = new_xp - cur_xp
            if diff != 0:
                updated_char, _ = add_xp(char_id, diff)
                current_char = updated_char

        # 其他簡單欄位：直接覆寫
        simple_fields = ["hp", "max_hp", "inventory", "conditions"]
        updates: dict = {}
        for key in simple_fields:
            if key in delta and delta[key] != current_char.get(key):
                updates[key] = delta[key]

        if updates:
            current_char = update_character(char_id, updates)

    # ── NPC 更新 ───────────────────────────────────────────────────────
    npcs_delta = delta.get("npcs") or delta.get("npc")
    if campaign_id and npcs_delta:
        if isinstance(npcs_delta, dict):
            npcs_list = [npcs_delta]
        else:
            npcs_list = list(npcs_delta)

        for n in npcs_list:
            if not isinstance(n, dict):
                continue
            name = (n.get("name") or "").strip()
            if not name:
                continue
            # 忽略「XX守衛」、「地精A」之類嘅雜兵
            if "XX" in name:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]+[A-Z]", name):
                continue

            status = n.get("status")
            location = n.get("location")
            role = n.get("role")
            notes = n.get("notes")

            npc_db.upsert_npc(
                campaign_id,
                name=name,
                status=status,
                location=location,
                role=role,
                notes=notes,
            )

    # ── Episode 更新／推進 ────────────────────────────────────────────
    if campaign_id:
        ep_payload = delta.get("episode") or delta.get("episodes")
        ep_list = []
        if isinstance(ep_payload, dict):
            ep_list = [ep_payload]
        elif isinstance(ep_payload, list):
            ep_list = ep_payload

        completed_numbers: list[int] = []
        for ep in ep_list:
            if not isinstance(ep, dict):
                continue
            num = ep.get("episode_number")
            if not isinstance(num, int):
                continue
            status = ep.get("status")
            initiating_event = ep.get("initiating_event")
            goal = ep.get("goal")
            ending = ep.get("ending")
            notes = ep.get("notes")

            episodes_db.upsert_episode(
                campaign_id,
                episode_number=num,
                initiating_event=initiating_event,
                goal=goal,
                ending=ending,
                status=status,
                notes=notes,
            )
            if status == "completed":
                completed_numbers.append(num)

        # 自動推進：完成某集 → 下一集 active；若無下一集 → 結束戰役
        if completed_numbers:
            # 以最大完成集數推進（避免一次完成多集時亂跳）
            last_done = max(completed_numbers)
            next_ep = episodes_db.activate_next_episode(campaign_id, last_done)
            if not next_ep:
                # 無下一集：結束戰役
                campaigns.end_campaign(campaign_id)

    # ── world_state 寫入 ─────────────────────────────────────────────
    if campaign_id:
        ws_payload = delta.get("world_state")
        ws_list = []
        if isinstance(ws_payload, dict):
            ws_list = [ws_payload]
        elif isinstance(ws_payload, list):
            ws_list = ws_payload
        for item in ws_list:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            value = item.get("value")
            if not isinstance(key, str) or not key.strip():
                continue
            if value is None:
                continue
            campaigns.set_world_state(campaign_id, key.strip(), str(value))

    # 移除 JSON code block，只保留畀玩家睇嘅故事文字
    story = (text[: m.start()] + text[m.end() :]).strip()
    return story, delta


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚔️ **失落的芬德爾礦坑 DM Bot** 🐉\n\n"
        "歡迎嚟到龍與地下城！我係你嘅AI地下城主。\n\n"
        "**開始遊戲**\n"
        "• `/newgame` — 開始新戰役\n"
        "• `/newchar` — 建立新角色\n"
        "• `/usechar <角色ID>` — 匯入你之前建立嘅角色\n"
        "  （角色ID 會喺建立角色後顯示，可以跨戰役重用）\n"
        "• `/startadventure` — 所有人準備好後開始冒險\n\n"
        "**遊戲中**\n"
        "• 直接輸入行動描述（例如：「我要偵察前方」）\n"
        "• 輸入 `grid` — DM 以 HTML 格線顯示戰場，跟住一段文字說明符號／HP／狀態\n"
        "• 輸入 `繼續` — 接續上一段 DM 敘事（唔加時間流逝）\n"
        "• 輸入 `更新：…` — 只更新資料庫欄位，回覆「成功更新」\n"
        "• `/status` — 查看戰役狀態\n"
        "• `/mychar` — 查看我的角色\n"
        "• `/recap` — 回顧故事\n\n"
        "**戰鬥**\n"
        "• `/startcombat [怪物] [數量]` — 開始戰鬥\n"
        "• `/attack <目標> <d20> [傷害]` — 攻擊\n"
        "• `/move <x> <y>` — 移動\n"
        "• `/nextturn` — 下一輪\n"
        "• `/combatgrid` — 查看戰鬥地圖\n\n"
        "**DM工具**\n"
        "• `/setlocation <地點>` — 更改地點\n"
        "• `/setworld <鍵> <值>` — 設定世界狀態\n"
        "• `/roll <骰子>` — 擲骰（例如 `/roll 2d6`）\n",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main message handler — 即時回覆玩家行動；不再等所有人一齊輸入。"""
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign or campaign["status"] == "character_creation":
        return  # Not in active adventure

    user = update.effective_user
    user_message = update.message.text.strip()
    if not user_message:
        return

    # In group chats, only respond when the bot is @mentioned
    if update.effective_chat.type in ("group", "supergroup"):
        bot_username = context.bot.username
        if f"@{bot_username}" not in user_message:
            return
        # Strip the @mention from the message before passing to DM
        user_message = user_message.replace(f"@{bot_username}", "").strip()
        if not user_message:
            return

    # 捷徑：只把玩家輸入展開成完整 prompt，其餘同一般訊息（build_context + chat）
    raw_player_text = user_message.strip()
    shortcut_kind: str | None = None
    if raw_player_text.lower() == "grid":
        user_message = GRID_SHORTCUT_EXPANDED
        shortcut_kind = "grid"
    elif raw_player_text == "繼續":
        user_message = CONTINUE_SHORTCUT_EXPANDED
        shortcut_kind = "continue"
    elif raw_player_text.startswith("更新："):
        upd_payload = raw_player_text[len("更新：") :].strip()
        if not upd_payload:
            await update.message.reply_text("請寫明要更新嘅內容。")
            return
        user_message = UPDATE_SHORTCUT_TEMPLATE.format(payload=upd_payload)
        shortcut_kind = "update"

    combat = combat_db.get_active_combat(campaign["id"])

    # 先攞到玩家喺呢個戰役底下嘅角色（如有）— 特別指令都要用到
    user_char = get_character_by_user(campaign["id"], user.id)

    # ── In combat: keep existing per-message behavior (no auto grid) ─────────
    # Don't process if in active combat and it's currently monster turn
    if combat and combat.get("status") == "active":
        order = combat.get("initiative_order") or []
        current_turn = combat.get("current_turn", 0)
        if order and 0 <= current_turn < len(order):
            current = order[current_turn]
            if current.get("entity_type") == "monster":
                await update.message.reply_text(
                    "⏳ 等等！現在係怪物的回合，請等待輪到你。",
                )
                return

    # Log player action（捷徑仍記錄玩家實際輸入）
    events_db.log_event(
        campaign["id"], user.first_name, raw_player_text, event_type="player_action"
    )

    # Build context and call DM（已展開嘅 user_message）
    await update.message.chat.send_action("typing")
    messages = await context_builder.build_context(campaign, user_message, user.first_name)
    # 按照新規則，喺 prompt 入面額外提供當前玩家角色嘅 JSON 角色卡
    if user_char:
        try:
            char_json = json.dumps(user_char, ensure_ascii=False)
            # 插入喺最後一個「玩家動作」訊息之前
            if messages and messages[-1].get("role") == "user":
                messages.insert(
                    -1,
                    {
                        "role": "user",
                        "content": f"Current character sheet (JSON): {char_json}",
                    },
                )
        except Exception:
            # JSON 序列化出錯就略過，唔影響主流程
            pass
    # 一般敘事 DM 回應：提高少少創意（更新指令用較低溫度較易只出 JSON）
    temp = 0.25 if shortcut_kind == "update" else 0.8
    max_tok = 900 if shortcut_kind == "update" else (1200 if shortcut_kind == "grid" else 1024)
    raw_response = await chat(messages, temperature=temp, max_tokens=max_tok)

    # 「更新：」捷徑：只寫入資料庫，回覆「成功更新」，唔走敘事／戰鬥標籤流程
    if shortcut_kind == "update":
        _apply_json_delta(campaign["id"], user_char, raw_response)
        await update.message.reply_text("成功更新")
        return

    # 先處理 JSON 區塊 → 直接更新 Supabase 角色／NPC 資料
    response, _ = _apply_json_delta(campaign["id"], user_char, raw_response)

    # 再處理可能存在的舊式自動化標籤（SYNC / XP / GIVE），並從訊息中移除
    response = await tag_processor.apply_tags(campaign, response, update, context)

    # Log DM response（grid 會記錄含標記嘅全文，方便回顧）
    events_db.log_event(campaign["id"], "DM", response, event_type="narrative")

    # Compress memory if needed
    await memory_manager.maybe_compress_memory(campaign["id"])

    # Auto-start combat if DM included a [COMBAT:monster:count] tag
    import re
    # 支援更靈活的怪物 key，並避免在訊息中顯示 [COMBAT:...] 標籤
    combat_tag = re.search(r'\[COMBAT:([^:\]]+):(\d+)\]', response)
    if combat_tag and not (combat and combat["status"] == "active"):
        monster_key = combat_tag.group(1)
        count = int(combat_tag.group(2))
        # Strip the tag from the displayed response
        response = re.sub(r'\n?\[COMBAT:[^:\]]+:\d+\]', '', response).strip()
        # Auto-initialize combat
        from combat import mechanics as _mech, initiative as _init
        from db.characters import get_characters as _get_chars
        chars = _get_chars(campaign["id"])
        highest_level = max((c.get("level", 1) for c in chars), default=1)
        monster_stats = _mech.get_monster_stats(monster_key, target_level=highest_level)
        if monster_stats and chars:
            new_combat = combat_db.create_combat_session(campaign["id"])
            combatants_for_init = []
            player_emojis = config.PLAYER_EMOJIS[:]
            for i, char in enumerate(chars):
                emoji = char.get("emoji", player_emojis[i % len(player_emojis)])
                combat_db.add_entity(
                    new_combat["id"], "player", char["name"],
                    x=2, y=i + 1,
                    hp=char["hp"], max_hp=char["max_hp"],
                    ac=char["armor_class"],
                    user_id=str(char["user_id"]),
                    char_id=char["id"],
                    emoji=emoji,
                )
                combatants_for_init.append({
                    "id": char["id"], "name": char["name"],
                    "dex": char["stats"].get("dex", 10),
                    "entity_type": "player", "emoji": emoji,
                })
            monster_emoji = config.MONSTER_EMOJIS.get(monster_key, config.MONSTER_EMOJIS["default"])
            for i in range(count):
                m_name = f"{monster_stats['name_zh']}{i+1}"
                combat_db.add_entity(
                    new_combat["id"], "monster", m_name,
                    x=7, y=i + 2,
                    hp=monster_stats["hp"], max_hp=monster_stats["max_hp"],
                    ac=monster_stats["ac"],
                    emoji=monster_emoji,
                )
                combatants_for_init.append({
                    "id": f"monster_{i}", "name": m_name,
                    "dex": monster_stats.get("dex", 10),
                    "entity_type": "monster", "emoji": monster_emoji,
                })
            order = _init.build_initiative_order(combatants_for_init)
            order_for_db = [
                {"name": c["name"], "entity_type": c["entity_type"],
                 "initiative": c["initiative_total"], "emoji": c["emoji"]}
                for c in order
            ]
            combat_db.update_combat(new_combat["id"], {
                "initiative_order": order_for_db,
                "current_turn": 0,
                "status": "active",
            })
            events_db.log_event(
                campaign["id"], "系統",
                f"自動戰鬥開始：{count}隻{monster_stats['name_zh']}",
                event_type="combat",
            )
            # Combat is created; further messages can use /combatgrid to view it.

    # 最後再保險一次，把任何殘留的 [COMBAT:...] 標籤從訊息中移除
    response = re.sub(r'\n?\[COMBAT:[^:\]]+:\d+\]', '', response).strip()

    # grid：先純文字符號對照，再 HTML 格線（順序同 prompt：LEGEND → GRID）
    if shortcut_kind == "grid":
        grid_body, legend = _split_grid_legend_response(response)
        if legend.strip():
            await _reply_long_plain(update, legend.strip())
        await _reply_long_html_pre(update, grid_body)
    else:
        await _reply_long_markdown(update, response)


async def cmd_roll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Roll dice. Usage: /roll 2d6, /roll d20, /roll 4d6"""
    from combat.mechanics import roll as dice_roll
    args = context.args or []
    expr = args[0].lower() if args else "d20"
    try:
        total, rolls = dice_roll(expr)
        rolls_str = " + ".join(str(r) for r in rolls)
        await update.message.reply_text(
            f"🎲 **{expr}** → [{rolls_str}] = **{total}**",
            parse_mode="Markdown",
        )
        # 不再自動將骰子結果送去 DM；由玩家自行報告和敘述。
    except Exception:
        await update.message.reply_text(
            f"無效的骰子格式：`{expr}`\n例如：`2d6`、`d20`、`4d6`",
            parse_mode="Markdown",
        )


async def cmd_setworld(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a world state key-value. Usage: /setworld <key> <value>"""
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。")
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("用法：`/setworld <鍵> <值>`\n例：`/setworld 西爾達已獲救 是`", parse_mode="Markdown")
        return
    key = args[0]
    value = " ".join(args[1:])
    campaigns.set_world_state(campaign["id"], key, value)
    await update.message.reply_text(f"✅ 世界狀態已更新：**{key}** = {value}", parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    import logging
    logger = logging.getLogger(__name__)
    logger.error("Exception while handling update:", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text(
            "⚠️ 發生錯誤，請稍後再試。如問題持續請聯絡DM。"
        )