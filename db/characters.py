from __future__ import annotations
from typing import Optional
from db.supabase_client import get_client

# XP thresholds per level (index = level, level 0 unused)
XP_THRESHOLDS = [0, 0, 300, 900, 2700, 6500, 14000, 23000, 34000,
                 48000, 64000, 85000, 100000, 120000, 140000,
                 165000, 195000, 225000, 265000, 305000, 355000]

# Proficiency bonus by level
PROF_BONUS = [0, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6]


def link_character_to_campaign(character_id: str, campaign_id: str, active: bool = True) -> dict:
    """Link an existing character to a campaign via characters_campaign table.

    One character row in `characters` can participate in multiple campaigns.
    """
    db = get_client()
    result = (
        db.table("characters_campaign")
        .upsert(
            {
                "character_id": character_id,
                "campaign_id": campaign_id,
                "active": active,
            },
            on_conflict="character_id,campaign_id",
        )
        .execute()
    )
    return result.data[0]


def create_character(campaign_id: str, user_id: int, username: str, sheet: dict) -> dict:
    """Create a character row from a generated sheet.

    Normalises能力值 key 為小寫 (str/dex/con/int/wis/cha)，
    以便整個系統一致地計算修正值，同時仍能向後相容舊資料。
    """
    db = get_client()
    raw_stats = sheet.get("stats", {})
    # 支援 LLM 可能輸出 "STR"/"Str" 等情況
    normalised_stats = {k.lower(): v for k, v in raw_stats.items()}

    result = db.table("characters").insert({
        "campaign_id": campaign_id,
        "user_id": str(user_id),
        "username": username,
        "name": sheet["name"],
        "class": sheet["class"],
        "race": sheet["race"],
        "background": sheet.get("background", ""),
        "level": sheet.get("level", 1),
        "xp": sheet.get("xp", 0),
        "hp": sheet["hp"],
        "max_hp": sheet.get("max_hp", sheet["hp"]),
        "stats": normalised_stats,
        "saving_throws": sheet.get("saving_throws", {}),
        "skills": sheet.get("skills", {}),
        "inventory": sheet.get("inventory", []),
        "spells": sheet.get("spells", {}),
        "spell_slots": sheet.get("spell_slots", {}),
        "conditions": [],
        "emoji": sheet.get("emoji", "🧙"),
        "proficiency_bonus": sheet.get("proficiency_bonus", 2),
        "armor_class": sheet.get("armor_class", 10),
        "speed": sheet.get("speed", 30),
        "personality": sheet.get("personality", ""),
        "subclass": sheet.get("subclass", ""),
    }).execute()
    char = result.data[0]
    # 建立對應嘅 campaign 關聯，方便同一角色參加多個戰役
    link_character_to_campaign(char["id"], campaign_id, active=True)
    return char


def get_characters(campaign_id: str) -> list[dict]:
    db = get_client()
    # 先喺關聯表揾出喺呢個戰役入面嘅角色 ID
    link_result = (
        db.table("characters_campaign")
        .select("character_id")
        .eq("campaign_id", campaign_id)
        .eq("active", True)
        .execute()
    )
    char_ids = [row["character_id"] for row in (link_result.data or [])]
    if not char_ids:
        return []

    result = (
        db.table("characters")
        .select("*")
        .in_("id", char_ids)
        .eq("active", True)
        .execute()
    )
    return result.data


def get_character_by_user(campaign_id: str, user_id: int) -> Optional[dict]:
    db = get_client()
    # 先揾出呢個戰役入面所有角色 ID
    link_result = (
        db.table("characters_campaign")
        .select("character_id")
        .eq("campaign_id", campaign_id)
        .eq("active", True)
        .execute()
    )
    char_ids = [row["character_id"] for row in (link_result.data or [])]
    if not char_ids:
        return None

    result = (
        db.table("characters")
        .select("*")
        .in_("id", char_ids)
        .eq("user_id", str(user_id))
        .eq("active", True)
        .execute()
    )
    return result.data[0] if result.data else None


def get_character_by_id(char_id: str) -> Optional[dict]:
    db = get_client()
    result = (
        db.table("characters")
        .select("*")
        .eq("id", char_id)
        .execute()
    )
    return result.data[0] if result.data else None


def update_character(char_id: str, updates: dict) -> dict:
    db = get_client()
    result = db.table("characters").update(updates).eq("id", char_id).execute()
    return result.data[0]


def update_hp(char_id: str, new_hp: int) -> dict:
    return update_character(char_id, {"hp": new_hp})


def add_xp(char_id: str, amount: int) -> tuple[dict, bool]:
    """Add XP to a character. Returns (updated_char, leveled_up).

    If the new XP total meets or exceeds the next level threshold,
    increments level and updates proficiency_bonus automatically.
    The caller is responsible for triggering the level-up UI flow.
    """
    char = get_character_by_id(char_id)
    if not char:
        raise ValueError(f"Character {char_id} not found")

    current_xp = char.get("xp", 0)
    current_level = char.get("level", 1)
    new_xp = current_xp + amount

    leveled_up = False
    new_level = current_level

    # Check if XP crosses the next level threshold (cap at 20)
    if current_level < 20:
        next_threshold = XP_THRESHOLDS[current_level + 1]
        if new_xp >= next_threshold:
            new_level = current_level + 1
            leveled_up = True

    updates: dict = {"xp": new_xp}
    if leveled_up:
        updates["level"] = new_level
        updates["proficiency_bonus"] = PROF_BONUS[new_level]

    updated_char = update_character(char_id, updates)
    return updated_char, leveled_up


def add_condition(char_id: str, condition: str, current_conditions: list) -> dict:
    if condition not in current_conditions:
        current_conditions.append(condition)
    return update_character(char_id, {"conditions": current_conditions})


def remove_condition(char_id: str, condition: str, current_conditions: list) -> dict:
    updated = [c for c in current_conditions if c != condition]
    return update_character(char_id, {"conditions": updated})


def add_to_inventory(char_id: str, item: str, current_inventory: list) -> dict:
    current_inventory.append(item)
    return update_character(char_id, {"inventory": current_inventory})


def remove_from_inventory(char_id: str, item: str, current_inventory: list) -> dict:
    updated = [i for i in current_inventory if i != item]
    return update_character(char_id, {"inventory": updated})
