-- =======================================================
-- DnD AI DM Bot - Supabase Schema
-- Run this in your Supabase SQL editor
-- =======================================================

-- Enable UUID generation
create extension if not exists "pgcrypto";

-- =======================================================
-- 1. CAMPAIGNS
-- =======================================================
create table if not exists campaigns (
    id                  uuid primary key default gen_random_uuid(),
    chat_id             text not null,
    module              text not null default 'lmop',
    status              text not null default 'character_creation',
                        -- character_creation | active | paused | ended
    current_location    text not null default 'phandalin_outskirts',
    act                 int not null default 1,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index if not exists idx_campaigns_chat_id on campaigns(chat_id);
create index if not exists idx_campaigns_status  on campaigns(status);

-- =======================================================
-- 2. CHARACTERS
-- =======================================================
create table if not exists characters (
    id                  uuid primary key default gen_random_uuid(),
    campaign_id         uuid not null references campaigns(id) on delete cascade,
    user_id             text not null,
    username            text not null,
    name                text not null,
    class               text not null,
    race                text not null,
    background          text not null default '',
    level               int not null default 1,
    hp                  int not null default 10,
    max_hp              int not null default 10,
    stats               jsonb not null default '{}',
    saving_throws       jsonb not null default '{}',
    skills              jsonb not null default '{}',
    inventory           jsonb not null default '[]',
    spells              jsonb not null default '{}',
    spell_slots         jsonb not null default '{}',
    conditions          jsonb not null default '[]',
    emoji               text not null default '🧙',
    proficiency_bonus   int not null default 2,
    armor_class         int not null default 10,
    speed               int not null default 30,
    personality         text not null default '',
    xp                  int not null default 0,
    subclass            text not null default '',
    active              boolean not null default true,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index if not exists idx_characters_campaign on characters(campaign_id);
create index if not exists idx_characters_user     on characters(user_id);
create unique index if not exists idx_characters_campaign_user
    on characters(campaign_id, user_id) where active = true;

-- =======================================================
-- 3. EVENTS (session log)
-- =======================================================
create table if not exists events (
    id              uuid primary key default gen_random_uuid(),
    campaign_id     uuid not null references campaigns(id) on delete cascade,
    type            text not null,
                    -- narrative | combat | skill_check | dialogue | loot | death | level_up
    description     text not null,
    metadata        jsonb not null default '{}',
    created_at      timestamptz not null default now()
);

create index if not exists idx_events_campaign on events(campaign_id);
create index if not exists idx_events_type     on events(type);

-- =======================================================
-- 4. COMBAT ENCOUNTERS
-- =======================================================
create table if not exists combat_encounters (
    id              uuid primary key default gen_random_uuid(),
    campaign_id     uuid not null references campaigns(id) on delete cascade,
    location        text not null,
    status          text not null default 'active',
                    -- active | victory | defeat | fled
    current_round   int not null default 1,
    grid_config     jsonb,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists idx_combat_campaign on combat_encounters(campaign_id);
create index if not exists idx_combat_status   on combat_encounters(status);

-- =======================================================
-- 5. COMBAT ENTITIES (PCs + monsters in encounter)
-- =======================================================
create table if not exists combat_entities (
    id                  uuid primary key default gen_random_uuid(),
    encounter_id        uuid not null references combat_encounters(id) on delete cascade,
    entity_type         text not null,
                        -- 'pc' | 'monster'
    character_id        uuid references characters(id) on delete cascade,
                        -- null if monster
    monster_name        text,
    monster_stats       jsonb,
    hp                  int not null,
    max_hp              int not null,
    armor_class         int not null,
    initiative          int not null default 0,
    turn_order          int,
    position            text,
    conditions          jsonb not null default '[]',
    has_acted           boolean not null default false,
    is_active           boolean not null default true,
    created_at          timestamptz not null default now()
);

create index if not exists idx_combat_entities_encounter  on combat_entities(encounter_id);
create index if not exists idx_combat_entities_character  on combat_entities(character_id);
create index if not exists idx_combat_entities_turn_order on combat_entities(encounter_id, turn_order);

-- =======================================================
-- 6. QUESTS
-- =======================================================
create table if not exists quests (
    id              uuid primary key default gen_random_uuid(),
    campaign_id     uuid not null references campaigns(id) on delete cascade,
    title           text not null,
    description     text not null,
    quest_giver     text,
    status          text not null default 'active',
                    -- active | completed | failed | abandoned
    reward          text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists idx_quests_campaign on quests(campaign_id);
create index if not exists idx_quests_status   on quests(status);

-- =======================================================
-- 7. LOCATIONS
-- =======================================================
create table if not exists locations (
    id              text primary key,
    module          text not null,
    name            text not null,
    description     text not null,
    npcs            jsonb not null default '[]',
    exits           jsonb not null default '[]',
    points_of_interest jsonb not null default '[]'
);

create index if not exists idx_locations_module on locations(module);

-- ===============================================
-- MIGRATION: Add xp and subclass to characters
-- Run if upgrading an existing database
-- ===============================================
alter table characters add column if not exists xp int not null default 0;
alter table characters add column if not exists subclass text not null default '';
