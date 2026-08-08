# database.py
import sqlite3
from datetime import datetime, timedelta
from config import OWNER_ID

# Глобальное подключение (один раз при импорте модуля)
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
c = conn.cursor()

def init_db():
    """Создаёт таблицы, выполняет миграции и начальные вставки."""
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        nickname TEXT,
        standoff_id TEXT,
        elo INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        registered INTEGER DEFAULT 1,
        banned_until TEXT,
        muted_until TEXT,
        premium INTEGER DEFAULT 0,
        game_ban INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS lobbies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT NOT NULL,
        thread_id INTEGER NOT NULL,
        match_number INTEGER DEFAULT 1,
        message_id INTEGER,
        map_name TEXT,
        UNIQUE(mode, thread_id)
    );
    CREATE TABLE IF NOT EXISTS lobby_registrations (
        lobby_id INTEGER,
        user_id INTEGER,
        joined_at TEXT,
        PRIMARY KEY (lobby_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lobby_id INTEGER,
        match_number INTEGER,
        status TEXT DEFAULT 'waiting',
        created_at TEXT,
        host_id INTEGER,
        score TEXT
    );
    CREATE TABLE IF NOT EXISTS match_players (
        match_id INTEGER,
        user_id INTEGER,
        team TEXT
    );
    CREATE TABLE IF NOT EXISTS admins (
        username TEXT PRIMARY KEY
    );
    CREATE TABLE IF NOT EXISTS duos (
        user_id INTEGER,
        friend_nickname TEXT,
        PRIMARY KEY (user_id, friend_nickname)
    );
    CREATE TABLE IF NOT EXISTS player_maps (
        user_id INTEGER,
        map_name TEXT,
        count INTEGER DEFAULT 1,
        PRIMARY KEY (user_id, map_name)
    );
    CREATE TABLE IF NOT EXISTS duo_requests (
        sender_id INTEGER,
        receiver_id INTEGER,
        PRIMARY KEY (sender_id, receiver_id)
    );
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id INTEGER NOT NULL,
        reporter_username TEXT,
        target_id INTEGER,
        target_username TEXT NOT NULL,
        report_text TEXT NOT NULL,
        message_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        problem_text TEXT NOT NULL,
        screenshot_file_id TEXT,
        status TEXT DEFAULT 'open',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()

    # Сбрасываем бан владельцу
    c.execute("UPDATE users SET banned_until=NULL WHERE user_id=?", (OWNER_ID,))
    conn.commit()

    # Миграции (добавление отсутствующих столбцов, если потребуется)
    migrations = [
        "ALTER TABLE lobbies ADD COLUMN map_name TEXT",
        "ALTER TABLE users ADD COLUMN custom_avatar TEXT",
        "ALTER TABLE users ADD COLUMN custom_banner TEXT",
        "ALTER TABLE matches ADD COLUMN host_id INTEGER",
        "ALTER TABLE matches ADD COLUMN score TEXT",
        "ALTER TABLE users ADD COLUMN premium INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN wins INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN losses INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN game_ban INTEGER DEFAULT 0",
    ]
    for mig in migrations:
        try:
            c.execute(mig)
            conn.commit()
        except:
            pass

    # Главный администратор
    c.execute("INSERT OR IGNORE INTO admins (username) VALUES ('nelinner')")
    conn.commit()

# ------------------------------------------------------------
# Вспомогательные функции для работы с БД
# ------------------------------------------------------------

def is_registered(user_id):
    c.execute("SELECT registered FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row is not None and row[0] == 1

def is_banned(user_id):
    c.execute("SELECT banned_until FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row is not None and row[0] and datetime.fromisoformat(row[0]) > datetime.now()

def is_muted(user_id):
    c.execute("SELECT muted_until FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row is not None and row[0] and datetime.fromisoformat(row[0]) > datetime.now()

def is_game_banned(user_id):
    c.execute("SELECT game_ban FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row and row[0] == 1

def is_admin(username):
    if not username:
        return False
    c.execute("SELECT 1 FROM admins WHERE username=?", (username.lower().lstrip('@'),))
    return c.fetchone() is not None

def is_premium(user_id):
    c.execute("SELECT premium FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row and row[0] == 1

def get_elo(user_id):
    c.execute("SELECT elo FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else 0

def get_nickname(user_id):
    c.execute("SELECT nickname FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else "Unknown"

def get_standoff_id(user_id):
    c.execute("SELECT standoff_id FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else "0"

def get_user_stats(user_id):
    c.execute("SELECT elo, wins, losses FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return (row[0], row[1] if row[1] else 0, row[2] if row[2] else 0) if row else (0, 0, 0)

def get_best_map(user_id):
    c.execute("SELECT map_name FROM player_maps WHERE user_id=? ORDER BY count DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    return row[0] if row else None

def increment_map_count(user_id, map_name):
    if not map_name:
        return
    c.execute("""INSERT INTO player_maps (user_id, map_name, count) VALUES (?, ?, 1)
                 ON CONFLICT(user_id, map_name) DO UPDATE SET count = count + 1""",
              (user_id, map_name))
    conn.commit()

def find_user(identifier: str):
    """Возвращает user_id по @username или никнейму, либо None.
       Временная защита: никогда не возвращает OWNER_ID,
       если только не ищется сам 'nelinner'."""
    if identifier.startswith("@"):
        username = identifier.lstrip("@").lower()
        if username != "nelinner":
            c.execute("SELECT user_id FROM users WHERE username=? AND user_id != ?", (username, OWNER_ID))
            row = c.fetchone()
            if row:
                return row[0]
            return None
        else:
            c.execute("SELECT user_id FROM users WHERE username=?", (username,))
            row = c.fetchone()
            return row[0] if row else None
    else:
        # Поиск по никнейму
        c.execute("SELECT user_id FROM users WHERE nickname=? AND user_id != ?", (identifier, OWNER_ID))
        row = c.fetchone()
        if row:
            return row[0]
        c.execute("SELECT user_id FROM users WHERE nickname=? AND user_id = ?", (identifier, OWNER_ID))
        row = c.fetchone()
        return row[0] if row else None

def save_report(reporter_id, reporter_username, target_id, target_username, report_text, message_id=None):
    c.execute("""INSERT INTO reports (reporter_id, reporter_username, target_id, target_username, report_text, message_id)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (reporter_id, reporter_username, target_id, target_username, report_text, message_id))
    conn.commit()
    return c.lastrowid

def save_ticket(user_id, username, problem_text, screenshot_file_id=None):
    c.execute("""INSERT INTO tickets (user_id, username, problem_text, screenshot_file_id)
                 VALUES (?, ?, ?, ?)""",
              (user_id, username, problem_text, screenshot_file_id))
    conn.commit()
    return c.lastrowid

def get_ticket(ticket_id):
    c.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    return c.fetchone()

def update_ticket_status(ticket_id, status):
    c.execute("UPDATE tickets SET status=? WHERE id=?", (status, ticket_id))
    conn.commit()

def get_report(report_id):
    c.execute("""SELECT reporter_id, target_id, target_username, report_text
                 FROM reports WHERE id=?""", (report_id,))
    return c.fetchone()

def update_report_target_id(report_id, target_id):
    c.execute("UPDATE reports SET target_id=? WHERE id=?", (target_id, report_id))
    conn.commit()

def get_admin_ids():
    """Возвращает список user_id всех администраторов."""
    c.execute("SELECT username FROM admins")
    admins = [row[0] for row in c.fetchall()]
    admin_ids = []
    for admin_username in admins:
        c.execute("SELECT user_id FROM users WHERE username=?", (admin_username,))
        row = c.fetchone()
        if row:
            admin_ids.append(row[0])
    return admin_ids
