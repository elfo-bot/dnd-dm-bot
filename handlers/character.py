from __future__ import annotations
import json
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters
)
from db import campaigns
from db.characters import (
    create_character,
    get_character_by_user,
    get_character_by_id,
    update_character,
    add_xp,
    link_character_to_campaign,
)
from dm.deepseek_client import chat, chat_json
import config

# ── Conversation states ────────────────────────────────────────────────────────
CHOOSING_NAME, CHOOSING_CLASS, CHOOSING_RACE, CHOOSING_BACKGROUND, CONFIRMING = range(5)
USECHAR_WAITING_ID = 10
LEVELUP_CHOOSING = 20

CLASSES = list(config.CLASS_HIT_DICE.keys())
RACES = ["人類", "精靈", "矮人", "半獸人", "半精人", "半身人", "侏儒", "提夫林", "龍裔", "斑貓人"]
BACKGROUNDS = ["民間英雄", "貴族", "犯罪者", "學者", "軍人", "奴兵", "水手", "遊俠"]

# XP thresholds per level (index = level)
XP_THRESHOLDS = [0, 0, 300, 900, 2700, 6500, 14000, 23000, 34000,
                 48000, 64000, 85000, 100000, 120000, 140000,
                 165000, 195000, 225000, 265000, 305000, 355000]


def _mod(score: int) -> str:
    m = (score - 10) // 2
    return f"+{m}" if m >= 0 else str(m)


def _stat_line(stats: dict) -> str:
    """e.g. 力8(-1) 敏17(+3) 體14(+2) 智12(+1) 感10(+0) 魅13(+1)"""
    # 兼容大寫/細寫 key（STR/str）
    keys = [("str", "力"), ("dex", "敏"), ("con", "體"), ("int", "智"), ("wis", "感"), ("cha", "魅")]
    parts = []
    for key, zh in keys:
        val = stats.get(key)
        if val is None:
            # 試下搵大寫版本（兼容舊資料）
            val = stats.get(key.upper(), 10)
        parts.append(f"{zh}{val}({_mod(val)})")
    return " ".join(parts)


def _format_sheet_preview(sheet: dict) -> str:
    """Rich compact character sheet for confirmation."""
    name = sheet["name"]
    race = sheet["race"]
    cls = sheet["class"]
    bg = sheet.get("background", "")
    hp = sheet.get("hp", sheet.get("max_hp", 10))
    ac = sheet.get("armor_class", 10)
    speed = sheet.get("speed", 30)
    stats = sheet.get("stats", {})
    inventory = ", ".join(sheet.get("inventory", []))
    personality = sheet.get("personality", "")

    lines = [
        f"🎲 **{name}**",
        f"{race} {cls} | 背景：{bg}",
        f"HP：{hp}  AC：{ac}  速度：{speed}呎",
        _stat_line(stats),
        f"裝備：{inventory}",
    ]

    spells = sheet.get("spells", {})
    if spells:
        spell_parts = []
        cantrips = spells.get("cantrips", [])
        if cantrips:
            spell_parts.append(f"戲法：{', '.join(cantrips)}")
        for lvl in ["1", "2", "3"]:
            sl = spells.get(lvl, [])
            if sl:
                spell_parts.append(f"{lvl}環：{', '.join(sl)}")
        slots = sheet.get("spell_slots", {})
        active_slots = ", ".join(f"{k}環×{v}" for k, v in slots.items() if v > 0)
        if spell_parts:
            lines.append("法術：" + " | ".join(spell_parts))
        if active_slots:
            lines.append(f"法術位：{active_slots}")

    if personality:
        lines.append(f"性格：{personality}")

    lines.append("")
    lines.append("確認建立此角色？輸入 **是** 確認，**否** 重新開始。")
    return "\n".join(lines)


def _format_mychar(char: dict) -> str:
    """Rich compact format for /mychar."""
    name = char["name"]
    race = char["race"]
    cls = char["class"]
    bg = char.get("background", "")
    level = char.get("level", 1)
    hp = char.get("hp", 0)
    max_hp = char.get("max_hp", 0)
    ac = char.get("armor_class", 10)
    speed = char.get("speed", 30)
    prof = char.get("proficiency_bonus", 2)
    xp = char.get("xp", 0)
    stats = char.get("stats", {})
    inventory = ", ".join(char.get("inventory", []))
    conditions = ", ".join(char.get("conditions", [])) or "無"
    personality = char.get("personality", "")
    char_id = char.get("id", "")

    next_xp = XP_THRESHOLDS[level + 1] if level < 20 else None
    xp_str = f"{xp}/{next_xp}" if next_xp else f"{xp} (滿級)"

    saves = char.get("saving_throws", {})
    save_map = {"STR": "力量", "DEX": "敏捷", "CON": "體質", "INT": "智力", "WIS": "感知", "CHA": "魅力"}
    prof_saves = ", ".join(save_map[k] for k, v in saves.items() if v > 0 and k in save_map) or "無"

    lines = [
        f"📜 **{name}**",
        f"{race} {cls} | 背景：{bg}",
        f"HP：{hp}/{max_hp}  AC：{ac}  速度：{speed}呎",
        f"等級 {level} | 熟練加值：+{prof} | XP：{xp_str}",
        "",
        _stat_line(stats),
        "",
        f"**豁免熟練：** {prof_saves}",
        f"**裝備：** {inventory}",
        f"**狀態：** {conditions}",
    ]

    spells = char.get("spells", {})
    if spells:
        spell_parts = []
        cantrips = spells.get("cantrips", [])
        if cantrips:
            spell_parts.append(f"戲法：{', '.join(cantrips)}")
        for lvl in ["1", "2", "3", "4", "5"]:
            sl = spells.get(lvl, [])
            if sl:
                spell_parts.append(f"{lvl}環：{', '.join(sl)}")
        if spell_parts:
            lines.append("**法術：** " + " | ".join(spell_parts))
        slots = char.get("spell_slots", {})
        active_slots = ", ".join(f"{k}環×{v}" for k, v in slots.items() if v > 0)
        if active_slots:
            lines.append(f"**法術位：** {active_slots}")

    if personality:
        lines.append(f"**性格：** {personality}")

    lines.append(f"\n🪪 ID: `{char_id}`")
    return "\n".join(lines)


# ── /newchar ───────────────────────────────────────────────────────────────────

async def cmd_newchar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("請先輸入 /newgame 開始戰役。")
        return ConversationHandler.END
    user_id = update.effective_user.id
    existing = get_character_by_user(campaign["id"], user_id)
    if existing:
        await update.message.reply_text(
            f"你已有角色：**{existing['name']}**（{existing['race']} {existing['class']}）\n"
            "輸入 /mychar 查看詳情。",
            parse_mode="Markdown",
        )
        return ConversationHandler.END
    context.user_data["campaign_id"] = campaign["id"]
    await update.message.reply_text("✨ **建立新角色**\n\n請輸入你的角色名稱：", parse_mode="Markdown")
    return CHOOSING_NAME


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if len(name) < 1 or len(name) > 30:
        await update.message.reply_text("名稱長度需在1-30字之間，請重新輸入：")
        return CHOOSING_NAME
    context.user_data["char_name"] = name
    class_list = "\n".join(f"{i+1}. {c}" for i, c in enumerate(CLASSES))
    await update.message.reply_text(
        f"好的，**{name}**！\n\n請選擇職業（輸入數字或職業名稱）：\n{class_list}",
        parse_mode="Markdown",
    )
    return CHOOSING_CLASS


async def receive_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    chosen = None
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(CLASSES):
            chosen = CLASSES[idx]
    else:
        for c in CLASSES:
            if text in c or c in text:
                chosen = c
                break
    if not chosen:
        await update.message.reply_text("請輸入有效的職業數字或名稱。")
        return CHOOSING_CLASS
    context.user_data["char_class"] = chosen
    race_list = "\n".join(f"{i+1}. {r}" for i, r in enumerate(RACES))
    await update.message.reply_text(
        f"職業：**{chosen}**\n\n請選擇種族（輸入數字或種族名稱）：\n{race_list}",
        parse_mode="Markdown",
    )
    return CHOOSING_RACE


async def receive_race(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    chosen = None
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(RACES):
            chosen = RACES[idx]
    else:
        for r in RACES:
            if text in r or r in text:
                chosen = r
                break
    if not chosen:
        await update.message.reply_text("請輸入有效的種族數字或名稱。")
        return CHOOSING_RACE
    context.user_data["char_race"] = chosen
    bg_list = "\n".join(f"{i+1}. {b}" for i, b in enumerate(BACKGROUNDS))
    await update.message.reply_text(
        f"種族：**{chosen}**\n\n請選擇背景（輸入數字或背景名稱）：\n{bg_list}",
        parse_mode="Markdown",
    )
    return CHOOSING_BACKGROUND


async def receive_background(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    chosen = None
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(BACKGROUNDS):
            chosen = BACKGROUNDS[idx]
    else:
        for b in BACKGROUNDS:
            if text in b or b in text:
                chosen = b
                break
    if not chosen:
        await update.message.reply_text("請輸入有效的背景數字或名稱。")
        return CHOOSING_BACKGROUND
    context.user_data["char_background"] = chosen

    name = context.user_data["char_name"]
    char_class = context.user_data["char_class"]
    race = context.user_data["char_race"]
    background = chosen

    await update.message.reply_text(
        f"🎲 正在為你生成角色：**{name}**（{race} {char_class}）...",
        parse_mode="Markdown",
    )

    prompt = f"""你是一位專業的D&D 5E角色生成助手。
請為以下角色生成**完整而實戰向**的角色卡：

名稱：{name}
職業：{char_class}
種族：{race}
背景：{background}

請使用標準的D&D 5E規則生成，並確保所有數值可以直接用於實戰擲骰：
1. 使用標準數組（15, 14, 13, 12, 10, 8）分配六大屬性，並根據種族加成調整
2. 計算HP（使用職業生命骰的平均值 + 體質調整值）
3. 計算AC（根據職業和裝備決定）
4. 根據種族設定速度（呎）
5. 根據職業和背景選擇技能熟練項
6. 列出此職業/種族在1級時的主要職業特性/種族特性（例如：怒氣、潛行攻擊、黑暗視覺等），以簡短文字描述效果
7. 根據職業給予起始裝備（用中文命名），並在文字中簡短標註其效果或加值（例如：「長劍（1d8斬擊，攻擊骰+STR修正）」）
8. 如果是施法職業，生成起始法術和法術位：
   - "spells.cantrips" 放入戲法列表
   - "spells.1" 放入1環法術名稱列表
   - "spell_slots" 內標明各環法術位數量（例如：{{"1": 2}}）
9. 生成一段生動的廣東話性格描述（50字以內，要有個性）

只返回JSON，格式：
{{
  "name": "{name}",
  "class": "{char_class}",
  "race": "{race}",
  "background": "{background}",
  "stats": {{"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}},
  "hp": 10,
  "max_hp": 10,
  "armor_class": 10,
  "speed": 30,
  "saving_throws": {{"STR": 0, "DEX": 0, "CON": 0, "INT": 0, "WIS": 0, "CHA": 0}},
  "skills": {{"Acrobatics": 0, "Athletics": 2}},
  "inventory": ["短劍", "皮甲"],
  "spells": {{"cantrips": [], "1": []}},
  "spell_slots": {{"1": 0}},
  "personality": "性格描述"
}}"""

    try:
        # chat_json 係 async，先 await，再由 JSON string 轉做 dict
        raw = await chat_json([{"role": "user", "content": prompt}])
        sheet = json.loads(raw)

        if "armor_class" not in sheet:
            dex_mod = (sheet["stats"]["DEX"] - 10) // 2
            sheet["armor_class"] = 10 + dex_mod
        if "speed" not in sheet:
            sheet["speed"] = 30
        if "personality" not in sheet:
            sheet["personality"] = ""

        context.user_data["char_sheet"] = sheet
        await update.message.reply_text(_format_sheet_preview(sheet), parse_mode="Markdown")
        return CONFIRMING

    except Exception as e:
        await update.message.reply_text(f"生成角色時發生錯誤：{str(e)}\n請稍後重試。")
        return ConversationHandler.END


async def confirm_character(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    if text in ["否", "no", "n", "重新開始"]:
        await update.message.reply_text("已取消，請重新輸入 /newchar 開始。")
        return ConversationHandler.END

    if text not in ["是", "yes", "y", "確認"]:
        await update.message.reply_text("請輸入 **是** 確認，或 **否** 重新開始。", parse_mode="Markdown")
        return CONFIRMING

    sheet = context.user_data["char_sheet"]
    campaign_id = context.user_data["campaign_id"]
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    char = create_character(campaign_id, user_id, username, sheet)
    char_id = char["id"]

    await update.message.reply_text(
        f"✅ **{char['name']}** 已成功建立！\n\n"
        f"🪪 角色 ID：`{char_id}`\n"
        f"（請記下此 ID，未來可用 /usechar 將此角色帶入新戰役）\n\n"
        f"輸入 /mychar 查看完整角色卡。",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cancel_newchar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("已取消角色建立。")
    return ConversationHandler.END


# ── /mychar ────────────────────────────────────────────────────────────────────

async def cmd_mychar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。")
        return

    user_id = update.effective_user.id
    char = get_character_by_user(campaign["id"], user_id)
    if not char:
        await update.message.reply_text("你還沒有角色，請使用 /newchar 建立角色。")
        return

    await update.message.reply_text(_format_mychar(char), parse_mode="Markdown")


# ── /usechar ───────────────────────────────────────────────────────────────────

async def cmd_usechar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("請先輸入 /newgame 開始戰役。")
        return ConversationHandler.END

    user_id = update.effective_user.id
    existing = get_character_by_user(campaign["id"], user_id)
    if existing:
        await update.message.reply_text(
            f"你在本戰役已有角色：**{existing['name']}**\n輸入 /mychar 查看。",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    if context.args:
        return await _do_usechar(update, context, campaign, user_id, context.args[0])

    context.user_data["usechar_campaign_id"] = campaign["id"]
    await update.message.reply_text(
        "請輸入你的角色 ID（建立角色時顯示的 UUID）："
    )
    return USECHAR_WAITING_ID


async def receive_usechar_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    char_id = update.message.text.strip()
    campaign_id = context.user_data.get("usechar_campaign_id")
    campaign = {"id": campaign_id}
    user_id = update.effective_user.id
    return await _do_usechar(update, context, campaign, user_id, char_id)


async def _do_usechar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    campaign: dict,
    user_id: int,
    char_id: str,
) -> int:
    char = get_character_by_id(char_id)
    if not char:
        await update.message.reply_text("找不到此角色 ID，請確認後重試。")
        return ConversationHandler.END

    if str(char["user_id"]) != str(user_id):
        await update.message.reply_text("此角色不屬於你，無法使用。")
        return ConversationHandler.END

    # 使用角色時，不再複製一份新角色，而係：
    # 1) 將此角色連結到當前戰役
    # 2) 重置 HP 並確保 active = True
    link_character_to_campaign(char["id"], campaign["id"], active=True)
    update_character(char["id"], {"hp": char["max_hp"], "active": True})
    await update.message.reply_text(
        f"✅ **{char['name']}** 已加入本戰役！\n\n"
        f"HP 已恢復至 {char['max_hp']}/{char['max_hp']}。\n"
        f"輸入 /mychar 查看角色卡。",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cancel_usechar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("已取消。")
    return ConversationHandler.END


# ── Level-up flow ──────────────────────────────────────────────────────────────

async def handle_levelup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    char: dict,
) -> None:
    """Called by combat_handlers after XP award triggers a level-up."""
    new_level = char["level"]
    char_id = char["id"]

    prompt = f"""你是D&D 5E的地下城主，負責處理角色升級。

角色資料：
- 名稱：{char['name']}
- 職業：{char['class']}
- 種族：{char['race']}
- 新等級：{new_level}
- 目前裝備：{', '.join(char.get('inventory', []))}
- 目前法術：{json.dumps(char.get('spells', {}), ensure_ascii=False)}

請根據D&D 5E規則，為此角色的 {new_level} 級升級生成選項。
包括（視職業而定）：
1. HP增加（必填：生命骰平均值 + 體質調整值）
2. 新特性（如適用）
3. 法術選擇（施法職業：列出3個可選新法術）
4. 子職業選擇（如適用，例如3級時）
5. 屬性值提升選項（如適用，例如4級時）

只返回JSON：
{{
  "hp_increase": 5,
  "new_features": ["特性名稱：描述"],
  "spell_choices": ["法術A", "法術B", "法術C"],
  "subclass_choices": ["子職業A", "子職業B", "子職業C"],
  "asi_choices": [["STR", "DEX"], ["CON", "WIS"]],
  "summary": "升到{new_level}級的簡短廣東話說明"
}}

如果某項不適用，設為空列表 []。"""

    try:
        raw = await chat_json([{"role": "user", "content": prompt}])
        options = json.loads(raw)
    except Exception:
        options = {
            "hp_increase": 1,
            "new_features": [],
            "spell_choices": [],
            "subclass_choices": [],
            "asi_choices": [],
            "summary": "升級了！",
        }

    context.user_data["levelup_char_id"] = char_id
    context.user_data["levelup_options"] = options

    hp_increase = options.get("hp_increase", 1)
    summary = options.get("summary", "")

    # Apply HP and features immediately (no choice needed)
    new_max_hp = char["max_hp"] + hp_increase
    immediate_updates: dict = {"max_hp": new_max_hp, "hp": new_max_hp}
    if options.get("new_features"):
        inv = list(char.get("inventory", []))
        inv.extend(options["new_features"])
        immediate_updates["inventory"] = inv
    update_character(char_id, immediate_updates)

    msg_lines = [
        f"🎉 **{char['name']} 升到 {new_level} 級！**",
        summary,
        f"❤️ 最大HP +{hp_increase}（現為 {new_max_hp}）",
    ]
    if options.get("new_features"):
        msg_lines.append("\n**新特性：**")
        for f in options["new_features"]:
            msg_lines.append(f"  • {f}")

    # Determine first interactive choice
    pending_choice = None
    if options.get("subclass_choices"):
        pending_choice = "subclass"
        msg_lines.append("\n**請選擇子職業（輸入數字）：**")
        for i, s in enumerate(options["subclass_choices"]):
            msg_lines.append(f"  {i+1}. {s}")
    elif options.get("spell_choices"):
        pending_choice = "spell"
        msg_lines.append("\n**請選擇一個新法術（輸入數字）：**")
        for i, s in enumerate(options["spell_choices"]):
            msg_lines.append(f"  {i+1}. {s}")
    elif options.get("asi_choices"):
        pending_choice = "asi"
        msg_lines.append("\n**屬性值提升：請選擇（輸入數字）：**")
        for i, pair in enumerate(options["asi_choices"]):
            msg_lines.append(f"  {i+1}. {pair[0]} 和 {pair[1]} 各+1")

    context.user_data["levelup_awaiting"] = pending_choice

    if pending_choice is None:
        msg_lines.append("\n升級完成！輸入 /mychar 查看角色卡。")

    await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")


async def receive_levelup_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    awaiting = context.user_data.get("levelup_awaiting")
    options = context.user_data.get("levelup_options", {})
    char_id = context.user_data.get("levelup_char_id")

    if not awaiting or not char_id:
        return ConversationHandler.END

    char = get_character_by_id(char_id)
    if not char:
        return ConversationHandler.END

    if not text.isdigit():
        await update.message.reply_text("請輸入數字選擇。")
        return LEVELUP_CHOOSING

    idx = int(text) - 1
    updates: dict = {}

    if awaiting == "subclass":
        choices = options.get("subclass_choices", [])
        if 0 <= idx < len(choices):
            updates["subclass"] = choices[idx]
            await update.message.reply_text(f"✅ 選擇子職業：**{choices[idx]}**", parse_mode="Markdown")
        else:
            await update.message.reply_text("無效選項，請重試。")
            return LEVELUP_CHOOSING

    elif awaiting == "spell":
        choices = options.get("spell_choices", [])
        if 0 <= idx < len(choices):
            chosen = choices[idx]
            spells = char.get("spells", {})
            for lvl in ["1", "2", "3", "4", "5", "6", "7", "8", "9"]:
                if lvl in spells and isinstance(spells[lvl], list):
                    spells[lvl].append(chosen)
                    break
            updates["spells"] = spells
            await update.message.reply_text(f"✅ 學會新法術：**{chosen}**", parse_mode="Markdown")
        else:
            await update.message.reply_text("無效選項，請重試。")
            return LEVELUP_CHOOSING

    elif awaiting == "asi":
        choices = options.get("asi_choices", [])
        if 0 <= idx < len(choices):
            pair = choices[idx]
            stats = char.get("stats", {})
            for attr in pair:
                stats[attr] = stats.get(attr, 10) + 1
            updates["stats"] = stats
            await update.message.reply_text(
                f"✅ 屬性提升：{pair[0]} +1，{pair[1]} +1", parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("無效選項，請重試。")
            return LEVELUP_CHOOSING

    if updates:
        update_character(char_id, updates)

    # Chain to next choice if any
    if awaiting == "subclass" and options.get("spell_choices"):
        context.user_data["levelup_awaiting"] = "spell"
        lines = ["**請選擇一個新法術（輸入數字）：**"]
        for i, s in enumerate(options["spell_choices"]):
            lines.append(f"  {i+1}. {s}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return LEVELUP_CHOOSING

    if awaiting in ["subclass", "spell"] and options.get("asi_choices"):
        context.user_data["levelup_awaiting"] = "asi"
        lines = ["**屬性值提升：請選擇（輸入數字）：**"]
        for i, pair in enumerate(options["asi_choices"]):
            lines.append(f"  {i+1}. {pair[0]} 和 {pair[1]} 各+1")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return LEVELUP_CHOOSING

    await update.message.reply_text("🎉 升級完成！輸入 /mychar 查看更新後的角色卡。")
    return ConversationHandler.END


# ── ConversationHandler factories ──────────────────────────────────────────────

def get_newchar_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("newchar", cmd_newchar)],
        states={
            CHOOSING_NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            CHOOSING_CLASS:      [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_class)],
            CHOOSING_RACE:       [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_race)],
            CHOOSING_BACKGROUND: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_background)],
            CONFIRMING:          [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_character)],
        },
        fallbacks=[CommandHandler("cancel", cancel_newchar)],
    )


def get_usechar_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("usechar", cmd_usechar)],
        states={
            USECHAR_WAITING_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_usechar_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel_usechar)],
    )


def get_levelup_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[],
        states={
            LEVELUP_CHOOSING: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_levelup_choice)],
        },
        fallbacks=[CommandHandler("cancel", cancel_usechar)],
        name="levelup",
        persistent=False,
    )
