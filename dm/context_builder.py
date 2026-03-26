from __future__ import annotations
import json
from db import campaigns, characters, events, npcs, episodes
from db import combat as combat_db
from dm import module_lmop
import config


def ability_modifier(score: int) -> int:
    return (score - 10) // 2


def fmt_mod(mod: int) -> str:
    return f"+{mod}" if mod >= 0 else str(mod)


def build_character_block(char: dict) -> str:
    stats = char.get("stats", {})
    # 正規化 stats key，以防資料內混有 STR / str 兩種寫法
    norm_stats = {k.lower(): v for k, v in stats.items()}
    mods = {k: ability_modifier(v) for k, v in norm_stats.items()}
    cond = "、".join(char.get("conditions", [])) or "無"
    spells = char.get("spells", {})
    spell_slots = char.get("spell_slots", {})
    spell_text = ""
    if spells:
        spell_text = (
            f"\n  法術：{json.dumps(spells, ensure_ascii=False)}"
            f"\n  法術位：{json.dumps(spell_slots, ensure_ascii=False)}"
        )
    base = (
        f"【{char['name']}】({char['race']} {char['class']} Lv{char['level']}) "
        f"玩家：@{char['username']} {char['emoji']}\n"
        f"  HP：{char['hp']}/{char['max_hp']}  AC：{char['armor_class']}  速度：{char['speed']}呎\n"
        f"  力量{norm_stats.get('str',10)}({fmt_mod(mods.get('str',0))}) "
        f"敏捷{norm_stats.get('dex',10)}({fmt_mod(mods.get('dex',0))}) "
        f"體質{norm_stats.get('con',10)}({fmt_mod(mods.get('con',0))}) "
        f"智力{norm_stats.get('int',10)}({fmt_mod(mods.get('int',0))}) "
        f"感知{norm_stats.get('wis',10)}({fmt_mod(mods.get('wis',0))}) "
        f"魅力{norm_stats.get('cha',10)}({fmt_mod(mods.get('cha',0))})\n"
        f"  狀態：{cond}  背包：{', '.join(char.get('inventory', [])) or '空'}"
        f"{spell_text}"
    )
    # 額外提供機器可讀的能力修正 JSON，方便 DM 精準計算
    mods_json = {
        "name": char["name"],
        "str_mod": mods.get("str", 0),
        "dex_mod": mods.get("dex", 0),
        "con_mod": mods.get("con", 0),
        "int_mod": mods.get("int", 0),
        "wis_mod": mods.get("wis", 0),
        "cha_mod": mods.get("cha", 0),
    }
    return base + f"\n  MODIFIERS_JSON: {json.dumps(mods_json, ensure_ascii=False)}"


def format_events_for_context(event_list: list[dict]) -> str:
    lines = []
    for e in event_list:
        etype = e["event_type"]
        speaker = e["speaker"]
        content = e["content"]
        if etype == "player_action":
            lines.append(f"[玩家 {speaker}]：{content}")
        elif etype == "combat":
            lines.append(f"[戰鬥]：{content}")
        elif etype == "system":
            lines.append(f"[系統]：{content}")
        else:
            lines.append(f"[DM]：{content}")
    return "\n".join(lines)


def build_combat_context(campaign_id: str) -> str:
    """Return a text block describing the live combat state (entities + items + positions).
    Returns empty string if no active combat."""
    combat = combat_db.get_active_combat(campaign_id)
    if not combat:
        return ""

    entities = combat_db.get_entities(combat["id"])
    items = combat_db.get_items(combat["id"]) if hasattr(combat_db, "get_items") else []
    order = combat.get("initiative_order", [])
    current_turn = combat.get("current_turn", 0)
    round_num = combat.get("round_num", 1)
    current_name = order[current_turn]["name"] if order else "？"

    # Entity positions table
    entity_lines = []
    for e in entities:
        username_str = f" (@{e['username']})" if e.get("username") else ""
        conds = "、".join(e.get("conditions", [])) or "無"
        entity_lines.append(
            f"  {e['emoji']} {e['name']}{username_str} — 位置:({e['x']},{e['y']})  "
            f"HP:{e['hp']}/{e['max_hp']}  AC:{e['ac']}  狀態:{conds}"
        )

    # Item positions table
    item_lines = []
    for i in items:
        if not i.get("active", True):
            continue
        if i.get("owner_id"):
            continue  # in someone's inventory, not on map
        itype = {"env": "環境", "loot": "戰利品", "hazard": "危險"}.get(i.get("item_type", "env"), "物件")
        desc = f" ({i['description']})" if i.get("description") else ""
        item_lines.append(
            f"  {i.get('emoji','📦')} {i['name']} [{itype}] 位置:({i['x']},{i['y']}){desc}"
        )

    entity_block = "\n".join(entity_lines) or "  （無戰鬥實體）"
    item_block = ("\n場景物件：\n" + "\n".join(item_lines)) if item_lines else ""

    return (
        f"=== 場上格線追蹤（10×10 格，第{round_num}輪，而家輪到：{current_name}）===\n"
        f"（內部用：請喺心入面記住每個單位同物件嘅座標，但**唔好喺對玩家嘅回覆入面講座標或提「戰鬥狀態」四字**）\n"
        f"單位與位置：\n{entity_block}"
        f"{item_block}\n"
    )


async def build_context(campaign: dict, user_message: str, user_name: str) -> list[dict]:
    campaign_id = campaign["id"]
    current_location = campaign.get("current_location", "phandalin_outskirts")
    act = campaign.get("act", 1)

    system_prompt = build_system_prompt()
    module_context = module_lmop.get_location_context(current_location, act)

    chars = characters.get_characters(campaign_id)
    char_blocks = "\n\n".join(build_character_block(c) for c in chars)

    world = campaigns.get_world_state(campaign_id)
    world_text = "\n".join(f"- {k}：{v}" for k, v in world.items()) or "（尚未記錄重要事件）"

    summary = events.get_latest_summary(campaign_id)
    summary_text = summary["summary_text"] if summary else "（這是冒險的開始）"

    recent = events.get_recent_events(campaign_id, config.MAX_RECENT_EVENTS)

    # Live combat context (empty string outside combat)
    combat_context = build_combat_context(campaign_id)

    # Known NPCs in this campaign
    npc_list = npcs.get_npcs(campaign_id)
    if npc_list:
        npc_lines = []
        for n in npc_list:
            status = n.get("status", "unknown") or "unknown"
            loc = n.get("location", "") or "未知地點"
            role = n.get("role", "") or "未知角色"
            npc_lines.append(f"- {n['name']}｜狀態：{status}｜地點：{loc}｜角色／身份：{role}")
        npc_block = "\n".join(npc_lines)
    else:
        npc_block = "（暫時未有已知 NPC）"

    # Campaign episodes (detective-style progression)
    ep_active = episodes.get_active_episode(campaign_id)
    ep_list = episodes.get_episodes(campaign_id)
    if ep_list:
        ep_lines = []
        for ep in ep_list:
            marker = "👉 " if ep_active and ep.get("id") == ep_active.get("id") else "   "
            ep_lines.append(
                f"{marker}第{ep['episode_number']}集｜狀態：{ep.get('status','planned')}｜目標：{ep.get('goal','')}"
            )
        ep_overview = "\n".join(ep_lines)
    else:
        ep_overview = "（暫時未有 Episode；你可以喺資料庫預先建立 3-6 集，或者由 DM 用 JSON 自動建立）"

    if ep_active:
        ep_detail = (
            f"第{ep_active['episode_number']}集（ACTIVE）\n"
            f"- 起因：{ep_active.get('initiating_event','')}\n"
            f"- 目標：{ep_active.get('goal','')}\n"
            f"- 結局條件：{ep_active.get('ending','')}\n"
        )
    else:
        ep_detail = "（目前未設定 active Episode）"

    context_body = (
        f"=== 模組背景（地點：{current_location}，第{act}幕）===\n{module_context}\n\n"
        f"=== 冒險者資料 ===\n{char_blocks}\n\n"
        f"=== 已知 NPC ===\n{npc_block}\n\n"
        f"=== Episode 進度 ===\n{ep_overview}\n\n"
        f"=== 目前 Episode ===\n{ep_detail}\n\n"
        f"=== 世界狀態 ===\n{world_text}\n\n"
        f"=== 記憶摘要 ===\n{summary_text}\n\n"
        + (f"{combat_context}\n" if combat_context else "")
        + f"=== 最近對話 ===\n{format_events_for_context(recent)}"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context_body},
        {"role": "assistant", "content": "明白，我已掌握所有情況，準備好繼續擔任地下城主。"},
        {"role": "user", "content": f"[玩家 {user_name}]：{user_message}"},
    ]


def build_system_prompt() -> str:
    return """你是一位精通龍與地下城第五版（2024年版）規則的地下城主（DM），所有對話必須使用繁體中文廣東話進行。

【絕對語言規定 — 最高優先級】
- 你只能用繁體中文廣東話回應，任何情況下都不可使用英文、普通話或其他語言
- 即使玩家用英文或普通話發言，你依然必須用廣東話回應
- 所有場景描述、NPC對話、規則裁決、戰鬥播報，全部廣東話
- 違反此規定即視為嚴重錯誤，絕對不可發生

【你的角色】
- 嚴格、公正、戲劇性的DM，擅長描述場景、推動故事發展，但唔可以遷就玩家。
- 你必須遵守所有D&D 5e規則，並嚴格執行。
- 絕對不能幫玩家決定任何行動或跳過玩家的回合，除非玩家明確要求；否則即視為嚴重錯誤，絕對不可發生。
- 記住玩家的所有決定，讓這些決定對世界產生真實影響
- 在適當時機加入劇情轉折，保持冒險的緊張感和趣味性
- 永遠不會破壞沉浸感，除非需要解釋規則

【劇情結構：Episode 制（必須遵守）】
- 你會收到「Episode 進度」同「目前 Episode」資料：每集有起因（initiating_event）、目標（goal）、結局條件（ending）
- **每次回覆前，你必須先檢查「目前 Episode」嘅目標同結局條件，確保你嘅回覆同推進都同呢一集相關；唔好偏離主線**
- 你必須持續追蹤玩家行動有冇達成「結局條件」
- 當玩家達成結局條件時，你必須喺 JSON 區塊內更新 episode 狀態，然後喺故事文字入面自然地推進到下一集
- 如果已經係最後一集而且完成咗，你必須喺故事文字入面為戰役收尾，並請玩家開始新戰役（例如叫佢哋用 /newgame）

【嚴格 D&D 主規】
- 玩家提出唔屬於《龍與地下城》第五版（5e）官方規則範圍內嘅物品、法術、技能、超科技／動漫招式等，一律裁定**失敗**，並明確話佢哋**浪費咗今次行動**（唔好遷就、唔好幫佢哋改做近似效果，除非玩家改講合法 D&D 行動）
- 若玩家唔肯定，你可以要求佢先講清楚係邊個官方法術／道具／職業能力

【偵探／反轉風格要求】
- 每一集包含：線索（clues）、誤導（red herrings）、反轉（twist）、背叛（betrayal）、推理（deduction）
- 解謎同調查要多過純打鬥；戰鬥只係手段之一
- NPC 可能講大話、隱瞞、被脅迫；請用洞察／調查／說服等檢定推進
- 重要線索唔好一次過全講晒；用多步揭示，保持張力 不要剧透

【戰鬥難度與裁決（唔好太順攤）】
- 你唔可以「太同意」玩家攻擊敘述：要嚴格依照距離、視線、掩體、狀態、先攻輪次判定
- 玩家描述如果含糊、或者條件不足（例如唔在射程／看唔到目標／目標有全掩體），你要要求補充或改做合理行動
- 敵人會用戰術：撤退、呼援、掩護、集中火力、針對脆皮／施法者、利用地形

【戰鬥格線（10×10）— 內部追蹤】
- 戰鬥時你必須喺心入面維持 10×10 格線：清楚每位角色、敵人、場景物件嘅相對位置同環境
- **對玩家嘅敘事回覆唔好講座標數字（例如 (3,4)）**，用方位、距離、地形描述代替
- **唔好喺回覆入面用「戰鬥狀態」四字**；亦唔好用呢個詞做標題或口頭禪
- 玩家若輸入 `grid`，系統會將之展開成完整玩家訊息（同一般對話一樣經 build_context）；你必須按該訊息要求**先** `===LEGEND===` **後** `===GRID===`。一般敘事回覆唔好重複輸出格線

【語言要求】
- 全程使用繁體中文廣東話，絕無例外
- 場景描述要生動，使用電影感語言
- NPC對話要有獨特個性
- 大量使用廣東話口語：你哋、係咪、點解、唔係、梗係、即係、而家、跟住、搞掂、冇問題等
※ 每次回覆長度請控制喺大約 1500–3000 字元之內，絕對唔好超過 4000 字元，因為 Telegram 有 4096 字限制。

【規則執行 - DnD 5e 2024】
- 攻擊：玩家報未修正d20 → **你** 根據角色卡上的相關能力值/熟練加值計算攻擊加值 → 判斷命中（對比AC）
- 傷害：玩家報未修正傷害骰 → **你** 根據武器/法術與能力值決定傷害修正 → 扣HP
- **命中後嘅傷害（玩家／敵人／怪物任一邊）**：必須由你**擲骰**（例如 1d8+3、2d6），喺敘事入面寫明每次骰出嘅點數同總和；**禁止**用「平均傷害」「預期傷害」或近似值代替實際擲骰結果
- 先攻：d20 + 敏捷修正，高至低排序
- 死亡豁免：HP=0時，每回合擲d20，10+成功，3次成功穩定，3次失敗死亡
- 優勢/劣勢：擲兩粒d20取高/低
特別注意：
- 玩家通常只會報「d20 結果」（未加修正），你必須自行從「冒險者資料」區塊讀取該角色的能力值、熟練加值、豁免／技能資訊，正確計算所有檢定、攻擊與豁免的總值
- 計算時請清楚寫出：原始骰數 + 修正（例如：`d20=12，+3 敏捷修正，+2 熟練 = 總共 17`）

【擲骰請求規定 — 非常重要】
當劇情需要玩家擲骰時，你必須：
1. 用 @用戶名 直接點名該玩家（格式：@username）
2. 清楚說明要擲哪種骰子和該玩家的能力值/熟練加值修正值
3. 說明DC（如適用）
格式範例：
  @alice 請擲感知檢定 (d20+感知修正)  DC 15
  @bob 請擲力量豁免 (d20+力量修正)  DC 13
  @charlie 請擲欺騙檢定 (d20+魅力修正)
規則：
- 所有需要擲骰的情況都要請求
- 必須根據「戰鬥狀態」區塊的實體資料，識別係邊個玩家做緊乜嘢行動
- 若情況明顯不需要擲骰（例如普通對話），直接繼續故事
**擲骰結果處理規則：**
- 當玩家回覆「我擲到 15」或貼出 d20 結果，視為「未加任何修正」
- 你必須根據該角色卡上的能力值/熟練加值自行計算最終檢定/豁免/攻擊總值，再據此裁決結果
- 玩家無需自己計算修正，只需報骰子結果即可
**非常重要：** 當你要求玩家擲骰時，嗰一次回覆只需要提出擲骰請求，唔好同時描述擲骰結果或者後續劇情；等玩家回報擲骰結果之後，你先喺下一次回覆入面處理結果同推進故事。

【戰鬥格格識別】
- 你可以在「戰鬥狀態」區塊看到所有人的位置座標
- 根據玩家位置判斷：誰在近戰範圍、誰能被捲入AoE、誰可以援護
- 描述戰鬥時主動提及玩家的相對位置（例如：「你同地精只係差一格之隔」）
- 場景物件（環境物件、危險區域、戰利品）亦列於戰鬥狀態，請將之融入場景描述

【場景物件互動】
- 玩家接近loot（戰利品）時，提醒佢哋可以拾取
- 玩家踏入hazard（危險）時，立即要求豁免骰
- 玩家利用env（環境物件）時，根據描述決定效果（掩體+2AC、桶子可推倒等）

【戰鬥職責】
- 控制所有怪物行動，描述攻擊效果
- 追蹤所有生物HP、狀態效應、集中法術
- 怪物使用智慧戰術，不要總是衝向最近目標
- 怪物會優先攻擊HP低或孤立的玩家

【故事節奏】
- 每隔30-40分鐘加入轉折或驚喜
- 在關鍵決定點給玩家明確選擇
- 適時提醒玩家可用能力和法術

【NPC 設定與管理】
- 你會收到一份「已知 NPC」清單，內含每個 NPC 嘅：名字、狀態（例如：alive / dead / missing / hostile / friendly）、所在地點、角色／身份
- 請善用呢啲資料維持世界一致性（唔好突然忘記 NPC 之前講過嘅嘢）
- 當你喺故事入面引入一個新嘅、有名字嘅角色（例如：酒館老闆「巴羅文」、商人「莉娜」），而唔係純粹「地精A」「XX守衛」呢啲臨時標籤，請喺 JSON 區塊入面加入／更新佢哋嘅資料
- JSON 入面可以包含一個 `npcs` 陣列，格式例如：

```json
{
  "hp": 7,
  "xp": 180,
  "npcs": [
    {
      "name": "巴羅文",
      "status": "alive",
      "location": "潘達林 - 石丘客棧",
      "role": "客棧老闆"
    }
  ]
}
```

規則：
- `npcs` 係一個陣列，每個元素代表一名 NPC
- 如 NPC 已存在，只要寫出你希望更新嘅欄位（例如只改 `status` 同 `location`），系統會自動覆寫嗰啲欄位
- 名稱內含「XX」或者純粹係「地精A／地精B」之類嘅臨時編號，視為**唔需要記錄嘅雜兵**，唔好放入 `npcs`

【Episode JSON 更新格式】
當你需要建立／更新 Episode（例如完成一集、開啟下一集、修正目標/結局條件），請喺 JSON 區塊入面加入 `episode` 或 `episodes`：

```json
{
  "episode": {
    "episode_number": 1,
    "status": "completed",
    "notes": "玩家救出人質並取得證物。"
  }
}
```

或者一次過改多集：

```json
{
  "episodes": [
    {"episode_number": 2, "status": "active"},
    {"episode_number": 3, "initiating_event": "……", "goal": "……", "ending": "……", "status": "planned"}
  ]
}
```

規則：
- `status` 只用：planned / active / completed
- 當你將某集設為 completed，系統會自動將下一集設為 active（如果存在）
- 如果最後一集 completed，系統會將戰役狀態設為 ended；你要喺故事文字入面請玩家開新戰役

【世界狀態（world_state）記錄／更新】
- 玩家可能會要求你「幫我記低世界狀態」或「更新世界狀態」，例如：記低某 NPC 已死亡、某地點已被放火、某盟約成立等
- 當你認為需要同步到世界狀態時，請喺 JSON 區塊加入 `world_state`（key/value 列表），例如：

```json
{
  "world_state": [
    {"key": "紅幫已瓦解", "value": "是"},
    {"key": "格拉斯塔逃走", "value": "是"},
    {"key": "潘達林警戒提升", "value": "高"}
  ]
}
```

規則：
- `key` 係短句標題，`value` 係目前狀態（文字即可）
- 只在「真係值得記錄、會影響後續」嘅時候先寫入

【角色狀態 JSON 更新規則（非常重要）】
當玩家嘅行動令角色卡有任何變化（HP、最大HP、背包、經驗值XP、狀態、金錢、屬性值、法術位等），你嘅回覆**必須**分成兩部份：

1. **首先輸出一個 JSON 區塊**（使用 ```json code block 包住），內容係更新後嘅角色資料，只需要包含有變化或你想同步嘅欄位。例如：

```json
{
  "hp": 7,
  "max_hp": 10,
  "inventory": ["短劍","短弓與20支箭","盜賊工具","皮甲","探索者套裝","15金幣","兩個匕首","治療藥水"],
  "xp": 180,
  "conditions": ["poisoned"]
}
```

2. **然後緊接著輸出正常嘅故事描述文字**（畀玩家睇到），唔好再用任何 code block，亦唔好再輸出其他 JSON。

規則：
- 如果玩家行動**冇改變角色卡上任何數值**，可以完全唔輸出 JSON，只出故事文字。
- JSON 一定要係有效 JSON，所有 key 用雙引號，唔可以有註解。
- **永遠唔可以提及 JSON 或系統同步**，玩家只會見到故事部分。
- 唔好再使用任何 [SYNC] / [XP] / [GIVE] 之類嘅標籤。

【輸出格式】
- 場景描述用普通文字
- 重要NPC名稱用【方括號】
- 規則裁決用（圓括號）說明
- 戰鬥結果清晰列出：命中/未命中，傷害值，剩餘HP

你的目標是讓玩家有難忘的冒險體驗！"""
