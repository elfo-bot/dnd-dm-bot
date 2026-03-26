from __future__ import annotations
from telegram import Update
from telegram.ext import ContextTypes
from db import campaigns, events as events_db
from db import episodes as episodes_db
from dm import context_builder, memory_manager
from dm.deepseek_client import chat
import config


CAMPAIGN_CHOICES: list[dict] = [
    {
        "key": "lmop",
        "title": "Lost Mine of Phandelver（失落的芬德爾礦坑）",
        "episodes": [
            {
                "initiating_event": "你哋接到護送貨車去潘達林嘅委託，但路上有人刻意設伏。",
                "goal": "查清伏擊背後主使，同時確保貨物與委託人線索唔消失。",
                "ending": "搵到關鍵線索（例如：俘虜口供、標記物品、藏身處證物）指向潘達林勢力。",
            },
            {
                "initiating_event": "潘達林表面平靜，但地下勢力橫行；有人唔想你哋繼續查。",
                "goal": "找出操控城鎮嘅黑手，拆穿佢哋嘅保護傘。",
                "ending": "攞到能證明頭目身份嘅證據／帳冊，並迫使佢哋撤退或被捕。",
            },
            {
                "initiating_event": "線索指向一處古老遺跡，同傳說中嘅礦坑秘密有關。",
                "goal": "解開遺跡謎團，搶先一步抵達真正目標地點。",
                "ending": "破解守衛／謎題取得通行關鍵，確認最終敵人計劃。",
            },
            {
                "initiating_event": "你哋踏入最終地城：敵人已準備好伏擊同陷阱。",
                "goal": "阻止敵人奪取礦坑核心秘密，擊敗幕後主使。",
                "ending": "最終頭目倒下或逃亡、核心資源被你哋掌握，潘達林局勢穩定。",
            },
        ],
    },
    {
        "key": "icespire",
        "title": "Dragon of Icespire Peak（冰脊峰之龍）",
        "episodes": [
            {
                "initiating_event": "寒風帶來龍影；鎮上委託板貼滿求助。",
                "goal": "調查龍行蹤，同時建立盟友網絡。",
                "ending": "確定龍的活動區域與弱點（巢穴線索、目擊證詞、痕跡）。",
            },
            {
                "initiating_event": "分散嘅威脅其實被同一股力量利用。",
                "goal": "揪出趁亂擴張嘅勢力，避免城鎮瓦解。",
                "ending": "解決一個關鍵據點並取得情報，指向龍巢或其代理人。",
            },
            {
                "initiating_event": "你哋鎖定龍巢，但對方早有準備。",
                "goal": "突破嚴酷環境與守衛，完成獵龍／逼退。",
                "ending": "龍被擊敗或被迫撤離，周邊聚落得以喘息。",
            },
        ],
    },
    {
        "key": "cos",
        "title": "Curse of Strahd（史卓德詛咒）",
        "episodes": [
            {
                "initiating_event": "迷霧將你哋吞噬，醒來已身處巴羅維亞；城鎮人心惶惶。",
                "goal": "理解詛咒規則，找出離開迷霧嘅可能。",
                "ending": "取得第一批關鍵預兆／線索，確認史卓德對你哋嘅興趣與陷阱。",
            },
            {
                "initiating_event": "盟友難辨真偽；每個 NPC 都可能有秘密與代價。",
                "goal": "蒐集能對抗史卓德嘅象徵物／盟友，破解迷霧謎團。",
                "ending": "成功救下或說服一名關鍵盟友，並掌握一件對抗之物。",
            },
            {
                "initiating_event": "城堡召喚；史卓德開始主動出手。",
                "goal": "在城堡與詭計中生存，終結詛咒。",
                "ending": "史卓德被擊敗／詛咒鬆動，迷霧裂開出口。",
            },
        ],
    },
    {
        "key": "toa",
        "title": "Tomb of Annihilation（滅亡之墓）",
        "episodes": [
            {
                "initiating_event": "死亡詛咒蔓延；復活失效，生命被慢慢抽走。",
                "goal": "找出詛咒源頭所在嘅大方向，準備深入叢林。",
                "ending": "確立通往核心區域嘅路線與必要資源／嚮導。",
            },
            {
                "initiating_event": "叢林裏面線索真假難分，古遺跡充滿陷阱。",
                "goal": "循線逐步逼近源頭，避免被假線索帶偏。",
                "ending": "取得可以定位最終地點嘅關鍵證物／地圖碎片。",
            },
            {
                "initiating_event": "你哋抵達終局地城；每一步都係死亡機關。",
                "goal": "關閉詛咒裝置並擊敗守護者。",
                "ending": "詛咒終止，城市重獲希望。",
            },
        ],
    },
    {
        "key": "wdh",
        "title": "Waterdeep: Dragon Heist（深水城：龍金劫案）",
        "episodes": [
            {
                "initiating_event": "深水城暗流湧動；一樁失蹤案牽扯到巨額寶藏。",
                "goal": "建立立足點，追查第一條寶藏線索。",
                "ending": "取得能指向下一名線索持有者嘅情報（暗號、地點、關鍵 NPC）。",
            },
            {
                "initiating_event": "多方勢力同時追逐，你哋被拉入陰謀。",
                "goal": "識破誰在利用你哋，搶先取得核心線索。",
                "ending": "搶到關鍵物件／密鑰，並揭露至少一個勢力嘅真正目的。",
            },
            {
                "initiating_event": "終局係一場城市級追逐：談判、潛入、調包都要用。",
                "goal": "保住寶藏線索並作出選擇：交予誰、或點樣處置。",
                "ending": "寶藏歸屬定局，深水城局勢因此改變。",
            },
        ],
    },
    {
        "key": "dotmm",
        "title": "Dungeon of the Mad Mage（瘋法師地城）",
        "episodes": [
            {
                "initiating_event": "地城入口出現異象；失蹤者與怪物湧現。",
                "goal": "建立生存節奏：補給、情報、路線圖。",
                "ending": "掌握第一層勢力分佈與通往更深處嘅方法。",
            },
            {
                "initiating_event": "更深層開始出現「迷你謀局」：派系互鬥、交易與背叛。",
                "goal": "喺派系鬥爭中獲利，搵到主線入口。",
                "ending": "取得通行權／鑰匙，令你哋可以安全下潛。",
            },
            {
                "initiating_event": "瘋法師開始注意到你哋；地城規則被改寫。",
                "goal": "對抗地城本身嘅惡意，找出離開或終結控制嘅方法。",
                "ending": "破解地城核心機制或逼退其主宰，成功脫出。",
            },
        ],
    },
    {
        "key": "dia",
        "title": "Descent into Avernus（墮入阿弗納斯）",
        "episodes": [
            {
                "initiating_event": "一座城市墜入地獄；證據顯示有人簽咗可怕契約。",
                "goal": "追查契約鏈，找出城市被拖走嘅真正原因。",
                "ending": "掌握通往地獄之路與關鍵契約條款。",
            },
            {
                "initiating_event": "阿弗納斯戰場：盟友都係惡魔與魔鬼，交易比刀更危險。",
                "goal": "用情報與交易換取推進機會，同時避免被契約反噬。",
                "ending": "取得可以扭轉契約／救城嘅核心資源或關鍵人物。",
            },
            {
                "initiating_event": "終局抉擇：救城定換取其他代價？",
                "goal": "打破束縛或以更高明手段贏過地獄規則。",
                "ending": "城市獲救／契約解除，或世界因此改寫。",
            },
        ],
    },
    {
        "key": "skt",
        "title": "Storm King's Thunder（風暴王之雷）",
        "episodes": [
            {
                "initiating_event": "巨人四處襲擊；各地傳言互相矛盾。",
                "goal": "調查巨人行動模式，找出背後失序原因。",
                "ending": "取得能指向巨人政治核心嘅線索（符號、使者、古物）。",
            },
            {
                "initiating_event": "真相唔係單純侵略；有人操縱各族矛盾。",
                "goal": "穿梭各地建立盟友，阻止災難升級。",
                "ending": "揭露操縱者並鎖定最終對峙地點。",
            },
            {
                "initiating_event": "風暴核心：你哋要喺巨人王庭／敵對勢力之間做決定。",
                "goal": "止戰、奪回秩序或擊敗真正主使。",
                "ending": "巨人秩序重建，世界危機解除。",
            },
        ],
    },
    {
        "key": "oota",
        "title": "Out of the Abyss（深淵魔劫）",
        "episodes": [
            {
                "initiating_event": "你哋被困地底黑暗世界；目標只得一個：生存與逃走。",
                "goal": "逃出追捕，找到通往地表嘅路。",
                "ending": "成功抵達相對安全據點並獲得深淵異變線索。",
            },
            {
                "initiating_event": "深淵力量滲透；盟友精神崩潰，幻覺與陰謀交織。",
                "goal": "確認異變源頭與其擴散方式，蒐集對抗方法。",
                "ending": "取得能封印／對抗深淵之物或關鍵儀式線索。",
            },
            {
                "initiating_event": "終局：深淵主宰現身，世界邊界裂開。",
                "goal": "阻止全面入侵並拯救仍然清醒嘅人。",
                "ending": "裂縫被封、主宰被逼退，地底回復平衡。",
            },
        ],
    },
    {
        "key": "rotfm",
        "title": "Rime of the Frostmaiden（霜巫之凍）",
        "episodes": [
            {
                "initiating_event": "永夜降臨十城；失蹤案同怪談頻發。",
                "goal": "調查永夜與連環事件背後關聯，建立信任。",
                "ending": "找出永夜關鍵線索（儀式痕跡、目擊者、古老符記）。",
            },
            {
                "initiating_event": "真相牽涉到更古老嘅秘密與被掩埋嘅遺跡。",
                "goal": "在暴風雪與敵意中找到遺跡入口，破解守護謎團。",
                "ending": "取得能打破永夜嘅核心資源／知識。",
            },
            {
                "initiating_event": "終局：你哋面對永夜之源與其守護者。",
                "goal": "終止永夜，令十城重新見到日光。",
                "ending": "永夜解除（或以代價解除），十城命運被改寫。",
            },
        ],
    },
]


async def cmd_newgame(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    existing = campaigns.get_active_campaign(chat_id)
    if existing:
        await update.message.reply_text(
            "已有進行中的戰役！輸入 /status 查看狀態，或 /endgame 結束目前戰役。"
        )
        return
    campaign = campaigns.create_campaign(chat_id, module="lmop")
    opening = (
        "⚔️ **新戰役已建立！**\n\n"
        "請每位玩家輸入 /newchar 建立你的角色。\n"
        "所有人準備好後，輸入 /startadventure 以選擇冒險模組並開始冒險。"
    )
    await update.message.reply_text(opening, parse_mode="Markdown")
    events_db.log_event(campaign["id"], "系統", "新戰役開始", event_type="system")


async def cmd_startadventure(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("尚未開始戰役，請先輸入 /newgame。")
        return

    from db.characters import get_characters
    chars = get_characters(campaign["id"])
    if not chars:
        await update.message.reply_text("還沒有任何角色！請先輸入 /newchar 建立角色。")
        return

    # Campaign selection: /startadventure <1-10>
    chosen_idx: int | None = None
    if context.args and context.args[0].isdigit():
        idx = int(context.args[0])
        if 1 <= idx <= len(CAMPAIGN_CHOICES):
            chosen_idx = idx - 1

    if chosen_idx is None:
        lines = [
            "請揀一個冒險模組開始（輸入：`/startadventure <編號>`）：",
            "",
        ]
        for i, c in enumerate(CAMPAIGN_CHOICES, start=1):
            lines.append(f"{i}. **{c['title']}**")
        lines.append("")
        lines.append("（注意：內容係根據官方模組大綱重述，會因應你哋行動動態變化。）")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    chosen = CAMPAIGN_CHOICES[chosen_idx]
    campaigns.update_campaign(campaign["id"], {"status": "active", "module": chosen["key"]})

    # Seed campaign episodes (episode 1 active, rest planned)
    for i, ep in enumerate(chosen["episodes"], start=1):
        episodes_db.upsert_episode(
            campaign["id"],
            episode_number=i,
            initiating_event=ep["initiating_event"],
            goal=ep["goal"],
            ending=ep["ending"],
            status="active" if i == 1 else "planned",
        )

    char_names = "、".join(c["name"] for c in chars)

    # Build an opening scene prompt based on active episode
    active_ep = episodes_db.get_active_episode(campaign["id"])
    ep_text = ""
    if active_ep:
        ep_text = (
            f"目前 Episode：第{active_ep['episode_number']}集\n"
            f"- 起因：{active_ep.get('initiating_event','')}\n"
            f"- 目標：{active_ep.get('goal','')}\n"
            f"- 結局條件：{active_ep.get('ending','')}\n"
        )
    opening_msg = [
        {
            "role": "system",
            "content": context_builder.build_system_prompt(),
        },
        {
            "role": "user",
            "content": (
                f"你而家主持嘅模組：{chosen['title']}\n\n"
                f"{ep_text}\n"
                f"冒險者：{char_names}\n"
                "請用廣東話繁體中文，以DM身份，用生動電影感描述開場場景（約 150-250 字）：\n"
                "1. 交代目前 Episode 嘅核心情境同主要謎團／威脅（偏偵探推理、唔好淨係打）\n"
                "2. 描述環境、氣氛同最少一個可互動 NPC\n"
                "3. 提供 2-3 條可行動嘅方向（例如調查、談判、潛入），最後以一個明確問題結尾"
            ),
        },
    ]
    # 劇集開場敘事：偏穩定，避免大幅偏離模組
    response = await chat(opening_msg, temperature=0.65, max_tokens=600)
    await update.message.reply_text(response, parse_mode="Markdown")
    events_db.log_event(campaign["id"], "DM", response, event_type="narrative")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。輸入 /newgame 開始！")
        return
    from db.characters import get_characters
    chars = get_characters(campaign["id"])
    from dm.context_builder import build_character_block
    blocks = "\n\n".join(build_character_block(c) for c in chars)
    world = campaigns.get_world_state(campaign["id"])
    world_text = "\n".join(f"• {k}：{v}" for k, v in world.items()) or "（尚無重要記錄）"
    location = campaign.get("current_location", "未知")
    act = campaign.get("act", 1)
    status_text = (
        f"📜 **戰役狀態**\n"
        f"地點：{location}  幕：第{act}幕\n\n"
        f"**冒險者**：\n{blocks}\n\n"
        f"**世界狀態**：\n{world_text}"
    )
    await update.message.reply_text(status_text, parse_mode="Markdown")


async def cmd_recap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。")
        return
    await update.message.reply_text("📖 生成回顧中...")
    recap = await memory_manager.generate_recap(campaign["id"])
    await update.message.reply_text(f"📖 **之前的故事...**\n\n{recap}", parse_mode="Markdown")


async def cmd_endgame(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。")
        return
    campaigns.end_campaign(campaign["id"])
    await update.message.reply_text(
        "🏁 戰役已結束。感謝各位冒險者！\n輸入 /newgame 開始新的冒險。"
    )


async def cmd_setlocation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """DM-only: set current location. Usage: /setlocation <location_key>"""
    chat_id = update.effective_chat.id
    campaign = campaigns.get_active_campaign(chat_id)
    if not campaign:
        await update.message.reply_text("目前沒有進行中的戰役。")
        return
    if not context.args:
        from dm.module_lmop import LOCATIONS
        locs = "\n".join(f"• `{k}` — {v['name']}" for k, v in LOCATIONS.items())
        await update.message.reply_text(f"用法：`/setlocation <地點代碼>`\n\n可用地點：\n{locs}", parse_mode="Markdown")
        return
    loc_key = context.args[0].lower()
    from dm.module_lmop import LOCATIONS
    if loc_key not in LOCATIONS:
        await update.message.reply_text(f"未知地點：{loc_key}")
        return
    loc_data = LOCATIONS[loc_key]
    campaigns.update_campaign(campaign["id"], {
        "current_location": loc_key,
        "act": loc_data["act"],
    })
    events_db.log_event(campaign["id"], "系統", f"地點更改為：{loc_data['name']}", event_type="system")
    await update.message.reply_text(f"✅ 地點已設為：**{loc_data['name']}**（第{loc_data['act']}幕）", parse_mode="Markdown")