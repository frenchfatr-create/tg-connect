import aiosqlite
from pathlib import Path


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS connections (
                connection_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                user_chat_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                rights_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS chats (
                connection_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                title TEXT,
                announced INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(connection_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                connection_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                message_id INTEGER NOT NULL,
                sender_id INTEGER,
                sender_name TEXT,
                text TEXT,
                content_type TEXT,
                message_date TEXT,
                edited INTEGER NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0,
                UNIQUE(connection_id, chat_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS deletions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                connection_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                deleted_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)
            # Safe, additive migrations for databases created by older releases.
            cur = await db.execute("PRAGMA table_info(messages)")
            existing = {row[1] for row in await cur.fetchall()}
            for column, declaration in (
                ("media_file_id", "TEXT"),
                ("media_file_unique_id", "TEXT"),
                ("media_group_id", "TEXT"),
            ):
                if column not in existing:
                    await db.execute(f"ALTER TABLE messages ADD COLUMN {column} {declaration}")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_media ON messages(media_file_id)")
            await db.commit()

    async def close(self):
        return

    async def set_setting(self, key, value):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            await db.commit()

    async def get_setting(self, key, default=None):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = await cur.fetchone()
            return row[0] if row else default

    async def save_connection(self, connection_id, user_id, user_chat_id, enabled, rights_json):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO connections(connection_id,user_id,user_chat_id,enabled,rights_json)
                VALUES(?,?,?,?,?) ON CONFLICT(connection_id) DO UPDATE SET
                enabled=excluded.enabled, rights_json=excluded.rights_json, updated_at=CURRENT_TIMESTAMP""",
                (connection_id, user_id, user_chat_id, int(enabled), rights_json),
            )
            await db.commit()

    async def mark_chat_announced(self, connection_id, chat_id, chat_title=None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO chats(connection_id,chat_id,title,announced) VALUES(?,?,?,0) "
                "ON CONFLICT(connection_id,chat_id) DO UPDATE SET title=excluded.title",
                (connection_id, chat_id, chat_title or str(chat_id)),
            )
            cur = await db.execute(
                "SELECT announced FROM chats WHERE connection_id=? AND chat_id=?",
                (connection_id, chat_id),
            )
            row = await cur.fetchone()
            first = bool(row and row[0] == 0)
            if first:
                await db.execute(
                    "UPDATE chats SET announced=1 WHERE connection_id=? AND chat_id=?",
                    (connection_id, chat_id),
                )
            await db.commit()
            return first

    async def reset_chat_announcement(self, connection_id, chat_id):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE chats SET announced=0 WHERE connection_id=? AND chat_id=?",
                (connection_id, chat_id),
            )
            await db.commit()

    async def save_message(self, connection_id, chat_id, chat_title, message_id,
                           sender_id, sender_name, text, content_type, message_date, edited,
                           media_file_id=None, media_file_unique_id=None, media_group_id=None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO chats(connection_id,chat_id,title) VALUES(?,?,?) "
                "ON CONFLICT(connection_id,chat_id) DO UPDATE SET title=excluded.title",
                (connection_id, chat_id, chat_title),
            )
            await db.execute(
                """INSERT INTO messages(connection_id,chat_id,chat_title,message_id,sender_id,
                sender_name,text,content_type,message_date,edited,
                media_file_id,media_file_unique_id,media_group_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(connection_id,chat_id,message_id) DO UPDATE SET
                text=excluded.text, edited=excluded.edited,
                content_type=excluded.content_type,
                media_file_id=excluded.media_file_id,
                media_file_unique_id=excluded.media_file_unique_id,
                media_group_id=excluded.media_group_id""",
                (connection_id, chat_id, chat_title, message_id, sender_id,
                 sender_name, text, content_type, message_date, int(edited),
                 media_file_id, media_file_unique_id, media_group_id),
            )
            await db.commit()

    async def update_message(self, connection_id, chat_id, message_id, text, edited=True):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE messages SET text=?, edited=? WHERE connection_id=? AND chat_id=? AND message_id=?""",
                (text, int(edited), connection_id, chat_id, message_id),
            )
            await db.commit()

    async def record_deletions(self, connection_id, chat_id, message_ids):
        async with aiosqlite.connect(self.path) as db:
            for mid in message_ids:
                await db.execute(
                    "INSERT INTO deletions(connection_id,chat_id,message_id) VALUES(?,?,?)",
                    (connection_id, chat_id, mid),
                )
                await db.execute(
                    "UPDATE messages SET deleted=1 WHERE connection_id=? AND chat_id=? AND message_id=?",
                    (connection_id, chat_id, mid),
                )
            await db.commit()

    async def stats(self):
        async with aiosqlite.connect(self.path) as db:
            async def scalar(sql):
                cur = await db.execute(sql)
                row = await cur.fetchone()
                return int(row[0] or 0)
            return {
                "connections": await scalar("SELECT COUNT(*) FROM connections WHERE enabled=1"),
                "chats": await scalar("SELECT COUNT(*) FROM chats"),
                "messages": await scalar("SELECT COUNT(*) FROM messages"),
                "deletions": await scalar("SELECT COUNT(*) FROM deletions"),
            }


    async def list_chats(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("""
                SELECT c.connection_id,c.chat_id,c.title,COUNT(m.id) AS message_count,
                       MAX(m.message_date) AS last_message
                FROM chats c LEFT JOIN messages m
                ON c.connection_id=m.connection_id AND c.chat_id=m.chat_id
                GROUP BY c.connection_id,c.chat_id ORDER BY last_message DESC
            """)
            return [dict(r) for r in await cur.fetchall()]

    async def list_messages(self, limit=50, query="", chat_id=None, deleted=None):
        clauses, params = [], []
        if query:
            clauses.append("(text LIKE ? OR sender_name LIKE ? OR chat_title LIKE ?)")
            pattern = f"%{query[:100]}%"
            params.extend([pattern, pattern, pattern])
        if chat_id is not None:
            clauses.append("chat_id=?")
            params.append(int(chat_id))
        if deleted is not None:
            clauses.append("deleted=?")
            params.append(int(deleted))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(f"""
                SELECT chat_title,chat_id,message_id,sender_id,sender_name,text,content_type,
                       message_date,edited,deleted,media_file_id,media_file_unique_id,media_group_id
                FROM messages {where} ORDER BY id DESC LIMIT ?
            """, (*params, min(max(int(limit), 1), 200)))
            return [dict(r) for r in await cur.fetchall()]

    async def delete_all_messages(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM messages")
            await db.execute("DELETE FROM deletions")
            await db.commit()

    async def get_connection_status(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("""
                SELECT connection_id,user_id,enabled,rights_json,updated_at
                FROM connections ORDER BY updated_at DESC
            """)
            return [dict(r) for r in await cur.fetchall()]


    async def list_people(self, limit=200):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("""
                SELECT sender_id, sender_name, COUNT(*) AS message_count,
                       MAX(message_date) AS last_seen,
                       COUNT(DISTINCT chat_id) AS chat_count
                FROM messages WHERE sender_id IS NOT NULL
                GROUP BY sender_id, sender_name
                ORDER BY last_seen DESC LIMIT ?
            """, (min(max(int(limit), 1), 500),))
            return [dict(r) for r in await cur.fetchall()]

    async def list_media(self, limit=200):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("""
                SELECT chat_title,chat_id,message_id,sender_id,sender_name,
                       content_type,text,message_date,media_file_id,media_file_unique_id
                FROM messages WHERE media_file_id IS NOT NULL
                ORDER BY id DESC LIMIT ?
            """, (min(max(int(limit), 1), 500),))
            return [dict(r) for r in await cur.fetchall()]
