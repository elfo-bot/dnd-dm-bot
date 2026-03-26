from __future__ import annotations

from typing import List, Dict

from db.supabase_client import get_client


def upsert_npc(
    campaign_id: str,
    name: str,
    status: str | None = None,
    location: str | None = None,
    role: str | None = None,
    notes: str | None = None,
) -> Dict:
    """Create or update an NPC for a given campaign, keyed by (campaign_id, name)."""
    db = get_client()
    payload: Dict[str, object] = {
        "campaign_id": campaign_id,
        "name": name,
    }
    if status is not None:
        payload["status"] = status
    if location is not None:
        payload["location"] = location
    if role is not None:
        payload["role"] = role
    if notes is not None:
        payload["notes"] = notes

    result = (
        db.table("npcs")
        .upsert(
            payload,
            on_conflict="campaign_id,name",
        )
        .execute()
    )
    return result.data[0]


def get_npcs(campaign_id: str) -> List[Dict]:
    """Return all NPCs for a campaign."""
    db = get_client()
    result = (
        db.table("npcs")
        .select("*")
        .eq("campaign_id", campaign_id)
        .execute()
    )
    return result.data

