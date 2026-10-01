import asyncio
import sqlite3
from dataclasses import dataclass
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

    async def create_turn(self, *, user_id: int, chat_id: int, merged_text: str, telegram_message_ids: list[int]) -> int:
        cursor = await self.execute("INSERT INTO conversation_turns(user_id,chat_id,merged_text) VALUES(?,?,?)", (user_id, chat_id, merged_text))
        turn_id = cursor.lastrowid
        if telegram_message_ids:
            placeholders = ",".join("?" for _ in telegram_message_ids)
            await self.execute(f"UPDATE messages SET conversation_turn_id=? WHERE chat_id=? AND telegram_message_id IN ({placeholders})", (turn_id, chat_id, *telegram_message_ids))
        return turn_id

    async def recent_messages(self, chat_id: int, limit: int = 24, recent_media_hours: int = 24):
        """Return only one Telegram chat's conversational history.

        A user can talk to the bot in a private chat and in groups.  Those are
        separate conversations even when they share the same Telegram user ID.
        """
        rows = await self.fetchall("SELECT m.sender,m.text,m.type,m.timestamp,ss.visual_description AS sticker_visual,ss.meanings_json AS sticker_meanings,CASE WHEN m.timestamp >= datetime('now', ?) THEN md.description END AS photo_description FROM messages m LEFT JOIN stickers s ON s.file_id=m.sticker_file_id LEFT JOIN sticker_semantics ss ON ss.sticker_id=s.id LEFT JOIN message_media mm ON mm.chat_id=m.chat_id AND mm.telegram_message_id=m.telegram_message_id LEFT JOIN media_descriptions md ON md.media_id=mm.media_id WHERE m.chat_id=? ORDER BY m.id DESC LIMIT ?", (f"-{recent_media_hours} hours", chat_id, limit))
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
        return bool(await self.fetchone("SELECT 1 FROM messages WHERE chat_id=? AND telegram_message_id=? AND sender='user'", (chat_id, message_id)))

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

    async def unread_user_messages_up_to(self, chat_id: int, boundary_message_id: int):
        return await self.fetchall(
            "SELECT id,telegram_message_id,user_id,type,text,sticker_file_id FROM messages "
            "WHERE chat_id=? AND sender='user' AND internally_read_at IS NULL AND telegram_message_id<=? ORDER BY id",
            (chat_id, boundary_message_id),
        )

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
