import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS conversation_turns (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
        merged_text TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, telegram_message_id INTEGER NOT NULL,
        user_id INTEGER, sender TEXT NOT NULL CHECK(sender IN ('user','assistant')),
        type TEXT NOT NULL, text TEXT, sticker_file_id TEXT, reply_to INTEGER,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP, conversation_turn_id INTEGER REFERENCES conversation_turns(id),
        UNIQUE(chat_id, telegram_message_id)
    );
    CREATE INDEX IF NOT EXISTS idx_messages_user_id_id ON messages(user_id, id);
    CREATE INDEX IF NOT EXISTS idx_messages_turn ON messages(conversation_turn_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_updates (
        telegram_update_id INTEGER PRIMARY KEY,
        received_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS developed_personality (
        id INTEGER PRIMARY KEY,
        category TEXT NOT NULL,
        subject TEXT NOT NULL,
        value TEXT NOT NULL,
        strength REAL NOT NULL CHECK(strength >= -1 AND strength <= 1),
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        source TEXT NOT NULL,
        source_turn_id INTEGER REFERENCES conversation_turns(id),
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(category, subject, value)
    );
    CREATE INDEX IF NOT EXISTS idx_personality_subject ON developed_personality(category, subject);
    CREATE TABLE IF NOT EXISTS emotional_state (
        id INTEGER PRIMARY KEY CHECK(id=1),
        mood TEXT NOT NULL DEFAULT 'normal', energy REAL NOT NULL DEFAULT .7,
        social_energy REAL NOT NULL DEFAULT .7, offense_level REAL NOT NULL DEFAULT 0,
        conversation_interest REAL NOT NULL DEFAULT .7, availability TEXT NOT NULL DEFAULT 'available',
        last_updated TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS scheduled_responses (
        id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        respond_after TEXT NOT NULL,
        generation_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','processing','completed','cancelled')),
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_responses(status, respond_after);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_lifecycle (
        chat_id INTEGER PRIMARY KEY,
        conversation_status TEXT NOT NULL DEFAULT 'active',
        last_user_message_at TEXT,
        last_bot_message_at TEXT,
        last_meaningful_interaction_at TEXT,
        expects_reply INTEGER NOT NULL DEFAULT 0,
        followup_importance REAL NOT NULL DEFAULT 0 CHECK(followup_importance >= 0 AND followup_importance <= 1),
        followup_reason TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS initiative_history (
        id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, reason TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_initiative_chat_time ON initiative_history(chat_id, created_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS sticker_sets (
        name TEXT PRIMARY KEY, title TEXT, imported_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS stickers (
        id INTEGER PRIMARY KEY, file_id TEXT UNIQUE, file_unique_id TEXT UNIQUE,
        set_name TEXT, emoji TEXT, description TEXT DEFAULT '', tags TEXT DEFAULT '[]',
        times_seen INTEGER NOT NULL DEFAULT 0, times_sent INTEGER NOT NULL DEFAULT 0
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        telegram_file_id TEXT NOT NULL,
        telegram_file_unique_id TEXT,
        media_type TEXT NOT NULL CHECK(media_type IN ('photo','sticker','animation')),
        mime_type TEXT,
        local_cache_path TEXT,
        byte_size INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(chat_id, telegram_file_id)
    );
    CREATE INDEX IF NOT EXISTS idx_media_unique ON media(telegram_file_unique_id);
    CREATE TABLE IF NOT EXISTS message_media (
        chat_id INTEGER NOT NULL, telegram_message_id INTEGER NOT NULL,
        media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
        PRIMARY KEY(chat_id, telegram_message_id, media_id)
    );
    CREATE TABLE IF NOT EXISTS sticker_semantics (
        sticker_id INTEGER PRIMARY KEY REFERENCES stickers(id) ON DELETE CASCADE,
        visual_description TEXT NOT NULL,
        animation_description TEXT,
        characters_json TEXT NOT NULL,
        emotions_json TEXT NOT NULL,
        meanings_json TEXT NOT NULL,
        usage_json TEXT NOT NULL,
        intensity REAL NOT NULL CHECK(intensity >= 0 AND intensity <= 1),
        searchable_text TEXT NOT NULL,
        analyzer_model TEXT,
        analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP,
        analysis_version TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sticker_analysis_jobs (
        id INTEGER PRIMARY KEY, sticker_id INTEGER NOT NULL REFERENCES stickers(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('pending','processing','done','failed')),
        priority INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(sticker_id)
    );
    CREATE INDEX IF NOT EXISTS idx_sticker_jobs_due ON sticker_analysis_jobs(status, priority DESC, id);
    CREATE TABLE IF NOT EXISTS sticker_embeddings (
        sticker_id INTEGER PRIMARY KEY REFERENCES stickers(id) ON DELETE CASCADE,
        embedding_json TEXT NOT NULL, embedding_model TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sticker_usage (
        id INTEGER PRIMARY KEY, sticker_id INTEGER NOT NULL REFERENCES stickers(id) ON DELETE CASCADE,
        chat_id INTEGER NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing')),
        context TEXT, used_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_sticker_usage_recent ON sticker_usage(chat_id, sticker_id, used_at DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS media_descriptions (
        media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
        description TEXT NOT NULL,
        model TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS media_analysis_jobs (
        id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('pending','processing','done','failed')),
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(media_id)
    );
    CREATE INDEX IF NOT EXISTS idx_media_jobs_status ON media_analysis_jobs(status,id);
    """,
    """
    -- Current reaction state.  A reaction is not a message/turn: it is stored
    -- separately so it can influence later context without waking the bot.
    CREATE TABLE IF NOT EXISTS message_reactions (
        chat_id INTEGER NOT NULL,
        telegram_message_id INTEGER NOT NULL,
        actor TEXT NOT NULL CHECK(actor IN ('user','assistant')),
        actor_user_id INTEGER NOT NULL DEFAULT 0,
        emoji TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(chat_id, telegram_message_id, actor, actor_user_id, emoji)
    );
    CREATE INDEX IF NOT EXISTS idx_reactions_recent ON message_reactions(actor, actor_user_id, active, updated_at DESC);
    """,
    """
    -- Cache provenance is independent from Telegram's declared sticker flags:
    -- the downloaded payload is the final authority for its media type.
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_presence (
        chat_id INTEGER PRIMARY KEY, local_day TEXT NOT NULL, timezone TEXT NOT NULL,
        sleep_until TEXT, availability TEXT NOT NULL DEFAULT 'available',
        current_event_id INTEGER, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS daily_events (
        id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, title TEXT NOT NULL,
        availability TEXT NOT NULL CHECK(availability IN ('available','busy','away','sleep')),
        starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, mentionable INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_daily_events_active ON daily_events(chat_id, starts_at, ends_at);
    CREATE TABLE IF NOT EXISTS image_generation_usage (
        id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, provider TEXT NOT NULL,
        prompt TEXT NOT NULL, status TEXT NOT NULL, cost REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS visual_state (chat_id INTEGER PRIMARY KEY, location TEXT NOT NULL, activity TEXT NOT NULL, clothing_context TEXT NOT NULL, period_key TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS generated_images (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, telegram_message_id INTEGER, kind TEXT NOT NULL, scene TEXT NOT NULL, location TEXT, activity TEXT, clothing_context TEXT, provider TEXT, model TEXT, status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS idx_generated_images_recent ON generated_images(chat_id,created_at DESC);
    """,
    """
    -- Long-term facts are deliberately scoped to one Telegram chat.  The
    -- same Telegram user can be present in unrelated private/group chats.
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        chat_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        importance REAL NOT NULL CHECK(importance >= 0 AND importance <= 1),
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        tags TEXT NOT NULL DEFAULT '[]',
        source_turn_id INTEGER REFERENCES conversation_turns(id),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_used TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_memories_chat_user_recent ON memories(chat_id, user_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_memories_chat_user_importance ON memories(chat_id, user_id, importance DESC, confidence DESC);
    """,
    """
    -- Persist the real event that justified a delayed reply.  It is optional:
    -- routine college/travel delay has no post-hoc explanation attached.
    ALTER TABLE scheduled_responses ADD COLUMN delay_event_id INTEGER REFERENCES daily_events(id);
    CREATE INDEX IF NOT EXISTS idx_scheduled_delay_event ON scheduled_responses(delay_event_id);
    """,
    """
    -- Internal read state only. Telegram Bot API read receipts are not
    -- touched; a future MTProto adapter can subscribe to completed jobs.
    ALTER TABLE messages ADD COLUMN internally_read_at TEXT;
    -- All rows predating this mechanism are historical conversation, not a
    -- newly arrived unread batch. Without this backfill the first live read
    -- after deployment would fold the entire chat into one turn.
    UPDATE messages SET internally_read_at=timestamp
    WHERE sender='user' AND internally_read_at IS NULL;
    CREATE TABLE IF NOT EXISTS scheduled_reads (
        id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        boundary_message_id INTEGER NOT NULL,
        read_after TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','processing','completed','cancelled')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_scheduled_reads_due ON scheduled_reads(status, read_after);
    CREATE INDEX IF NOT EXISTS idx_scheduled_reads_chat_status ON scheduled_reads(chat_id, status, id DESC);
    """,
    """
    -- Global character autobiography. It deliberately has source-chat
    -- provenance but is not scoped to a single Telegram conversation.
    CREATE TABLE IF NOT EXISTS anya_life_events (
        id INTEGER PRIMARY KEY,
        occurred_at TEXT,
        local_day TEXT,
        kind TEXT NOT NULL DEFAULT 'ordinary',
        summary TEXT NOT NULL,
        details TEXT,
        location_context TEXT,
        participants_json TEXT NOT NULL DEFAULT '[]',
        source_chat_id INTEGER,
        source_turn_id INTEGER REFERENCES conversation_turns(id),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_anya_life_events_recent ON anya_life_events(occurred_at DESC, id DESC);
    CREATE INDEX IF NOT EXISTS idx_anya_life_events_day ON anya_life_events(local_day, id DESC);
    """,
    """
    -- Initiative v2 records whether a message was a follow-up or a genuine
    -- spontaneous first message, and its broad conversational form.
    ALTER TABLE initiative_history ADD COLUMN basis TEXT;
    ALTER TABLE initiative_history ADD COLUMN kind TEXT;
    CREATE INDEX IF NOT EXISTS idx_initiative_chat_recent_kind ON initiative_history(chat_id, created_at DESC, id DESC);
    """,
    """
    -- At most one bedtime farewell per chat and local calendar day.  It is
    -- deliberately independent from initiative history so a natural reply
    -- can complete the ritual without a separate initiative.
    CREATE TABLE IF NOT EXISTS bedtime_history (
        chat_id INTEGER NOT NULL,
        local_day TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source IN ('conversation','initiative')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(chat_id, local_day)
    );
    """,
    """
    -- A direct request may postpone exactly one upcoming sleep episode.
    -- The date is the local date on which that episode normally begins.
    CREATE TABLE IF NOT EXISTS bedtime_overrides (
        chat_id INTEGER NOT NULL,
        sleep_start_date TEXT NOT NULL,
        delay_minutes INTEGER NOT NULL CHECK(delay_minutes BETWEEN 15 AND 240),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(chat_id, sleep_start_date)
    );
    """,
    """
    -- Emotional continuity is per Telegram chat; the legacy singleton remains
    -- untouched for migration safety and will be retired by application wiring.
    CREATE TABLE IF NOT EXISTS emotional_states (
        chat_id INTEGER PRIMARY KEY,
        warmth REAL NOT NULL CHECK(warmth >= 0 AND warmth <= 1),
        trust REAL NOT NULL CHECK(trust >= 0 AND trust <= 1),
        joy REAL NOT NULL CHECK(joy >= 0 AND joy <= 1),
        sadness REAL NOT NULL CHECK(sadness >= 0 AND sadness <= 1),
        irritation REAL NOT NULL CHECK(irritation >= 0 AND irritation <= 1),
        hurt REAL NOT NULL CHECK(hurt >= 0 AND hurt <= 1),
        anxiety REAL NOT NULL CHECK(anxiety >= 0 AND anxiety <= 1),
        fatigue REAL NOT NULL CHECK(fatigue >= 0 AND fatigue <= 1),
        curiosity REAL NOT NULL CHECK(curiosity >= 0 AND curiosity <= 1),
        social_need REAL NOT NULL CHECK(social_need >= 0 AND social_need <= 1),
        last_interaction_at TEXT NOT NULL,
        last_advanced_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS episodic_memories (
      id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, kind TEXT NOT NULL, summary TEXT NOT NULL, reflection TEXT NOT NULL DEFAULT '', importance REAL NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL DEFAULT 'active', unresolved INTEGER NOT NULL DEFAULT 0, emotion_snapshot TEXT NOT NULL, source_generation_id TEXT, source_message_id INTEGER, superseded_by INTEGER, resolved_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_episodic_chat_status ON episodic_memories(chat_id,status,unresolved,updated_at DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS affective_states (
      chat_id INTEGER PRIMARY KEY, regulators_json TEXT NOT NULL,
      last_advanced_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS affective_profiles (
      chat_id INTEGER PRIMARY KEY, emotions_json TEXT NOT NULL,
      last_updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS relationship_bonds (
      chat_id INTEGER PRIMARY KEY,
      bond_strength REAL NOT NULL CHECK(bond_strength BETWEEN 0 AND 1),
      relational_trust REAL NOT NULL CHECK(relational_trust BETWEEN 0 AND 1),
      familiarity REAL NOT NULL CHECK(familiarity BETWEEN 0 AND 1),
      care_investment REAL NOT NULL CHECK(care_investment BETWEEN 0 AND 1),
      rupture_load REAL NOT NULL CHECK(rupture_load BETWEEN 0 AND 1),
      relationship_security REAL NOT NULL CHECK(relationship_security BETWEEN 0 AND 1),
      love_strength REAL NOT NULL CHECK(love_strength BETWEEN 0 AND 1),
      meaningful_interactions INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    -- Local observability only. Prompt text here is not conversation memory
    -- and is never read by ContextBuilder.
    CREATE TABLE IF NOT EXISTS llm_generations (
      id INTEGER PRIMARY KEY,
      chat_id INTEGER, turn_id INTEGER REFERENCES conversation_turns(id),
      generation_id TEXT, provider_generation_id TEXT,
      kind TEXT NOT NULL, model TEXT NOT NULL, session_id TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, latency_ms INTEGER NOT NULL,
      prompt_tokens INTEGER, cached_tokens INTEGER, cache_write_tokens INTEGER,
      completion_tokens INTEGER, total_tokens INTEGER, cost REAL, cache_discount REAL,
      estimated_input_tokens INTEGER, components_json TEXT NOT NULL DEFAULT '{}',
      system_prompt TEXT, dynamic_context TEXT, user_turn TEXT, response_schema_json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_llm_generations_chat_created ON llm_generations(chat_id,created_at DESC,id DESC);
    CREATE INDEX IF NOT EXISTS idx_llm_generations_generation ON llm_generations(generation_id);
    CREATE TABLE IF NOT EXISTS turn_state_observations (
      id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
      turn_id INTEGER REFERENCES conversation_turns(id), generation_id TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, user_message TEXT NOT NULL,
      before_json TEXT NOT NULL, after_json TEXT NOT NULL, delta_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_turn_state_chat_created ON turn_state_observations(chat_id,created_at DESC,id DESC);
    CREATE INDEX IF NOT EXISTS idx_turn_state_generation ON turn_state_observations(generation_id);
    """,
    """
    -- Anya 2.0 shared-state foundation. Old chat-scoped affect/life tables
    -- intentionally remain intact while the runtime migrates in stages.
    CREATE TABLE IF NOT EXISTS anya_world_state (
      id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL DEFAULT 1,
      mind_json TEXT NOT NULL, life_json TEXT NOT NULL, needs_json TEXT NOT NULL DEFAULT '{}',
      intentions_json TEXT NOT NULL DEFAULT '[]', migration_source_chat_id INTEGER,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS anya_contacts (
      telegram_user_id INTEGER PRIMARY KEY, display_name TEXT, username TEXT, first_chat_id INTEGER,
      relationship_json TEXT NOT NULL, affect_json TEXT NOT NULL, boundaries_json TEXT NOT NULL DEFAULT '{}',
      policy_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','limited','blocked')),
      migrated_from_chat_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS anya_conversation_contacts (
      chat_id INTEGER NOT NULL, telegram_user_id INTEGER NOT NULL REFERENCES anya_contacts(telegram_user_id),
      context_kind TEXT NOT NULL DEFAULT 'private' CHECK(context_kind IN ('private','group','channel','unknown')),
      first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(chat_id,telegram_user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_anya_conversation_contacts_user ON anya_conversation_contacts(telegram_user_id,last_seen_at DESC);
    CREATE TABLE IF NOT EXISTS anya_2_migrations (
      name TEXT PRIMARY KEY, details_json TEXT NOT NULL DEFAULT '{}', applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    -- Append-only causal journal.  State tables stay materialized views during
    -- migration; this table lets later processors recover and explain why a
    -- transition happened without replaying user text as a command.
    CREATE TABLE IF NOT EXISTS anya_events (
      event_id TEXT PRIMARY KEY,
      event_type TEXT NOT NULL,
      occurred_at TEXT NOT NULL,
      source TEXT NOT NULL,
      actor_kind TEXT NOT NULL,
      actor_id TEXT,
      target_kind TEXT,
      target_id TEXT,
      scope TEXT NOT NULL,
      payload_json TEXT NOT NULL DEFAULT '{}',
      correlation_id TEXT,
      causation_id TEXT,
      idempotency_key TEXT NOT NULL UNIQUE,
      schema_version INTEGER NOT NULL DEFAULT 1,
      status TEXT NOT NULL DEFAULT 'recorded' CHECK(status IN ('recorded','applied','failed')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_anya_events_occurred ON anya_events(occurred_at,event_id);
    CREATE INDEX IF NOT EXISTS idx_anya_events_actor ON anya_events(actor_kind,actor_id,occurred_at DESC);
    CREATE INDEX IF NOT EXISTS idx_anya_events_causation ON anya_events(causation_id,occurred_at);
    CREATE TABLE IF NOT EXISTS anya_event_transitions (
      id INTEGER PRIMARY KEY,
      event_id TEXT NOT NULL REFERENCES anya_events(event_id),
      transition_kind TEXT NOT NULL,
      before_json TEXT NOT NULL DEFAULT '{}',
      after_json TEXT NOT NULL DEFAULT '{}',
      result_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(event_id,transition_kind)
    );
    """,
    """
    -- Delayed work is owned by the transport that created it.  A user-account
    -- runtime must never claim a Bot API job from the same durable database.
    ALTER TABLE scheduled_responses ADD COLUMN transport TEXT NOT NULL DEFAULT 'bot_api';
    ALTER TABLE scheduled_reads ADD COLUMN transport TEXT NOT NULL DEFAULT 'bot_api';
    CREATE INDEX IF NOT EXISTS idx_scheduled_responses_transport_due ON scheduled_responses(transport,status,respond_after);
    CREATE INDEX IF NOT EXISTS idx_scheduled_reads_transport_due ON scheduled_reads(transport,status,read_after);
    -- MTProto documents are reusable only through their own account session;
    -- Bot API file IDs are intentionally never treated as Telethon files.
    CREATE TABLE IF NOT EXISTS mtproto_sticker_documents (
      sticker_id INTEGER PRIMARY KEY REFERENCES stickers(id) ON DELETE CASCADE,
      document_id INTEGER NOT NULL,
      access_hash INTEGER NOT NULL,
      file_reference BLOB NOT NULL,
      mime_type TEXT,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    -- Transport delivery state is separate from `messages`: a local message
    -- row proves only local persistence, not that Telegram acknowledged a
    -- read receipt or that another person read an outgoing message.
    CREATE TABLE IF NOT EXISTS telegram_message_states (
      id INTEGER PRIMARY KEY,
      transport TEXT NOT NULL,
      account_id INTEGER NOT NULL,
      peer_type TEXT NOT NULL,
      peer_id INTEGER NOT NULL,
      message_id INTEGER NOT NULL,
      direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing')),
      local_chat_id INTEGER NOT NULL,
      status TEXT NOT NULL,
      read_watermark INTEGER,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(transport,account_id,peer_type,peer_id,message_id,direction)
    );
    CREATE INDEX IF NOT EXISTS idx_telegram_message_states_chat ON telegram_message_states(local_chat_id,direction,message_id);
    CREATE INDEX IF NOT EXISTS idx_telegram_message_states_peer ON telegram_message_states(transport,account_id,peer_type,peer_id,direction,message_id);
    CREATE TABLE IF NOT EXISTS telegram_message_state_events (
      id INTEGER PRIMARY KEY,
      state_id INTEGER NOT NULL REFERENCES telegram_message_states(id) ON DELETE CASCADE,
      status TEXT NOT NULL,
      source TEXT NOT NULL,
      read_watermark INTEGER NOT NULL DEFAULT -1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(state_id,status,source,read_watermark)
    );
    CREATE INDEX IF NOT EXISTS idx_telegram_message_state_events_state ON telegram_message_state_events(state_id,id);
    """,
    """
    -- An external Telegram action is at-most-once per transport/generation
    -- slot. It is claimed before a side effect so a process restart cannot
    -- enqueue the same generated action into a fresh in-memory ActionQueue.
    CREATE TABLE IF NOT EXISTS action_dispatches (
      id INTEGER PRIMARY KEY,
      transport TEXT NOT NULL,
      generation_id TEXT NOT NULL,
      action_index INTEGER NOT NULL,
      chat_id INTEGER NOT NULL,
      action_type TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('claimed','completed','failed')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(transport,generation_id,action_index)
    );
    CREATE INDEX IF NOT EXISTS idx_action_dispatches_chat ON action_dispatches(transport,chat_id,created_at);
    """,
    """
    -- A durable dispatch has three materially different pre-terminal states:
    -- `execution_state=not_started` is safe to reclaim; `executing` may have
    -- reached Telegram and must never be replayed after a crash. `status`
    -- stays backward-compatible with the original SQLite CHECK constraint.
    -- `recovery_status=unknown` makes that latter case visible for manual or
    -- future provider-specific reconciliation without weakening at-most-once.
    ALTER TABLE action_dispatches ADD COLUMN recovery_status TEXT NOT NULL DEFAULT 'none'
      CHECK(recovery_status IN ('none','unknown'));
    ALTER TABLE action_dispatches ADD COLUMN execution_state TEXT NOT NULL DEFAULT 'not_started'
      CHECK(execution_state IN ('not_started','executing'));
    ALTER TABLE action_dispatches ADD COLUMN started_at TEXT;
    ALTER TABLE action_dispatches ADD COLUMN detail TEXT;
    CREATE INDEX IF NOT EXISTS idx_action_dispatches_recovery
      ON action_dispatches(transport,status,recovery_status,updated_at);
    """,
    """
    -- Read acknowledgement retries are intentionally observable and bounded
    -- per burst. A later low-frequency retry still allows recovery after a
    -- transient Telegram watermark outage without pretending it succeeded.
    ALTER TABLE scheduled_reads ADD COLUMN read_confirmation_attempts INTEGER NOT NULL DEFAULT 0;
    """,
    """
    -- Stable Telegram identity registry. `local_chat_id` deliberately remains
    -- a compatibility bridge for existing messages/turns, not the identity
    -- itself: account, peer and optional topic are always stored separately.
    CREATE TABLE IF NOT EXISTS telegram_accounts (
      transport TEXT NOT NULL, account_id INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(transport,account_id)
    );
    CREATE TABLE IF NOT EXISTS telegram_peers (
      transport TEXT NOT NULL, account_id INTEGER NOT NULL,
      peer_type TEXT NOT NULL CHECK(peer_type IN ('user','group','supergroup','channel')),
      peer_id INTEGER NOT NULL, title TEXT, username TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(transport,account_id,peer_type,peer_id),
      FOREIGN KEY(transport,account_id) REFERENCES telegram_accounts(transport,account_id)
    );
    CREATE TABLE IF NOT EXISTS telegram_contact_identities (
      transport TEXT NOT NULL, account_id INTEGER NOT NULL, telegram_user_id INTEGER NOT NULL,
      local_contact_user_id INTEGER NOT NULL REFERENCES anya_contacts(telegram_user_id),
      username TEXT, display_name TEXT,
      first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(transport,account_id,telegram_user_id),
      FOREIGN KEY(transport,account_id) REFERENCES telegram_accounts(transport,account_id)
    );
    CREATE TABLE IF NOT EXISTS telegram_conversations (
      transport TEXT NOT NULL, account_id INTEGER NOT NULL,
      peer_type TEXT NOT NULL CHECK(peer_type IN ('user','group','supergroup','channel')),
      peer_id INTEGER NOT NULL, thread_id INTEGER NOT NULL DEFAULT 0,
      local_chat_id INTEGER NOT NULL UNIQUE,
      participation_policy TEXT NOT NULL DEFAULT 'OFF' CHECK(participation_policy IN ('OFF','OBSERVE_ONLY','MENTIONS_ONLY','PARTICIPATE')),
      consent_granted INTEGER NOT NULL DEFAULT 0 CHECK(consent_granted IN (0,1)),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(transport,account_id,peer_type,peer_id,thread_id),
      FOREIGN KEY(transport,account_id,peer_type,peer_id) REFERENCES telegram_peers(transport,account_id,peer_type,peer_id)
    );
    CREATE INDEX IF NOT EXISTS idx_telegram_contact_identities_local ON telegram_contact_identities(local_contact_user_id);
    CREATE INDEX IF NOT EXISTS idx_telegram_conversations_peer ON telegram_conversations(transport,account_id,peer_type,peer_id);
    """,
    """
    -- Outbound message mutations remain in the local history so an edited or
    -- deleted remote message cannot be accidentally targeted again.  Deleted
    -- rows are excluded from conversational context instead of being erased,
    -- preserving delivery auditability without resurfacing their text.
    ALTER TABLE messages ADD COLUMN deleted_at TEXT;
    """,
    """
    -- Consent-gated social observations. This is deliberately independent of
    -- chats, contacts and beliefs: a source can provide experience without
    -- gaining the ability to mutate Anya's personality.
    CREATE TABLE IF NOT EXISTS social_sources (
      id INTEGER PRIMARY KEY,
      platform TEXT NOT NULL,
      platform_account_id TEXT NOT NULL,
      source_id TEXT NOT NULL,
      source_type TEXT NOT NULL,
      display_name TEXT,
      usage_mode TEXT NOT NULL,
      ai_processing_allowed INTEGER NOT NULL CHECK(ai_processing_allowed IN (0,1)),
      memory_allowed INTEGER NOT NULL CHECK(memory_allowed IN (0,1)),
      retention_allowed INTEGER NOT NULL CHECK(retention_allowed IN (0,1)),
      explicit_consent INTEGER NOT NULL CHECK(explicit_consent IN (0,1)),
      consent_scope TEXT,
      permission_expires_at TEXT,
      capabilities_json TEXT NOT NULL DEFAULT '[]',
      cursor_value TEXT,
      status TEXT NOT NULL DEFAULT 'enabled' CHECK(status IN ('enabled','disabled','revoked')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(platform,platform_account_id,source_id)
    );
    CREATE TABLE IF NOT EXISTS social_events (
      id INTEGER PRIMARY KEY,
      source_row_id INTEGER NOT NULL REFERENCES social_sources(id),
      event_id TEXT NOT NULL,
      idempotency_key TEXT NOT NULL UNIQUE,
      actor_id TEXT,
      thread_id TEXT,
      event_kind TEXT NOT NULL,
      content_ref TEXT,
      content_hash TEXT,
      content_excerpt TEXT,
      media_metadata_json TEXT NOT NULL DEFAULT '{}',
      reply_to_event_id TEXT,
      reference_event_ids_json TEXT NOT NULL DEFAULT '[]',
      visibility TEXT NOT NULL,
      permissions_json TEXT NOT NULL,
      provenance_json TEXT NOT NULL,
      occurred_at TEXT NOT NULL,
      received_at TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('accepted','rejected','deleted','restored')),
      rejection_reason TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(source_row_id,event_id)
    );
    CREATE INDEX IF NOT EXISTS idx_social_events_source_time ON social_events(source_row_id,occurred_at DESC);
    CREATE INDEX IF NOT EXISTS idx_social_events_status ON social_events(status,received_at DESC);
    """,
    """
    -- Memory routing for accepted social observations. This is intentionally
    -- separate from existing private-chat `memories` and `episodic_memories`
    -- until context/privacy routing is completed in a later stage.
    CREATE TABLE IF NOT EXISTS social_memories (
      id INTEGER PRIMARY KEY,
      social_event_id INTEGER NOT NULL REFERENCES social_events(id),
      tier TEXT NOT NULL CHECK(tier IN ('working','episodic','social')),
      actor_id TEXT,
      summary TEXT,
      importance REAL NOT NULL CHECK(importance >= 0 AND importance <= 1),
      emotional_significance REAL NOT NULL CHECK(emotional_significance >= 0 AND emotional_significance <= 1),
      confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
      expires_at TEXT,
      status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','expired','deleted')),
      provenance_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(social_event_id,tier)
    );
    CREATE INDEX IF NOT EXISTS idx_social_memories_active ON social_memories(status,tier,expires_at);
    """,
    """
    -- Stage 3 keeps external evidence separate from Anya's own beliefs.
    -- No raw social statement becomes a belief merely by being persisted here.
    CREATE TABLE IF NOT EXISTS social_evidence (
      id INTEGER PRIMARY KEY,
      social_event_id INTEGER REFERENCES social_events(id),
      topic TEXT NOT NULL,
      proposition TEXT NOT NULL,
      evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('observation','external_claim','verified_fact','own_reflection')),
      reliability REAL NOT NULL CHECK(reliability >= 0 AND reliability <= 1),
      independence_key TEXT NOT NULL,
      provenance_json TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','retracted')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(social_event_id,evidence_kind,proposition)
    );
    CREATE INDEX IF NOT EXISTS idx_social_evidence_topic ON social_evidence(topic,status);
    CREATE TABLE IF NOT EXISTS social_beliefs (
      id INTEGER PRIMARY KEY,
      topic TEXT NOT NULL,
      proposition TEXT NOT NULL,
      stance TEXT NOT NULL CHECK(stance IN ('support','oppose','uncertain')),
      confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
      importance REAL NOT NULL CHECK(importance >= 0 AND importance <= 1),
      emotional_significance REAL NOT NULL CHECK(emotional_significance >= 0 AND emotional_significance <= 1),
      origin TEXT NOT NULL CHECK(origin IN ('own_reflection','curated_seed')),
      provenance_json TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded','retracted')),
      first_formed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      last_revised_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(topic,proposition)
    );
    CREATE INDEX IF NOT EXISTS idx_social_beliefs_active ON social_beliefs(status,importance,confidence);
    CREATE TABLE IF NOT EXISTS social_belief_evidence (
      belief_id INTEGER NOT NULL REFERENCES social_beliefs(id),
      evidence_id INTEGER NOT NULL REFERENCES social_evidence(id),
      relation TEXT NOT NULL CHECK(relation IN ('support','contradict')),
      weight REAL NOT NULL CHECK(weight >= 0 AND weight <= 1),
      independence_key TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(belief_id,evidence_id),
      UNIQUE(belief_id,independence_key)
    );
    CREATE TABLE IF NOT EXISTS social_belief_revisions (
      id INTEGER PRIMARY KEY,
      belief_id INTEGER NOT NULL REFERENCES social_beliefs(id),
      evidence_id INTEGER REFERENCES social_evidence(id),
      old_confidence REAL NOT NULL,
      new_confidence REAL NOT NULL,
      old_stance TEXT NOT NULL,
      new_stance TEXT NOT NULL,
      reason TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
]


@dataclass(frozen=True)
class ExecuteResult:
    lastrowid: int | None
    rowcount: int


class Database:
    def __init__(self, url: str):
        prefixes = ("sqlite:///", "sqlite+aiosqlite:///")
        prefix = next((value for value in prefixes if url.startswith(value)), None)
        if prefix is None:
            raise ValueError("DATABASE_URL must use sqlite:///path")
        self.path = url.removeprefix(prefix)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self):
        # SQLite operations are serialized by `_lock`; keeping the connection
        # on the event-loop thread avoids platform-specific thread deadlocks.
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        await self._migrate()

    async def _migrate(self):
        await self._run(lambda: (self.conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"), self.conn.commit()))
        for version, sql in enumerate(MIGRATIONS, start=1):
            exists = await self.fetchone("SELECT 1 FROM schema_migrations WHERE version=?", (version,))
            if not exists:
                def apply():
                    self.conn.executescript(sql)
                    if version == 7:
                        self._ensure_column("sticker_sets", "analysis_status", "TEXT NOT NULL DEFAULT 'pending'")
                        self._ensure_column("sticker_sets", "total_stickers", "INTEGER NOT NULL DEFAULT 0")
                        self._ensure_column("sticker_sets", "analyzed_stickers", "INTEGER NOT NULL DEFAULT 0")
                        self._ensure_column("stickers", "sticker_type", "TEXT NOT NULL DEFAULT 'static'")
                        self._ensure_column("stickers", "local_cache_path", "TEXT")
                        # SQLite cannot ADD COLUMN with CURRENT_TIMESTAMP default;
                        # new rows can leave this optional provenance field null.
                        self._ensure_column("stickers", "created_at", "TEXT")
                    if version == 10:
                        self._ensure_column("stickers", "cached_sticker_type", "TEXT")
                        self._ensure_column("stickers", "cache_byte_size", "INTEGER")
                        self._ensure_column("stickers", "cache_validated_at", "TEXT")
                    self.conn.execute("INSERT INTO schema_migrations(version) VALUES(?)", (version,))
                    self.conn.commit()
                await self._run(apply)

    def _ensure_column(self, table: str, column: str, definition: str):
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def close(self):
        if self.conn:
            conn, self.conn = self.conn, None
            conn.close()

    async def _run(self, callback):
        async with self._lock:
            return callback()

    async def execute(self, sql, values=()):
        def run():
            cursor = self.conn.execute(sql, values)
            self.conn.commit()
            return ExecuteResult(cursor.lastrowid, cursor.rowcount)
        return await self._run(run)

    async def upsert_social_source(self, source, *, capabilities) -> tuple[int, str | None]:
        """Persist a source permission snapshot without storing credentials."""
        permission = source.permission
        await self.execute(
            "INSERT INTO social_sources(platform,platform_account_id,source_id,source_type,display_name,usage_mode,"
            "ai_processing_allowed,memory_allowed,retention_allowed,explicit_consent,consent_scope,permission_expires_at,capabilities_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(platform,platform_account_id,source_id) DO UPDATE SET "
            "source_type=excluded.source_type,display_name=excluded.display_name,usage_mode=excluded.usage_mode,"
            "ai_processing_allowed=excluded.ai_processing_allowed,memory_allowed=excluded.memory_allowed,"
            "retention_allowed=excluded.retention_allowed,explicit_consent=excluded.explicit_consent,"
            "consent_scope=excluded.consent_scope,permission_expires_at=excluded.permission_expires_at,"
            "capabilities_json=excluded.capabilities_json,updated_at=CURRENT_TIMESTAMP",
            (
                source.platform, source.platform_account_id, source.source_id, source.source_type,
                source.display_name, permission.usage_mode, int(permission.ai_processing_allowed),
                int(permission.memory_allowed), int(permission.retention_allowed), int(permission.explicit_consent),
                permission.consent_scope,
                permission.expires_at.astimezone(timezone.utc).isoformat() if permission.expires_at else None,
                json.dumps(sorted(capabilities)),
            ),
        )
        row = await self.fetchone(
            "SELECT id,cursor_value FROM social_sources WHERE platform=? AND platform_account_id=? AND source_id=?",
            (source.platform, source.platform_account_id, source.source_id),
        )
        return int(row["id"]), row["cursor_value"]

    async def update_social_source_cursor(self, source_row_id: int, cursor: str | None) -> None:
        await self.execute(
            "UPDATE social_sources SET cursor_value=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (cursor, source_row_id),
        )

    async def record_social_event(
        self, *, source_row_id: int, event, status: str, rejection_reason: str | None,
        content_hash: str | None, content_excerpt: str | None,
    ) -> bool:
        permission = event.source.permission
        result = await self.execute(
            "INSERT INTO social_events(source_row_id,event_id,idempotency_key,actor_id,thread_id,event_kind,content_ref,"
            "content_hash,content_excerpt,media_metadata_json,reply_to_event_id,reference_event_ids_json,visibility,"
            "permissions_json,provenance_json,occurred_at,received_at,status,rejection_reason) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING",
            (
                source_row_id, event.event_id, event.idempotency_key, event.actor_id, event.thread_id,
                event.event_kind, event.content_ref, content_hash, content_excerpt,
                json.dumps(event.media_metadata, sort_keys=True), event.reply_to_event_id,
                json.dumps(event.reference_event_ids), event.visibility,
                json.dumps({
                    "usage_mode": permission.usage_mode,
                    "ai_processing_allowed": permission.ai_processing_allowed,
                    "memory_allowed": permission.memory_allowed,
                    "retention_allowed": permission.retention_allowed,
                    "explicit_consent": permission.explicit_consent,
                    "consent_scope": permission.consent_scope,
                }, sort_keys=True),
                json.dumps(event.provenance, sort_keys=True),
                event.occurred_at.astimezone(timezone.utc).isoformat(),
                event.received_at.astimezone(timezone.utc).isoformat(), status, rejection_reason,
            ),
        )
        return result.rowcount == 1

    async def delete_social_event_content(self, source_row_id: int, event_ids: tuple[str, ...]) -> int:
        """Honor deletion/revocation without deleting the minimal audit record."""
        if not event_ids:
            return 0
        placeholders = ",".join("?" for _ in event_ids)
        result = await self.execute(
            f"UPDATE social_events SET status='deleted',content_ref=NULL,content_hash=NULL,content_excerpt=NULL,"
            f"updated_at=CURRENT_TIMESTAMP WHERE source_row_id=? AND event_id IN ({placeholders}) "
            "AND status IN ('accepted','restored')",
            (source_row_id, *event_ids),
        )
        if result.rowcount:
            await self.execute(
                f"UPDATE social_memories SET status='deleted',summary=NULL,updated_at=CURRENT_TIMESTAMP "
                f"WHERE social_event_id IN (SELECT id FROM social_events WHERE source_row_id=? AND event_id IN ({placeholders})) "
                "AND status='active'",
                (source_row_id, *event_ids),
            )
        return result.rowcount

    async def active_social_evidence_for_events(self, source_row_id: int, event_ids: tuple[str, ...]):
        if not event_ids:
            return []
        placeholders = ",".join("?" for _ in event_ids)
        return await self.fetchall(
            f"SELECT se.* FROM social_evidence se JOIN social_events e ON e.id=se.social_event_id "
            f"WHERE e.source_row_id=? AND e.event_id IN ({placeholders}) AND se.status='active'",
            (source_row_id, *event_ids),
        )

    async def social_belief_evidence_links(self, evidence_id: int):
        return await self.fetchall(
            "SELECT l.belief_id,l.relation,l.weight,b.confidence,b.stance FROM social_belief_evidence l "
            "JOIN social_beliefs b ON b.id=l.belief_id WHERE l.evidence_id=? AND b.status='active'",
            (evidence_id,),
        )

    async def retract_social_evidence(self, evidence_id: int) -> bool:
        result = await self.execute(
            "UPDATE social_evidence SET status='retracted' WHERE id=? AND status='active'", (evidence_id,),
        )
        return result.rowcount == 1

    async def update_social_event_content(
        self,
        source_row_id: int,
        event_ids: tuple[str, ...],
        *,
        content_ref: str | None,
        content_hash: str | None,
        content_excerpt: str | None,
        restoring: bool = False,
    ) -> int:
        """Apply a permitted edit/restore while keeping both events auditable.

        A restore must bring a fresh, permitted representation of the content;
        deleted memory rows intentionally remain deleted. The restore observation
        may form new memory through the normal router instead.
        """
        if not event_ids:
            return 0
        placeholders = ",".join("?" for _ in event_ids)
        source_statuses = "('deleted')" if restoring else "('accepted','restored')"
        target_status = "restored" if restoring else "accepted"
        result = await self.execute(
            f"UPDATE social_events SET status=?,content_ref=?,content_hash=?,content_excerpt=?,"
            f"updated_at=CURRENT_TIMESTAMP WHERE source_row_id=? AND event_id IN ({placeholders}) "
            f"AND status IN {source_statuses}",
            (target_status, content_ref, content_hash, content_excerpt, source_row_id, *event_ids),
        )
        if result.rowcount:
            await self.execute(
                f"UPDATE social_memories SET summary=?,updated_at=CURRENT_TIMESTAMP "
                f"WHERE social_event_id IN (SELECT id FROM social_events WHERE source_row_id=? "
                f"AND event_id IN ({placeholders})) AND status='active'",
                (content_excerpt, source_row_id, *event_ids),
            )
        return result.rowcount

    async def social_event_id(self, source_row_id: int, event_id: str) -> int | None:
        row = await self.fetchone(
            "SELECT id FROM social_events WHERE source_row_id=? AND event_id=?",
            (source_row_id, event_id),
        )
        return int(row["id"]) if row else None

    async def record_social_memory(
        self, *, social_event_id: int, tier: str, actor_id: str | None, summary: str | None,
        importance: float, emotional_significance: float, confidence: float,
        expires_at: str | None, provenance: dict,
    ) -> bool:
        result = await self.execute(
            "INSERT INTO social_memories(social_event_id,tier,actor_id,summary,importance,emotional_significance,confidence,expires_at,provenance_json) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(social_event_id,tier) DO NOTHING",
            (social_event_id, tier, actor_id, summary, importance, emotional_significance,
             confidence, expires_at, json.dumps(provenance, sort_keys=True)),
        )
        return result.rowcount == 1

    async def expire_social_memories(self, now_iso: str) -> int:
        result = await self.execute(
            "UPDATE social_memories SET status='expired',updated_at=CURRENT_TIMESTAMP "
            "WHERE status='active' AND expires_at IS NOT NULL AND expires_at<?",
            (now_iso,),
        )
        return result.rowcount

    async def active_social_memories_for_scope(self, consent_scope: str, now_iso: str):
        """Read only the external memories explicitly permitted for one chat."""
        return await self.fetchall(
            "SELECT m.id,m.tier,m.actor_id,m.summary,m.importance,m.emotional_significance,m.confidence,"
            "e.id AS social_event_id,"
            "m.created_at,e.occurred_at,s.platform,s.source_type "
            "FROM social_memories m "
            "JOIN social_events e ON e.id=m.social_event_id "
            "JOIN social_sources s ON s.id=e.source_row_id "
            "WHERE m.status='active' AND m.summary IS NOT NULL "
            "AND (m.expires_at IS NULL OR m.expires_at>?) "
            "AND e.status IN ('accepted','restored') AND s.status='enabled' "
            "AND s.ai_processing_allowed=1 AND s.memory_allowed=1 "
            "AND s.consent_scope=? "
            "AND (s.permission_expires_at IS NULL OR s.permission_expires_at>=?) "
            "ORDER BY CASE m.tier WHEN 'episodic' THEN 0 WHEN 'working' THEN 1 ELSE 2 END, "
            "(m.importance + m.emotional_significance) DESC, e.occurred_at DESC, m.id DESC LIMIT 200",
            (now_iso, consent_scope, now_iso),
        )

    async def record_social_evidence(self, *, social_event_id: int | None, topic: str, proposition: str,
                                     evidence_kind: str, reliability: float, independence_key: str,
                                     provenance: dict) -> int | None:
        result = await self.execute(
            "INSERT INTO social_evidence(social_event_id,topic,proposition,evidence_kind,reliability,independence_key,provenance_json) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(social_event_id,evidence_kind,proposition) DO NOTHING",
            (social_event_id, topic, proposition, evidence_kind, reliability, independence_key,
             json.dumps(provenance, sort_keys=True)),
        )
        if result.rowcount:
            return result.lastrowid
        row = await self.fetchone(
            "SELECT id FROM social_evidence WHERE social_event_id IS ? AND evidence_kind=? AND proposition=?",
            (social_event_id, evidence_kind, proposition),
        )
        return int(row["id"]) if row else None

    async def social_evidence(self, evidence_id: int):
        return await self.fetchone("SELECT * FROM social_evidence WHERE id=?", (evidence_id,))

    async def create_social_belief(self, *, topic: str, proposition: str, stance: str, confidence: float,
                                   importance: float, emotional_significance: float, origin: str, provenance: dict) -> int | None:
        result = await self.execute(
            "INSERT INTO social_beliefs(topic,proposition,stance,confidence,importance,emotional_significance,origin,provenance_json) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(topic,proposition) DO NOTHING",
            (topic, proposition, stance, confidence, importance, emotional_significance, origin,
             json.dumps(provenance, sort_keys=True)),
        )
        return result.lastrowid if result.rowcount else None

    async def social_belief(self, belief_id: int):
        return await self.fetchone("SELECT * FROM social_beliefs WHERE id=? AND status='active'", (belief_id,))

    async def link_social_belief_evidence(self, *, belief_id: int, evidence_id: int, relation: str,
                                          weight: float, independence_key: str) -> bool:
        result = await self.execute(
            "INSERT INTO social_belief_evidence(belief_id,evidence_id,relation,weight,independence_key) VALUES(?,?,?,?,?) "
            "ON CONFLICT DO NOTHING",
            (belief_id, evidence_id, relation, weight, independence_key),
        )
        return result.rowcount == 1

    async def revise_social_belief(self, *, belief_id: int, evidence_id: int, old_confidence: float,
                                   new_confidence: float, old_stance: str, new_stance: str, reason: str) -> None:
        await self.execute(
            "UPDATE social_beliefs SET confidence=?,stance=?,last_revised_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'",
            (new_confidence, new_stance, belief_id),
        )
        await self.execute(
            "INSERT INTO social_belief_revisions(belief_id,evidence_id,old_confidence,new_confidence,old_stance,new_stance,reason) "
            "VALUES(?,?,?,?,?,?,?)",
            (belief_id, evidence_id, old_confidence, new_confidence, old_stance, new_stance, reason),
        )

    async def active_social_beliefs(self):
        return await self.fetchall(
            "SELECT id,topic,proposition,stance,confidence,importance,emotional_significance,origin "
            "FROM social_beliefs WHERE status='active' ORDER BY importance DESC,confidence DESC,id DESC LIMIT 100",
        )

    async def fetchall(self, sql, values=()):
        return await self._run(lambda: self.conn.execute(sql, values).fetchall())

    async def fetchone(self, sql, values=()):
        return await self._run(lambda: self.conn.execute(sql, values).fetchone())

    async def ensure_user(self, user_id: int, username: str | None):
        await self.execute("INSERT INTO users(id,username) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username", (user_id, username))

    async def claim_update(self, telegram_update_id: int) -> bool:
        result = await self.execute("INSERT INTO telegram_updates(telegram_update_id) VALUES(?) ON CONFLICT(telegram_update_id) DO NOTHING", (telegram_update_id,))
        return result.rowcount == 1

    async def record_message(self, *, chat_id: int, telegram_message_id: int, sender: str, kind: str, user_id: int | None = None, text: str | None = None, sticker_file_id: str | None = None, reply_to: int | None = None) -> int | None:
        cursor = await self.execute(
            "INSERT INTO messages(chat_id,telegram_message_id,user_id,sender,type,text,sticker_file_id,reply_to) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(chat_id,telegram_message_id) DO NOTHING",
            (chat_id, telegram_message_id, user_id, sender, kind, text, sticker_file_id, reply_to),
        )
        return cursor.lastrowid if cursor.rowcount else None

    async def record_telegram_message_state(
        self, *, transport: str, account_id: int, peer_type: str, peer_id: int,
        message_id: int, direction: str, local_chat_id: int, status: str,
        source: str, read_watermark: int | None = None,
    ) -> int:
        """Persist a truthful transport state plus its compact transition log.

        State and event rows intentionally carry only protocol identifiers and
        status metadata, never message contents.  A unique identity includes
        the account and peer so two Telegram accounts cannot collide on an
        ordinary per-chat message ID.
        """
        def run():
            self.conn.execute(
                "INSERT INTO telegram_message_states(transport,account_id,peer_type,peer_id,message_id,direction,local_chat_id,status,read_watermark) "
                "VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(transport,account_id,peer_type,peer_id,message_id,direction) DO UPDATE SET "
                "local_chat_id=excluded.local_chat_id,status=excluded.status,read_watermark=COALESCE(excluded.read_watermark,telegram_message_states.read_watermark),updated_at=CURRENT_TIMESTAMP",
                (transport, account_id, peer_type, peer_id, message_id, direction, local_chat_id, status, read_watermark),
            )
            row = self.conn.execute(
                "SELECT id FROM telegram_message_states WHERE transport=? AND account_id=? AND peer_type=? AND peer_id=? AND message_id=? AND direction=?",
                (transport, account_id, peer_type, peer_id, message_id, direction),
            ).fetchone()
            self.conn.execute(
                "INSERT OR IGNORE INTO telegram_message_state_events(state_id,status,source,read_watermark) VALUES(?,?,?,?)",
                (row["id"], status, source, read_watermark if read_watermark is not None else -1),
            )
            self.conn.commit()
            return row["id"]
        return await self._run(run)

    async def claim_action_dispatch(
        self, *, transport: str, generation_id: str, action_index: int,
        chat_id: int, action_type: str,
    ) -> bool:
        """Durably claim a single external ActionQueue slot before execution."""
        result = await self.execute(
            "INSERT INTO action_dispatches(transport,generation_id,action_index,chat_id,action_type,status) "
            "VALUES(?,?,?,?,?,'claimed') ON CONFLICT(transport,generation_id,action_index) DO NOTHING",
            (transport, generation_id, action_index, chat_id, action_type),
        )
        if result.rowcount == 1:
            return True
        # `claimed` is deliberately before the executor starts. It is safe to
        # re-enqueue after a process dies while a slot is only sitting in the
        # in-memory queue. The compare-and-swap in start_action_dispatch still
        # ensures only one concurrent process can enter the executor.
        reclaimed = await self.execute(
            "UPDATE action_dispatches SET updated_at=CURRENT_TIMESTAMP,detail=NULL "
            "WHERE transport=? AND generation_id=? AND action_index=? AND status='claimed' "
            "AND execution_state='not_started' AND recovery_status='none'",
            (transport, generation_id, action_index),
        )
        return reclaimed.rowcount == 1

    async def start_action_dispatch(
        self, *, transport: str, generation_id: str, action_index: int,
    ) -> bool:
        """Atomically enter the external-call zone for one claimed slot."""
        result = await self.execute(
            "UPDATE action_dispatches SET execution_state='executing',started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP "
            "WHERE transport=? AND generation_id=? AND action_index=? AND status='claimed' "
            "AND execution_state='not_started' AND recovery_status='none'",
            (transport, generation_id, action_index),
        )
        return result.rowcount == 1

    async def cancel_unstarted_action_dispatch(
        self, *, transport: str, generation_id: str, action_index: int,
    ) -> bool:
        """Terminally record an action superseded before its executor started."""
        result = await self.execute(
            "UPDATE action_dispatches SET status='failed',detail='cancelled before external dispatch',"
            "updated_at=CURRENT_TIMESTAMP WHERE transport=? AND generation_id=? AND action_index=? "
            "AND status='claimed' AND execution_state='not_started' AND recovery_status='none'",
            (transport, generation_id, action_index),
        )
        return result.rowcount == 1

    async def finish_action_dispatch(
        self, *, transport: str, generation_id: str, action_index: int, success: bool,
    ) -> None:
        await self.execute(
            "UPDATE action_dispatches SET status=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE transport=? AND generation_id=? AND action_index=? AND status='claimed' AND execution_state='executing'",
            ("completed" if success else "failed", transport, generation_id, action_index),
        )

    async def reconcile_action_dispatches(self, *, transport: str) -> dict[str, int]:
        """Make crash leftovers diagnosable without resending uncertain work.

        A `claimed` row has not passed the executor compare-and-swap and may be
        reclaimed if the same generation is reconstructed. An `executing` row
        could have sent successfully just before the process died, so it is
        terminally marked unknown and is intentionally never auto-retried.
        """
        unknown = await self.execute(
            "UPDATE action_dispatches SET status='failed',recovery_status='unknown',"
            "detail='process stopped during external dispatch',updated_at=CURRENT_TIMESTAMP "
            "WHERE transport=? AND status='claimed' AND execution_state='executing' AND recovery_status='none'",
            (transport,),
        )
        claimed = await self.fetchone(
            "SELECT COUNT(*) AS count FROM action_dispatches WHERE transport=? AND status='claimed' "
            "AND execution_state='not_started' AND recovery_status='none'",
            (transport,),
        )
        return {"unknown": unknown.rowcount, "reclaimable_claims": int(claimed["count"] if claimed else 0)}

    async def transition_telegram_messages(
        self, *, local_chat_id: int, message_ids: list[int], status: str,
        source: str, read_watermark: int | None = None,
    ) -> int:
        """Move known incoming MTProto messages forward without content access."""
        if not message_ids:
            return 0
        placeholders = ",".join("?" for _ in message_ids)
        rows = await self.fetchall(
            f"SELECT id FROM telegram_message_states WHERE local_chat_id=? AND direction='incoming' AND message_id IN ({placeholders})",
            (local_chat_id, *message_ids),
        )
        for row in rows:
            await self.execute(
                "UPDATE telegram_message_states SET status=?,read_watermark=COALESCE(?,read_watermark),updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, read_watermark, row["id"]),
            )
            await self.execute(
                "INSERT OR IGNORE INTO telegram_message_state_events(state_id,status,source,read_watermark) VALUES(?,?,?,?)",
                (row["id"], status, source, read_watermark if read_watermark is not None else -1),
            )
        return len(rows)

    async def transition_telegram_turn(
        self, *, local_chat_id: int, turn_id: int | None, status: str, source: str,
    ) -> int:
        if turn_id is None:
            return 0
        rows = await self.fetchall(
            "SELECT telegram_message_id FROM messages WHERE chat_id=? AND sender='user' AND conversation_turn_id=?",
            (local_chat_id, turn_id),
        )
        return await self.transition_telegram_messages(
            local_chat_id=local_chat_id, message_ids=[row["telegram_message_id"] for row in rows],
            status=status, source=source,
        )

    async def confirm_mtproto_inbox_watermark(
        self, *, account_id: int, peer_id: int, max_message_id: int, source: str,
    ) -> int:
        """Record Telegram's own inbox watermark without regressing later state."""
        rows = await self.fetchall(
            "SELECT id,status FROM telegram_message_states WHERE transport='mtproto' AND account_id=? "
            "AND peer_type='user' AND peer_id=? AND direction='incoming' AND message_id<=?",
            (account_id, peer_id, max_message_id),
        )
        # A server update can arrive after the request callback has parked the
        # durable read job in `read_ack_waiting`. That is still a forward
        # transition, never an observational no-op.
        forward = {"received", "scheduled_read", "read_ack_requested", "read_ack_waiting"}
        for row in rows:
            next_status = "read_ack_confirmed" if row["status"] in forward else row["status"]
            await self.execute(
                "UPDATE telegram_message_states SET status=?,read_watermark=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (next_status, max_message_id, row["id"]),
            )
            await self.execute(
                "INSERT OR IGNORE INTO telegram_message_state_events(state_id,status,source,read_watermark) VALUES(?,?,?,?)",
                (row["id"], "read_ack_confirmed", source, max_message_id),
            )
        return len(rows)

    async def mark_mtproto_read_confirmation_pending(
        self, *, local_chat_id: int, message_ids: list[int], read_watermark: int | None,
        source: str,
    ) -> int:
        """Park an unconfirmed receipt without regressing a raced confirmation.

        `MessageRead` updates are concurrent with the RPC result path.  A
        blind state update here could turn an already-confirmed receipt back
        into `read_ack_waiting`, which in turn causes pointless retries and a
        misleading monitor state.
        """
        if not message_ids:
            return 0
        placeholders = ",".join("?" for _ in message_ids)
        rows = await self.fetchall(
            f"SELECT id,message_id,status,read_watermark FROM telegram_message_states "
            f"WHERE local_chat_id=? AND direction='incoming' AND message_id IN ({placeholders})",
            (local_chat_id, *message_ids),
        )
        updated = 0
        for row in rows:
            confirmed = row["read_watermark"] is not None and row["read_watermark"] >= row["message_id"]
            if confirmed:
                continue
            result = await self.execute(
                "UPDATE telegram_message_states SET status='read_ack_waiting',"
                "read_watermark=COALESCE(?,read_watermark),updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status IN ('received','scheduled_read','read_ack_requested','read_ack_waiting') "
                "AND (read_watermark IS NULL OR read_watermark < message_id)",
                (read_watermark, row["id"]),
            )
            if result.rowcount:
                updated += 1
                await self.execute(
                    "INSERT OR IGNORE INTO telegram_message_state_events(state_id,status,source,read_watermark) VALUES(?,?,?,?)",
                    (row["id"], "read_ack_waiting", source, read_watermark if read_watermark is not None else -1),
                )
        return updated

    async def mtproto_inbox_read_confirmed(self, *, local_chat_id: int, message_ids: list[int]) -> bool:
        """Whether Telegram has confirmed every requested incoming message."""
        if not message_ids:
            return False
        placeholders = ",".join("?" for _ in message_ids)
        row = await self.fetchone(
            f"SELECT COUNT(*) AS count FROM telegram_message_states "
            f"WHERE local_chat_id=? AND direction='incoming' AND message_id IN ({placeholders}) "
            "AND read_watermark IS NOT NULL AND read_watermark >= message_id",
            (local_chat_id, *message_ids),
        )
        return bool(row and row["count"] == len(message_ids))

    async def record_mtproto_outbox_read_watermark(
        self, *, account_id: int, peer_id: int, max_message_id: int, source: str,
    ) -> int:
        """Store recipient-read watermark separately; it is not a local tick."""
        rows = await self.fetchall(
            "SELECT id FROM telegram_message_states WHERE transport='mtproto' AND account_id=? "
            "AND peer_type='user' AND peer_id=? AND direction='outgoing' AND message_id<=?",
            (account_id, peer_id, max_message_id),
        )
        for row in rows:
            await self.execute(
                "UPDATE telegram_message_states SET read_watermark=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (max_message_id, row["id"]),
            )
            await self.execute(
                "INSERT OR IGNORE INTO telegram_message_state_events(state_id,status,source,read_watermark) VALUES(?,?,?,?)",
                (row["id"], "recipient_read", source, max_message_id),
            )
        return len(rows)

    async def create_turn(self, *, user_id: int, chat_id: int, merged_text: str, telegram_message_ids: list[int]) -> int:
        cursor = await self.execute("INSERT INTO conversation_turns(user_id,chat_id,merged_text) VALUES(?,?,?)", (user_id, chat_id, merged_text))
        turn_id = cursor.lastrowid
        if telegram_message_ids:
            placeholders = ",".join("?" for _ in telegram_message_ids)
            await self.execute(f"UPDATE messages SET conversation_turn_id=? WHERE chat_id=? AND telegram_message_id IN ({placeholders})", (turn_id, chat_id, *telegram_message_ids))
        return turn_id

    async def ensure_turn_for_batch(self, *, user_id: int, chat_id: int, merged_text: str,
                                    telegram_message_ids: list[int]) -> int:
        """Create one durable logical turn, or reuse it after a failed dispatch."""
        if not telegram_message_ids:
            raise ValueError("telegram_message_ids must not be empty")
        placeholders = ",".join("?" for _ in telegram_message_ids)

        def run():
            rows = self.conn.execute(
                f"SELECT telegram_message_id,conversation_turn_id FROM messages WHERE chat_id=? "
                f"AND telegram_message_id IN ({placeholders})",
                (chat_id, *telegram_message_ids),
            ).fetchall()
            existing = {row["conversation_turn_id"] for row in rows if row["conversation_turn_id"] is not None}
            if existing:
                if len(existing) != 1:
                    raise RuntimeError("read batch spans multiple conversation turns")
                turn_id = existing.pop()
                self.conn.execute(
                    f"UPDATE messages SET conversation_turn_id=? WHERE chat_id=? "
                    f"AND telegram_message_id IN ({placeholders}) AND conversation_turn_id IS NULL",
                    (turn_id, chat_id, *telegram_message_ids),
                )
                self.conn.commit()
                return turn_id
            cursor = self.conn.execute(
                "INSERT INTO conversation_turns(user_id,chat_id,merged_text) VALUES(?,?,?)",
                (user_id, chat_id, merged_text),
            )
            turn_id = cursor.lastrowid
            self.conn.execute(
                f"UPDATE messages SET conversation_turn_id=? WHERE chat_id=? "
                f"AND telegram_message_id IN ({placeholders}) AND conversation_turn_id IS NULL",
                (turn_id, chat_id, *telegram_message_ids),
            )
            self.conn.commit()
            return turn_id

        return await self._run(run)

    async def recent_messages(self, chat_id: int, limit: int = 24, recent_media_hours: int = 24,
                              exclude_turn_id: int | None = None):
        """Return only one Telegram chat's conversational history.

        A user can talk to the bot in a private chat and in groups.  Those are
        separate conversations even when they share the same Telegram user ID.
        """
        excluded = " AND (m.conversation_turn_id IS NULL OR m.conversation_turn_id != ?)" if exclude_turn_id is not None else ""
        values = ((f"-{recent_media_hours} hours", chat_id, exclude_turn_id, limit)
                  if exclude_turn_id is not None else (f"-{recent_media_hours} hours", chat_id, limit))
        rows = await self.fetchall("SELECT m.sender,m.text,m.type,m.timestamp,ss.visual_description AS sticker_visual,ss.meanings_json AS sticker_meanings,CASE WHEN m.timestamp >= datetime('now', ?) THEN md.description END AS photo_description FROM messages m LEFT JOIN stickers s ON s.file_id=m.sticker_file_id LEFT JOIN sticker_semantics ss ON ss.sticker_id=s.id LEFT JOIN message_media mm ON mm.chat_id=m.chat_id AND mm.telegram_message_id=m.telegram_message_id LEFT JOIN media_descriptions md ON md.media_id=mm.media_id WHERE m.chat_id=? AND m.deleted_at IS NULL" + excluded + " ORDER BY m.id DESC LIMIT ?", values)
        return list(reversed(rows))

    async def record_reaction(self, *, chat_id: int, telegram_message_id: int, actor: str, emoji: str, actor_user_id: int | None = None, active: bool = True):
        """Persist a reaction state without treating it as a conversational message."""
        await self.execute(
            "INSERT INTO message_reactions(chat_id,telegram_message_id,actor,actor_user_id,emoji,active) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(chat_id,telegram_message_id,actor,actor_user_id,emoji) DO UPDATE SET active=excluded.active,updated_at=CURRENT_TIMESTAMP",
            (chat_id, telegram_message_id, actor, actor_user_id or 0, emoji, int(active)),
        )

    async def recent_reaction_signals(self, chat_id: int, user_id: int, limit: int = 6, hours: int = 72):
        """Recent active reactions by the owner to assistant messages, for future context only."""
        return await self.fetchall(
            "SELECT r.emoji,r.updated_at,m.text,m.type FROM message_reactions r "
            "JOIN messages m ON m.chat_id=r.chat_id AND m.telegram_message_id=r.telegram_message_id "
            "WHERE r.chat_id=? AND r.actor='user' AND r.actor_user_id=? AND r.active=1 AND m.sender='assistant' "
            "AND r.updated_at >= datetime('now', ?) ORDER BY r.updated_at DESC LIMIT ?",
            (chat_id, user_id, f"-{hours} hours", limit),
        )

    async def pending_user_text(self, chat_id: int) -> tuple[int | None, str]:
        row = await self.fetchone("SELECT MAX(id) AS last_bot_id FROM messages WHERE chat_id=? AND sender='assistant'", (chat_id,))
        after_id = row["last_bot_id"] or 0
        rows = await self.fetchall("SELECT text FROM messages WHERE chat_id=? AND sender='user' AND id>? ORDER BY id", (chat_id, after_id))
        user = await self.fetchone("SELECT user_id FROM messages WHERE chat_id=? AND sender='user' ORDER BY id DESC LIMIT 1", (chat_id,))
        return (user["user_id"] if user else None, "\n".join(row["text"] or "[sticker]" for row in rows))

    async def known_user_message(self, chat_id: int, message_id: int) -> bool:
        return bool(await self.fetchone("SELECT 1 FROM messages WHERE chat_id=? AND telegram_message_id=? AND sender='user' AND deleted_at IS NULL", (chat_id, message_id)))

    async def known_assistant_message(self, chat_id: int, message_id: int) -> bool:
        """Only an extant message sent by Anya is a valid edit/delete target."""
        return bool(await self.fetchone(
            "SELECT 1 FROM messages WHERE chat_id=? AND telegram_message_id=? AND sender='assistant' AND deleted_at IS NULL",
            (chat_id, message_id),
        ))

    async def update_assistant_message(self, chat_id: int, message_id: int, text: str) -> bool:
        result = await self.execute(
            "UPDATE messages SET text=? WHERE chat_id=? AND telegram_message_id=? AND sender='assistant' AND deleted_at IS NULL",
            (text, chat_id, message_id),
        )
        return result.rowcount == 1

    async def mark_assistant_message_deleted(self, chat_id: int, message_id: int) -> bool:
        result = await self.execute(
            "UPDATE messages SET deleted_at=CURRENT_TIMESTAMP WHERE chat_id=? AND telegram_message_id=? AND sender='assistant' AND deleted_at IS NULL",
            (chat_id, message_id),
        )
        return result.rowcount == 1

    async def chat_response_signals(self, chat_id: int):
        row = await self.fetchone("SELECT (julianday('now') - julianday(MAX(timestamp))) * 86400 AS seconds_since_last, SUM(CASE WHEN sender='user' THEN 1 ELSE 0 END) AS user_messages FROM messages WHERE chat_id=?", (chat_id,))
        return {"seconds_since_last": row["seconds_since_last"] or 0, "user_messages": row["user_messages"] or 0}

    async def chat_is_active(self, chat_id: int, window_seconds: float) -> bool:
        """True only while this chat has a recent assistant reply."""
        row = await self.fetchone(
            "SELECT (julianday('now')-julianday(MAX(timestamp)))*86400 AS seconds_since_assistant "
            "FROM messages WHERE chat_id=? AND sender='assistant'",
            (chat_id,),
        )
        seconds = row["seconds_since_assistant"] if row else None
        return seconds is not None and seconds <= window_seconds

    async def record_media(self, *, chat_id: int, telegram_message_id: int, file_id: str, file_unique_id: str | None, media_type: str, mime_type: str | None, local_cache_path: str | None, byte_size: int | None) -> int:
        await self.execute(
            "INSERT INTO media(chat_id,telegram_file_id,telegram_file_unique_id,media_type,mime_type,local_cache_path,byte_size) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(chat_id,telegram_file_id) DO UPDATE SET local_cache_path=COALESCE(excluded.local_cache_path,media.local_cache_path), mime_type=COALESCE(excluded.mime_type,media.mime_type), byte_size=COALESCE(excluded.byte_size,media.byte_size)",
            (chat_id, file_id, file_unique_id, media_type, mime_type, local_cache_path, byte_size),
        )
        row = await self.fetchone("SELECT id FROM media WHERE chat_id=? AND telegram_file_id=?", (chat_id, file_id))
        media_id = row["id"]
        await self.execute("INSERT OR IGNORE INTO message_media(chat_id,telegram_message_id,media_id) VALUES(?,?,?)", (chat_id, telegram_message_id, media_id))
        return media_id

    async def queue_media_description(self, media_id: int):
        await self.execute("INSERT INTO media_analysis_jobs(media_id,status) VALUES(?,'pending') ON CONFLICT(media_id) DO NOTHING", (media_id,))

    async def pending_user_message_ids(self, chat_id: int) -> tuple[int | None, str, list[int]]:
        user_id, text = await self.pending_user_text(chat_id)
        row = await self.fetchone("SELECT MAX(id) AS last_bot_id FROM messages WHERE chat_id=? AND sender='assistant'", (chat_id,))
        rows = await self.fetchall("SELECT telegram_message_id FROM messages WHERE chat_id=? AND sender='user' AND id>? ORDER BY id", (chat_id, row["last_bot_id"] or 0))
        return user_id, text, [row["telegram_message_id"] for row in rows]

    async def turn_id_for_messages(self, chat_id: int, message_ids: list[int]) -> int | None:
        if not message_ids:
            return None
        placeholders = ",".join("?" for _ in message_ids)
        row = await self.fetchone(
            f"SELECT conversation_turn_id FROM messages WHERE chat_id=? "
            f"AND telegram_message_id IN ({placeholders}) AND conversation_turn_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (chat_id, *message_ids),
        )
        return row["conversation_turn_id"] if row else None

    async def unread_user_messages_up_to(self, chat_id: int, boundary_message_id: int):
        return await self.fetchall(
            "SELECT id,telegram_message_id,user_id,type,text,sticker_file_id FROM messages "
            "WHERE chat_id=? AND sender='user' AND internally_read_at IS NULL AND telegram_message_id<=? ORDER BY id",
            (chat_id, boundary_message_id),
        )

    async def read_batch_for_boundary(self, chat_id: int, boundary_message_id: int):
        """Recover an already-created batch on retry, otherwise load unread rows."""
        boundary = await self.fetchone(
            "SELECT conversation_turn_id FROM messages WHERE chat_id=? AND sender='user' "
            "AND telegram_message_id=?",
            (chat_id, boundary_message_id),
        )
        if boundary and boundary["conversation_turn_id"] is not None:
            return await self.fetchall(
                "SELECT id,telegram_message_id,user_id,type,text,sticker_file_id FROM messages "
                "WHERE chat_id=? AND sender='user' AND conversation_turn_id=? ORDER BY id",
                (chat_id, boundary["conversation_turn_id"]),
            )
        return await self.unread_user_messages_up_to(chat_id, boundary_message_id)

    async def mark_messages_read(self, chat_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return
        marks = ",".join("?" for _ in message_ids)
        await self.execute(
            f"UPDATE messages SET internally_read_at=CURRENT_TIMESTAMP WHERE chat_id=? AND sender='user' "
            f"AND internally_read_at IS NULL AND telegram_message_id IN ({marks})",
            (chat_id, *message_ids),
        )

    async def has_unread_user_message_after(self, chat_id: int, boundary_message_id: int) -> bool:
        """A newer arrival must not be silently folded into a claimed batch."""
        return bool(await self.fetchone(
            "SELECT 1 FROM messages WHERE chat_id=? AND sender='user' AND internally_read_at IS NULL "
            "AND telegram_message_id>? LIMIT 1",
            (chat_id, boundary_message_id),
        ))

    async def has_unread_user_messages(self, chat_id: int) -> bool:
        return bool(await self.fetchone(
            "SELECT 1 FROM messages WHERE chat_id=? AND sender='user' AND internally_read_at IS NULL LIMIT 1",
            (chat_id,),
        ))

    async def has_active_scheduled_read(self, chat_id: int) -> bool:
        return bool(await self.fetchone(
            "SELECT 1 FROM scheduled_reads WHERE chat_id=? AND status IN ('pending','processing') LIMIT 1",
            (chat_id,),
        ))

    async def bedtime_done(self, chat_id: int, local_day: str) -> bool:
        return bool(await self.fetchone("SELECT 1 FROM bedtime_history WHERE chat_id=? AND local_day=?", (chat_id, local_day)))

    async def record_bedtime(self, chat_id: int, local_day: str, source: str) -> None:
        await self.execute("INSERT OR IGNORE INTO bedtime_history(chat_id,local_day,source) VALUES(?,?,?)", (chat_id, local_day, source))

    async def bedtime_delay_minutes(self, chat_id: int, sleep_start_date: str) -> int:
        row = await self.fetchone(
            "SELECT delay_minutes FROM bedtime_overrides WHERE chat_id=? AND sleep_start_date=?",
            (chat_id, sleep_start_date),
        )
        return row["delay_minutes"] if row else 0

    async def set_bedtime_delay(self, chat_id: int, sleep_start_date: str, delay_minutes: int) -> None:
        await self.execute(
            "INSERT INTO bedtime_overrides(chat_id,sleep_start_date,delay_minutes) VALUES(?,?,?) "
            "ON CONFLICT(chat_id,sleep_start_date) DO UPDATE SET delay_minutes=excluded.delay_minutes,created_at=CURRENT_TIMESTAMP",
            (chat_id, sleep_start_date, delay_minutes),
        )

    async def import_sticker_pack(self, *, name: str, title: str, stickers: list[tuple[str, str, str, str]], current_unique_id: str | None) -> int:
        """One SQLite transaction for a whole pack; returns number of newly queued jobs."""
        def run():
            with self.conn:
                self.conn.execute("INSERT INTO sticker_sets(name,title,total_stickers,analysis_status) VALUES(?,?,?,'pending') ON CONFLICT(name) DO UPDATE SET title=excluded.title,total_stickers=excluded.total_stickers", (name, title, len(stickers)))
                self.conn.executemany("INSERT OR IGNORE INTO stickers(file_id,file_unique_id,set_name,emoji,sticker_type) VALUES(?,?,?,?,?)", [(file_id, unique, name, emoji, kind) for file_id, unique, emoji, kind in stickers])
                values = [unique for _, unique, _, _ in stickers]
                marks = ",".join("?" for _ in values)
                rows = self.conn.execute(f"SELECT s.id,s.file_unique_id,ss.sticker_id AS semantic_id FROM stickers s LEFT JOIN sticker_semantics ss ON ss.sticker_id=s.id WHERE s.file_unique_id IN ({marks})", values).fetchall()
                jobs = [(row["id"], 100 if row["file_unique_id"] == current_unique_id else 0) for row in rows if row["semantic_id"] is None]
                self.conn.executemany("INSERT INTO sticker_analysis_jobs(sticker_id,status,priority) VALUES(?,'pending',?) ON CONFLICT(sticker_id) DO UPDATE SET priority=MAX(priority,excluded.priority),status=CASE WHEN sticker_analysis_jobs.status='failed' THEN 'pending' ELSE sticker_analysis_jobs.status END,updated_at=CURRENT_TIMESTAMP", jobs)
                return len(jobs)
        return await self._run(run)
