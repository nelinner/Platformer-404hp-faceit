import sqlite3
import os
from datetime import datetime, timedelta
from config import OWNER_ID

# Уникальное имя файла базы данных, чтобы не пересекаться с другими ботами
DB_FILENAME = "hp404faceit_bot.db"
# База будет лежать рядом с database.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, DB_FILENAME))

print(f"Используется база данных: {DB_PATH}")

conn = sqlite3.connect(
    DB_PATH,
    timeout=30,
    check_same_thread=False,
)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=30000")
conn.execute("PRAGMA foreign_keys=ON")
c = conn.cursor()

def _parse_datetime(value):
    """Parse a stored ISO timestamp safely. Returns None for invalid/empty values."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None

def column_exists(table, column):
    """Проверяет, существует ли столбец в таблице."""
    c.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in c.fetchall())

def init_db():
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
        premium_until TEXT,
        game_ban INTEGER DEFAULT 0,
        badge TEXT DEFAULT '',
        balance INTEGER DEFAULT 0,
        coins INTEGER DEFAULT 0,
        custom_avatar TEXT,
        custom_banner TEXT
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
        lobby_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        joined_at TEXT NOT NULL,
        PRIMARY KEY (lobby_id, user_id),
        FOREIGN KEY (lobby_id) REFERENCES lobbies(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
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
        match_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        team TEXT NOT NULL,
        PRIMARY KEY (match_id, user_id),
        FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
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
    CREATE TABLE IF NOT EXISTS referrals (
        invited_id INTEGER PRIMARY KEY,
        inviter_id INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS promocodes (
        code TEXT PRIMARY KEY,
        reward_type TEXT NOT NULL,
        reward_value INTEGER NOT NULL,
        max_uses INTEGER DEFAULT 1,
        used_count INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS promo_uses (
        user_id INTEGER NOT NULL,
        code TEXT NOT NULL,
        PRIMARY KEY (user_id, code),
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
        FOREIGN KEY (code) REFERENCES promocodes(code) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS user_frames (
        user_id INTEGER PRIMARY KEY,
        frame_name TEXT NOT NULL
    );
    """)
    conn.commit()

    # Безопасное добавление недостающих столбцов
    migrations = [
        ("users", "custom_avatar", "TEXT"),
        ("users", "custom_banner", "TEXT"),
        ("users", "premium", "INTEGER DEFAULT 0"),
        ("users", "wins", "INTEGER DEFAULT 0"),
        ("users", "losses", "INTEGER DEFAULT 0"),
        ("users", "game_ban", "INTEGER DEFAULT 0"),
        ("users", "badge", "TEXT DEFAULT ''"),
        ("users", "balance", "INTEGER DEFAULT 0"),
        ("users", "premium_until", "TEXT"),
        ("users", "coins", "INTEGER DEFAULT 0"),
        ("lobbies", "map_name", "TEXT"),
        ("matches", "host_id", "INTEGER"),
        ("matches", "score", "TEXT"),
    ]
    for table, column, col_type in migrations:
        if not column_exists(table, column):
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                conn.commit()
            except Exception as e:
                print(f"Ошибка миграции {table}.{column}: {e}")

    c.execute("UPDATE users SET banned_until=NULL WHERE user_id=?", (OWNER_ID,))
    conn.commit()

    c.execute("INSERT OR IGNORE INTO admins (username) VALUES ('nelinner')")
    conn.commit()

# ---------- Все функции базы данных ----------
def is_registered(user_id):
    c.execute("SELECT registered FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row is not None and row[0] == 1

def is_banned(user_id):
    c.execute("SELECT banned_until FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    until = _parse_datetime(row[0]) if row else None
    return until is not None and until > datetime.now()

def is_muted(user_id):
    c.execute("SELECT muted_until FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    until = _parse_datetime(row[0]) if row else None
    return until is not None and until > datetime.now()

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
    c.execute("SELECT premium, premium_until FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        return False

    premium, premium_until = row
    if premium_until:
        until = _parse_datetime(premium_until)
        if until is not None:
            active = until > datetime.now()
            if not active and premium:
                c.execute(
                    "UPDATE users SET premium=0 WHERE user_id=?",
                    (user_id,),
                )
                conn.commit()
            return active
        # Invalid expiration must not grant Premium.
        if premium:
            c.execute(
                "UPDATE users SET premium=0 WHERE user_id=?",
                (user_id,),
            )
            conn.commit()
        return False

    # Legacy permanent Premium is retained only when premium_until is NULL.
    return premium == 1

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
    c.execute("SELECT username FROM admins")
    admins = [row[0] for row in c.fetchall()]
    admin_ids = []
    for admin_username in admins:
        c.execute("SELECT user_id FROM users WHERE username=?", (admin_username,))
        row = c.fetchone()
        if row:
            admin_ids.append(row[0])
    return admin_ids

def get_lobbies_with_players():
    c.execute("""
        SELECT l.id, l.mode, l.thread_id, COUNT(lr.user_id)
        FROM lobbies l
        INNER JOIN lobby_registrations lr ON l.id = lr.lobby_id
        GROUP BY l.id
        HAVING COUNT(lr.user_id) > 0
    """)
    return c.fetchall()

def get_players_in_lobby(lobby_id):
    c.execute("""
        SELECT u.user_id, u.nickname
        FROM lobby_registrations lr
        JOIN users u ON lr.user_id = u.user_id
        WHERE lr.lobby_id = ?
    """, (lobby_id,))
    return c.fetchall()

def remove_player_from_lobby(lobby_id, user_id):
    c.execute("DELETE FROM lobby_registrations WHERE lobby_id = ? AND user_id = ?",
              (lobby_id, user_id))
    conn.commit()

def add_player_to_lobby(lobby_id, user_id):
    if is_banned(user_id) or is_game_banned(user_id):
        return False
    c.execute("SELECT 1 FROM lobby_registrations WHERE lobby_id=? AND user_id=?", (lobby_id, user_id))
    if c.fetchone():
        return False
    c.execute("INSERT INTO lobby_registrations (lobby_id, user_id, joined_at) VALUES (?, ?, ?)",
              (lobby_id, user_id, datetime.now().isoformat()))
    conn.commit()
    return True

def get_user_active_matches(user_id):
    c.execute("""
        SELECT m.id, m.lobby_id, m.match_number, l.map_name, l.mode, m.created_at
        FROM matches m
        JOIN lobbies l ON m.lobby_id = l.id
        WHERE m.host_id = ? AND m.status = 'drawn'
        ORDER BY m.created_at DESC
    """, (user_id,))
    return c.fetchall()

def cancel_match(match_id):
    c.execute("UPDATE matches SET status = 'cancelled' WHERE id = ?", (match_id,))
    conn.commit()

def get_match_info(match_id):
    c.execute("""
        SELECT m.id, m.lobby_id, m.match_number, l.map_name, l.mode, m.host_id, m.status
        FROM matches m
        JOIN lobbies l ON m.lobby_id = l.id
        WHERE m.id = ?
    """, (match_id,))
    return c.fetchone()

def get_user_badge(user_id):
    c.execute("SELECT badge FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else ""

def set_user_badge(user_id, badge):
    c.execute("UPDATE users SET badge=? WHERE user_id=?", (badge, user_id))
    conn.commit()

def get_duo_partner(user_id):
    c.execute("SELECT friend_nickname FROM duos WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        return None
    friend_nick = row[0]
    c.execute("SELECT user_id FROM users WHERE nickname=?", (friend_nick,))
    row2 = c.fetchone()
    return row2[0] if row2 else None

def remove_duo(user_id):
    c.execute("SELECT friend_nickname FROM duos WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row:
        partner_nick = row[0]
        c.execute("DELETE FROM duos WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM duos WHERE user_id IN (SELECT user_id FROM users WHERE nickname=?)", (partner_nick,))
        conn.commit()

def get_all_finished_matches():
    c.execute("""
        SELECT m.id, m.lobby_id, m.match_number, l.map_name, l.mode, m.host_id, m.score
        FROM matches m
        JOIN lobbies l ON m.lobby_id = l.id
        WHERE m.status = 'finished'
        ORDER BY m.created_at DESC
    """)
    return c.fetchall()

def update_match_score(match_id, new_ct_score, new_t_score):
    c.execute("SELECT score FROM matches WHERE id=?", (match_id,))
    row = c.fetchone()
    if not row or not row[0]:
        return False
    old_score = row[0].split('-')
    old_ct = int(old_score[0])
    old_t = int(old_score[1])

    # Откат старого результата
    c.execute("SELECT user_id, team FROM match_players WHERE match_id=?", (match_id,))
    players = c.fetchall()
    for uid, team in players:
        is_winner_old = (team == 'CT' and old_ct > old_t) or (team == 'T' and old_t > old_ct)
        premium = is_premium(uid)
        delta_old = (50 if premium else 25) if is_winner_old else (-15 if premium else -10)
        current_elo = get_elo(uid)
        new_elo = max(0, current_elo - delta_old)
        c.execute("UPDATE users SET elo=? WHERE user_id=?", (new_elo, uid))
        if is_winner_old:
            c.execute("UPDATE users SET wins = wins - 1 WHERE user_id=?", (uid,))
        else:
            c.execute("UPDATE users SET losses = losses - 1 WHERE user_id=?", (uid,))

    # Применение нового результата
    for uid, team in players:
        is_winner_new = (team == 'CT' and new_ct_score > new_t_score) or (team == 'T' and new_t_score > new_ct_score)
        premium = is_premium(uid)
        delta_new = (50 if premium else 25) if is_winner_new else (-15 if premium else -10)
        new_elo = max(0, get_elo(uid) + delta_new)
        c.execute("UPDATE users SET elo=? WHERE user_id=?", (new_elo, uid))
        if is_winner_new:
            c.execute("UPDATE users SET wins = wins + 1 WHERE user_id=?", (uid,))
        else:
            c.execute("UPDATE users SET losses = losses + 1 WHERE user_id=?", (uid,))

    c.execute("UPDATE matches SET score=? WHERE id=?", (f"{new_ct_score}-{new_t_score}", match_id))
    conn.commit()
    return True

def create_referral(invited_id, inviter_id):
    try:
        c.execute("INSERT OR IGNORE INTO referrals (invited_id, inviter_id) VALUES (?, ?)", (invited_id, inviter_id))
        conn.commit()
        return c.rowcount > 0
    except:
        return False

def get_referral_count(user_id):
    c.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else 0

def get_balance(user_id):
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else 0

def add_balance(user_id, amount):
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def add_premium_days(user_id, days):
    if days <= 0:
        return False

    c.execute("SELECT premium_until FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    now = datetime.now()
    current = _parse_datetime(row[0]) if row and row[0] else None

    if current is None or current < now:
        current = now

    new_until = current + timedelta(days=days)
    c.execute(
        "UPDATE users SET premium=1, premium_until=? WHERE user_id=?",
        (new_until.isoformat(), user_id),
    )
    conn.commit()
    return True

def get_coins(user_id):
    c.execute("SELECT coins FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else 0

def add_coins(user_id, amount):
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def get_user_frame(user_id):
    c.execute("SELECT frame_name FROM user_frames WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else None

def set_user_frame(user_id, frame_name):
    c.execute("INSERT INTO user_frames (user_id, frame_name) VALUES (?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET frame_name=excluded.frame_name",
              (user_id, frame_name))
    conn.commit()

def create_promo(code, reward_type, reward_value, max_uses=1):
    c.execute("INSERT OR REPLACE INTO promocodes (code, reward_type, reward_value, max_uses) VALUES (?, ?, ?, ?)",
              (code.upper(), reward_type, reward_value, max_uses))
    conn.commit()

def use_promo(user_id, code):
    code = code.strip().upper()
    if not code:
        return False, "Введите промокод."

    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "SELECT reward_type, reward_value, max_uses, used_count "
            "FROM promocodes WHERE code=?",
            (code,),
        )
        promo = c.fetchone()
        if not promo:
            conn.rollback()
            return False, "Промокод не найден."

        reward_type, reward_value, max_uses, used_count = promo
        if max_uses is not None and used_count >= max_uses:
            conn.rollback()
            return False, "Промокод исчерпан."

        c.execute(
            "SELECT 1 FROM promo_uses WHERE user_id=? AND code=?",
            (user_id, code),
        )
        if c.fetchone():
            conn.rollback()
            return False, "Вы уже использовали этот промокод."

        if reward_type == "premium":
            if reward_value <= 0:
                conn.rollback()
                return False, "Некорректная награда промокода."
            now = datetime.now()
            c.execute(
                "SELECT premium_until FROM users WHERE user_id=?",
                (user_id,),
            )
            row = c.fetchone()
            current = _parse_datetime(row[0]) if row and row[0] else None
            if current is None or current < now:
                current = now
            new_until = current + timedelta(days=reward_value)
            c.execute(
                "UPDATE users SET premium=1, premium_until=? WHERE user_id=?",
                (new_until.isoformat(), user_id),
            )
            message = f"Вам начислено {reward_value} дней премиума."
        elif reward_type == "coins":
            c.execute(
                "UPDATE users SET coins = coins + ? WHERE user_id=?",
                (reward_value, user_id),
            )
            message = f"Вам начислено {reward_value} Coins."
        elif reward_type == "balance":
            c.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id=?",
                (reward_value, user_id),
            )
            message = f"Вам начислено {reward_value} на баланс."
        else:
            conn.rollback()
            return False, "Неизвестный тип награды."

        c.execute(
            "UPDATE promocodes SET used_count = used_count + 1 "
            "WHERE code=? AND (max_uses IS NULL OR used_count < max_uses)",
            (code,),
        )
        if c.rowcount != 1:
            conn.rollback()
            return False, "Промокод уже исчерпан."

        c.execute(
            "INSERT INTO promo_uses (user_id, code) VALUES (?, ?)",
            (user_id, code),
        )
        conn.commit()
        return True, message
    except sqlite3.IntegrityError:
        conn.rollback()
        return False, "Вы уже использовали этот промокод."
    except sqlite3.Error:
        conn.rollback()
        raise
