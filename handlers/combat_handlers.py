from __future__ import annotations
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from db import campaigns, events as events_db
from db.characters import get_characters, get_character_by_user, add_xp
from db import combat as combat_db
from combat import mechanics, initiative, grid
from dm.deepseek_client import chat
from dm import context_builder, tag_processor
import config


# ━━ helpers ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _get_items(combat_id: str) -> list[dict]:
    try:
        return combat_db.get_items(combat_id)
    except Exception:
        return []


async def _reply_long_markdown(update: Update, text: str) -> None:
    """Send long DM combat messages in chunks of <=4096 chars."""
    if not text:
        return
    # 預留少少 buffer，避免撞到 Telegram 4096 字限制
    max_len = 3500
    for i in range(0, len(text), max_len):
        chunk = text[i : i + max_len]
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except BadRequest:
            await update.message.reply_text(chunk)


async def _award_xp_and_levelup(
    update: Update,
    context,
    campaign: dict,
    combat: dict,
    monsters_defeated: list[dict],
) -> None:
    """Award XP to all surviving players; trigger level-up UI if any level up."""
    from handlers.character import handle_levelup

    if not monsters_defeated:
        return

    # Sum XP from all defeated monsters
    total_xp = 0
    for m in monsters_defeated:
        monster_key = m["name"].rstrip("0123456789").lower()
        # XP 同怪物 scaling 無關，用 base stats 就得
        stats = mechanics.get_monster_stats(monster_key)
        if stats:
            total_xp += mechanics.xp_for_cr(stats.get("cr", 0))

    if total_xp <= 0:
        return

    chars = get_characters(campaign["id"])
    if not chars:
        return

    # 目前戰鬥生命值只存在 combat_entities，角色表未同步 HP，
    # 所以同一場戰鬥內一律平均分 XP。
    share = total_xp // len(chars)

    xp_lines = [f"⭐ **戰鬥勝利！獲得 {total_xp} XP（每人 {share} XP）**\n"]

    # 一次過處理 XP 增加同升級，避免重覆寫入
    for char in chars:
        before_xp = char.get("xp", 0)
        before_level = char.get("level", 1)

        updated_char, leveled_up = add_xp(char["id"], share)

        xp_lines.append(
            f"  {char['name']}：{before_xp} → {updated_char['xp']} XP"
        )
        if leveled_up or updated_char["level"] > before_level:
            xp_lines.append(f"  🎉 {char['name']} 升到 {updated_char['level']} 級！")
            # 立刻觸發升級流程，直接用最新角色資料
            await handle_levelup(update, context, updated_char)

    await update.message.reply_text("\n".join(xp_lines), parse_mode="Markdown")


# ━━ /startcombat ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def cmd_startcombat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Begin a combat encounter. Usage: /startcombat [monster_key] [count]"""
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。")
        return
    if combat_db.get_active_combat(campaign["id"]):
        await update.message.reply_text("已有進行中的戰鬥！輸入 /combatgrid 查看。")
        return

    args = context.args or []
    monster_key = args[0].lower() if args else "goblin"
    count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 2
    # 難度跟隨戰役最高等角色
    chars = get_characters(campaign["id"])
    highest_level = max((c.get("level", 1) for c in chars), default=1)
    monster_stats = mechanics.get_monster_stats(monster_key, target_level=highest_level)
    if not monster_stats:
        await update.message.reply_text(
            f"未知怪物：`{monster_key}`\n可用怪物：{', '.join(mechanics.MONSTER_STATS.keys())}",
            parse_mode="Markdown",
        )
        return

    if not chars:
        await update.message.reply_text("沒有角色！請先建立角色。")
        return

    combat = combat_db.create_combat_session(campaign["id"])
    combatants_for_init = []

    player_emojis = config.PLAYER_EMOJIS[:]
    for i, char in enumerate(chars):
        emoji = char.get("emoji", player_emojis[i % len(player_emojis)])
        combat_db.add_entity(
            combat["id"], "player", char["name"],
            x=2, y=i + 1,
            hp=char["hp"], max_hp=char["max_hp"],
            ac=char["armor_class"],
            user_id=str(char["user_id"]),
            username=char.get("username", ""),
            char_id=char["id"],
            emoji=emoji,
        )
        combatants_for_init.append({
            "id": char["id"], "name": char["name"],
            "dex": char["stats"].get("dex", 10), "type": "player",
        })

    for i in range(count):
        name = f"{monster_stats.get('name_zh') or monster_stats.get('name', monster_key)}{i+1}"
        # 優先用怪物 stat block 上的 emoji，否則退回 config.MONSTER_EMOJIS
        emoji = monster_stats.get(
            "emoji",
            config.MONSTER_EMOJIS.get(monster_key, config.MONSTER_EMOJIS["default"]),
        )
        combat_db.add_entity(
            combat["id"], "monster", name,
            x=8, y=i + 1,
            hp=monster_stats["hp"], max_hp=monster_stats["hp"],
            ac=monster_stats["ac"],
            emoji=emoji,
        )
        combatants_for_init.append({
            "id": name, "name": name,
            "dex": monster_stats.get("dex", 10), "type": "monster",
        })

    init_order = initiative.roll_initiative(combatants_for_init)
    combat_db.set_initiative_order(combat["id"], init_order)
    # 戰鬥正式開始
    combat_db.update_combat(combat["id"], {"status": "active"})

    player_names = ", ".join(c["name"] for c in chars)
    monster_name = monster_stats["name"]
    prompt = f"""你是一位經驗豐富的D&D地下城主。
戰鬥即將開始：{player_names} 遭遇了 {count} 隻 {monster_name}。

請用生動的語言描述這場遭遇戰的開場（100字以內），包括：
1. 環境描述
2. 怪物的出現方式
3. 緊張的氛圍營造

用繁體中文，第二人稱「你們」來稱呼玩家。"""

    try:
        narration = await chat(
            [{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=256,
        )
        narration = await tag_processor.apply_tags(campaign, narration, update, context)
    except Exception:
        narration = f"你們遭遇了 {count} 隻 {monster_name}！"

    events_db.log_event(
        campaign["id"], "combat_start",
        f"戰鬥開始：{player_names} vs {count}x {monster_name}",
        {"combat_id": combat["id"], "narration": narration}
    )

    init_str = "\n".join(
        f"{i+1}. {c['name']} (先攻: {c['initiative']})"
        for i, c in enumerate(init_order)
    )

    await _reply_long_markdown(
        update,
        f"⚔️ **戰鬥開始！**\n\n{narration}\n\n"
        f"**先攻順序：**\n{init_str}\n\n"
        f"輸入 /combatgrid 查看戰場，/action 來行動。",
    )


# ━━ /combatgrid ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def cmd_combatgrid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。")
        return

    combat = combat_db.get_active_combat(campaign["id"])
    if not combat:
        await update.message.reply_text("目前沒有進行中的戰鬥。")
        return

    entities = combat_db.get_entities(combat["id"])
    init_order = combat.get("initiative_order") or []
    current_turn = combat.get("current_turn", 0)
    round_num = combat.get("round_num", 1)

    # 取得目前輪到邊個供顯示用
    current_name = ""
    if init_order and 0 <= current_turn < len(init_order):
        current_name = init_order[current_turn]["name"]

    # 取出場上物品，並用 render_combat_status 一次過畫出 emoji 戰場 + HP/物品
    items_list = _get_items(combat["id"])
    panel = grid.render_combat_status(
        entities,
        round_num=round_num,
        current_name=current_name or "？",
        items=items_list,
    )

    # 先攻順序（攻擊次序）列表
    init_lines = []
    for i, c in enumerate(init_order):
        marker = "👉 " if i == current_turn else "   "
        entity = next((e for e in entities if e["name"] == c["name"]), None)
        emoji = entity.get("emoji", "") if entity else ""
        init_val = c.get("initiative", "?")
        init_lines.append(f"{marker}{i+1}. {emoji} {c['name']}（先攻 {init_val}）")
    init_block = "\n".join(init_lines) if init_lines else "（未有先攻資料）"

    text = (
        "⚔️ **戰鬥狀態**\n\n"
        f"{panel}\n\n"
        f"**先攻順序：**\n{init_block}"
    )
    await _reply_long_markdown(update, text)


# ━━ /action ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def cmd_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Player declares their action. Usage: /action <description>"""
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。")
        return

    combat = combat_db.get_active_combat(campaign["id"])
    if not combat:
        await update.message.reply_text("目前沒有進行中的戰鬥。使用 /startcombat 開始戰鬥。")
        return

    user_id = update.effective_user.id
    char = get_character_by_user(campaign["id"], user_id)
    if not char:
        await update.message.reply_text("你沒有角色。")
        return

    entities = combat_db.get_entities(combat["id"])
    player_entity = next((e for e in entities if e.get("char_id") == char["id"]), None)
    if not player_entity:
        await update.message.reply_text("你的角色不在戰鬥中。")
        return

    init_order = combat["initiative_order"] or []
    current_turn = combat.get("current_turn", 0)
    if current_turn >= len(init_order):
        await update.message.reply_text("戰鬥已結束或先攻順序有誤。")
        return

    current_combatant = init_order[current_turn]
    if current_combatant["name"] != player_entity["name"]:
        await update.message.reply_text(
            f"現在是 **{current_combatant['name']}** 的回合，請等待你的回合。",
            parse_mode="Markdown",
        )
        return

    if not context.args:
        await update.message.reply_text(
            "請描述你的行動，例如：\n"
            "/action 攻擊哥布林1\n"
            "/action 移動到(5,3)\n"
            "/action 施放魔法飛彈"
        )
        return

    action_text = " ".join(context.args)

    ctx = (
        context_builder.build_combat_context(campaign["id"])
        + f"\n\n【玩家行動】{char['name']}：{action_text}\n"
    )

    try:
        # 戰鬥回應：附帶 system prompt，唔好講「戰鬥狀態」四字
        dm_response = await chat(
            [
                {"role": "system", "content": context_builder.build_system_prompt()},
                {"role": "user", "content": ctx},
            ],
            temperature=0.9,
            max_tokens=768,
        )
        dm_response = await tag_processor.apply_tags(campaign, dm_response, update, context)
    except Exception as e:
        await update.message.reply_text(f"DM AI 發生錯誤：{str(e)}")
        return

    events_db.log_event(
        campaign["id"], "combat_action",
        f"{char['name']}: {action_text}",
        {
            "combat_id": combat["id"],
            "character_id": char["id"],
            "action": action_text,
            "dm_response": dm_response,
        }
    )

    # Advance turn
    next_turn = (current_turn + 1) % len(init_order)
    cur_round = combat.get("round_num", 1)
    new_round = cur_round + (1 if next_turn == 0 else 0)
    combat_db.update_combat(combat["id"], {
        "current_turn": next_turn,
        "round_num": new_round,
    })

    # Re-fetch entities to get updated HP after DM resolves action
    entities = combat_db.get_entities(combat["id"])

    # Check if all monsters are dead
    monsters = [e for e in entities if e.get("entity_type") == "monster"]
    dead_monsters = [m for m in monsters if m["hp"] <= 0]
    all_monsters_dead = len(monsters) > 0 and all(m["hp"] <= 0 for m in monsters)

    next_combatant = init_order[next_turn]
    next_entity = next((e for e in entities if e["name"] == next_combatant["name"]), None)
    turn_indicator = f"\n\n👉 **下一位：{next_combatant['name']}**"
    if next_entity and next_entity.get("entity_type") == "player":
        turn_indicator += " - 輸入 /action 來行動"

    await _reply_long_markdown(
        update,
        f"🎲 **{char['name']}** {action_text}\n\n"
        f"{dm_response}"
        f"{turn_indicator}",
    )

    # Award XP if all monsters dead
    if all_monsters_dead:
        await _end_combat_with_victory(update, context, campaign, combat, dead_monsters)
        return

    # If next turn is a monster, auto-resolve
    if next_entity and next_entity.get("entity_type") == "monster" and next_entity["hp"] > 0:
        await _resolve_monster_turn(update, context, campaign, combat, next_entity, entities)


async def _end_combat_with_victory(
    update: Update,
    context,
    campaign: dict,
    combat: dict,
    defeated_monsters: list[dict],
) -> None:
    """End combat, award XP, trigger level-up if needed."""
    combat_db.end_combat(combat["id"])
    events_db.log_event(campaign["id"], "combat_end", "戰鬥勝利", {"combat_id": combat["id"]})

    await update.message.reply_text(
        "⚔️ **所有敵人已被消滅！戰鬥勝利！**",
        parse_mode="Markdown",
    )

    await _award_xp_and_levelup(update, context, campaign, combat, defeated_monsters)


async def _resolve_monster_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    campaign: dict,
    combat: dict,
    monster: dict,
    entities: list[dict],
) -> None:
    """AI-controlled monster takes its turn."""
    players = [e for e in entities if e.get("entity_type") == "player" and e["hp"] > 0]
    if not players:
        await update.message.reply_text("所有玩家角色都倒下了！戰鬥結束。")
        combat_db.end_combat(combat["id"])
        return

    # 難度跟隨戰役最高等角色
    chars = get_characters(campaign["id"])
    highest_level = max((c.get("level", 1) for c in chars), default=1)
    monster_stats = mechanics.get_monster_stats(
        monster["name"].rstrip("0123456789").lower(),
        target_level=highest_level,
    )
    if not monster_stats:
        await update.message.reply_text(f"{monster['name']} 的回合被跳過（未知怪物數據）。")
        return

    prompt = f"""你是D&D地下城主，控制怪物 {monster['name']} 行動。

當前戰況：
- {monster['name']}: HP {monster['hp']}/{monster['max_hp']}, 位置({monster['x']},{monster['y']})
- 玩家角色：{', '.join(f"{p['name']} HP{p['hp']}/{p['max_hp']} @({p['x']},{p['y']})" for p in players)}

怪物數據：{monster_stats}

請決定 {monster['name']} 的行動並用生動的語言描述（50字以內）。"""

    try:
        # 怪物行動敘事：保持少量隨機，但以穩定為主
        action = await chat(
            [{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=256,
        )
        action = await tag_processor.apply_tags(campaign, action, update, context)
    except Exception:
        action = f"{monster['name']} 警戒地移動。"

    events_db.log_event(
        campaign["id"], "combat_action",
        f"{monster['name']}: {action}",
        {"combat_id": combat["id"], "monster_action": action}
    )

    init_order = combat["initiative_order"] or []
    current_turn = combat.get("current_turn", 0)
    next_turn = (current_turn + 1) % len(init_order)
    cur_round = combat.get("round_num", 1)
    new_round = cur_round + (1 if next_turn == 0 else 0)
    combat_db.update_combat(combat["id"], {
        "current_turn": next_turn,
        "round_num": new_round,
    })

    next_combatant = init_order[next_turn]
    turn_indicator = f"\n\n👉 **下一位：{next_combatant['name']}**"

    await _reply_long_markdown(
        update,
        f"👹 **{monster['name']}**\n{action}"
        f"{turn_indicator}",
    )


# ━━ /endcombat ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def cmd_endcombat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。")
        return

    combat = combat_db.get_active_combat(campaign["id"])
    if not combat:
        await update.message.reply_text("目前沒有進行中的戰鬥。")
        return

    entities = combat_db.get_entities(combat["id"])
    dead_monsters = [e for e in entities if e.get("entity_type") == "monster" and e["hp"] <= 0]

    combat_db.end_combat(combat["id"])
    events_db.log_event(campaign["id"], "combat_end", "戰鬥結束", {"combat_id": combat["id"]})
    await update.message.reply_text("⚔️ 戰鬥已結束。")

    if dead_monsters:
        await _award_xp_and_levelup(update, context, campaign, combat, dead_monsters)


# ━━ /move ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def cmd_move(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Move your character. Usage: /move <x> <y>"""
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。")
        return

    combat = combat_db.get_active_combat(campaign["id"])
    if not combat:
        await update.message.reply_text("目前沒有進行中的戰鬥。")
        return

    user_id = update.effective_user.id
    char = get_character_by_user(campaign["id"], user_id)
    if not char:
        await update.message.reply_text("你沒有角色。")
        return

    entities = combat_db.get_entities(combat["id"])
    player_entity = next((e for e in entities if e.get("char_id") == char["id"]), None)
    if not player_entity:
        await update.message.reply_text("你的角色不在戰鬥中。")
        return

    if len(context.args) < 2:
        await update.message.reply_text("用法：/move <x> <y>")
        return

    try:
        new_x = int(context.args[0])
        new_y = int(context.args[1])
    except ValueError:
        await update.message.reply_text("座標必須是數字。")
        return

    if not (0 <= new_x < config.GRID_WIDTH and 0 <= new_y < config.GRID_HEIGHT):
        await update.message.reply_text(
            f"座標超出範圍（0-{config.GRID_WIDTH-1}, 0-{config.GRID_HEIGHT-1}）。"
        )
        return

    old_x, old_y = player_entity["x"], player_entity["y"]
    distance = grid.calculate_distance(old_x, old_y, new_x, new_y)
    speed_feet = char.get("speed", 30)
    max_squares = speed_feet // 5

    if distance > max_squares:
        await update.message.reply_text(
            f"移動距離 {distance} 格超過你的速度上限 {max_squares} 格（{speed_feet}呎）。"
        )
        return

    if any(e["x"] == new_x and e["y"] == new_y and e["id"] != player_entity["id"] for e in entities):
        await update.message.reply_text("該位置已被佔據。")
        return

    combat_db.update_entity(player_entity["id"], {"x": new_x, "y": new_y})
    await update.message.reply_text(
        f"✅ {char['name']} 移動到 ({new_x}, {new_y})\n"
        f"輸入 /combatgrid 查看戰場。"
    )