from __future__ import annotations

from typing import Optional, List, Dict

from db.supabase_client import get_client


def get_episodes(campaign_id: str) -> List[Dict]:
    db = get_client()
    result = (
        db.table("campaign_episodes")
        .select("*")
        .eq("campaign_id", campaign_id)
        .order("episode_number", desc=False)
        .execute()
    )
    return result.data


def get_active_episode(campaign_id: str) -> Optional[Dict]:
    db = get_client()
    result = (
        db.table("campaign_episodes")
        .select("*")
        .eq("campaign_id", campaign_id)
        .eq("status", "active")
        .order("episode_number", desc=False)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def upsert_episode(
    campaign_id: str,
    episode_number: int,
    initiating_event: str | None = None,
    goal: str | None = None,
    ending: str | None = None,
    status: str | None = None,
    notes: str | None = None,
) -> Dict:
    db = get_client()
    payload: Dict[str, object] = {
        "campaign_id": campaign_id,
        "episode_number": episode_number,
    }
    if initiating_event is not None:
        payload["initiating_event"] = initiating_event
    if goal is not None:
        payload["goal"] = goal
    if ending is not None:
        payload["ending"] = ending
    if status is not None:
        payload["status"] = status
    if notes is not None:
        payload["notes"] = notes

    result = (
        db.table("campaign_episodes")
        .upsert(payload, on_conflict="campaign_id,episode_number")
        .execute()
    )
    return result.data[0]


def complete_episode(campaign_id: str, episode_number: int) -> Optional[Dict]:
    db = get_client()
    result = (
        db.table("campaign_episodes")
        .update({"status": "completed"})
        .eq("campaign_id", campaign_id)
        .eq("episode_number", episode_number)
        .execute()
    )
    return result.data[0] if result.data else None


def activate_next_episode(campaign_id: str, after_episode_number: int) -> Optional[Dict]:
    """Set the next episode to active (if exists). Returns the activated episode."""
    db = get_client()
    next_ep_res = (
        db.table("campaign_episodes")
        .select("*")
        .eq("campaign_id", campaign_id)
        .eq("episode_number", after_episode_number + 1)
        .limit(1)
        .execute()
    )
    if not next_ep_res.data:
        return None

    ep = next_ep_res.data[0]
    upd = (
        db.table("campaign_episodes")
        .update({"status": "active"})
        .eq("id", ep["id"])
        .execute()
    )
    return upd.data[0] if upd.data else ep

