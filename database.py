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
        game_ban INTEGER DEFAULT 0,
        badge TEXT DEFAULT ''
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
        "ALTER TABLE users ADD COLUMN badge TEXT DEFAULT ''",
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

# ----------------- Новые функции для управления лобби и матчами -----------------

def get_lobbies_with_players():
    """Возвращает список лобби (id, mode, thread_id, количество игроков),
       в которых есть хотя бы один зарегистрированный игрок."""
    c.execute("""
        SELECT l.id, l.mode, l.thread_id, COUNT(lr.user_id)
        FROM lobbies l
        INNER JOIN lobby_registrations lr ON l.id = lr.lobby_id
        GROUP BY l.id
        HAVING COUNT(lr.user_id) > 0
    """)
    return c.fetchall()

def get_players_in_lobby(lobby_id):
    """Возвращает список (user_id, nickname) игроков в указанном лобби."""
    c.execute("""
        SELECT u.user_id, u.nickname
        FROM lobby_registrations lr
        JOIN users u ON lr.user_id = u.user_id
        WHERE lr.lobby_id = ?
    """, (lobby_id,))
    return c.fetchall()

def remove_player_from_lobby(lobby_id, user_id):
    """Удаляет игрока из лобби."""
    c.execute("DELETE FROM lobby_registrations WHERE lobby_id = ? AND user_id = ?",
              (lobby_id, user_id))
    conn.commit()

def add_player_to_lobby(lobby_id, user_id):
    """Добавляет игрока в лобби, если он не забанен, не имеет игрового бана и ещё не в лобби.
       Возвращает True при успехе, иначе False."""
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
    """Возвращает список активных матчей (id, lobby_id, match_number, map_name, mode, created_at),
       где пользователь является хостом и статус = 'drawn'."""
    c.execute("""
        SELECT m.id, m.lobby_id, m.match_number, l.map_name, l.mode, m.created_at
        FROM matches m
        JOIN lobbies l ON m.lobby_id = l.id
        WHERE m.host_id = ? AND m.status = 'drawn'
        ORDER BY m.created_at DESC
    """, (user_id,))
    return c.fetchall()

def cancel_match(match_id):
    """Отменяет матч (ставит статус 'cancelled')."""
    c.execute("UPDATE matches SET status = 'cancelled' WHERE id = ?", (match_id,))
    conn.commit()

def get_match_info(match_id):
    """Возвращает (match_id, lobby_id, match_number, map_name, mode, host_id, status)
       для указанного матча."""
    c.execute("""
        SELECT m.id, m.lobby_id, m.match_number, l.map_name, l.mode, m.host_id, m.status
        FROM matches m
        JOIN lobbies l ON m.lobby_id = l.id
        WHERE m.id = ?
    """, (match_id,))
    return c.fetchone()

# ----------------- Функции для бейджей -----------------

def get_user_badge(user_id):
    c.execute("SELECT badge FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else ""

def set_user_badge(user_id, badge):
    c.execute("UPDATE users SET badge=? WHERE user_id=?", (badge, user_id))
    conn.commit()

# ----------------- Функции для DUO -----------------

def get_duo_partner(user_id):
    """Возвращает user_id партнёра по дуо, либо None."""
    c.execute("SELECT friend_nickname FROM duos WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        return None
    friend_nick = row[0]
    # Ищем партнёра по никнейму
    c.execute("SELECT user_id FROM users WHERE nickname=?", (friend_nick,))
    row2 = c.fetchone()
    return row2[0] if row2 else None

def remove_duo(user_id):
    """Удаляет дуо-связку для обоих участников."""
    # Получаем партнёра
    c.execute("SELECT friend_nickname FROM duos WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row:
        partner_nick = row[0]
        # Удаляем запись у инициатора
        c.execute("DELETE FROM duos WHERE user_id=?", (user_id,))
        # Удаляем запись у партнёра (если он есть в БД)
        c.execute("DELETE FROM duos WHERE user_id IN (SELECT user_id FROM users WHERE nickname=?)", (partner_nick,))
        conn.commit()

# ----------------- Функции для управления результатами -----------------

def get_all_finished_matches():
    """Возвращает список всех завершённых матчей (status='finished')."""
    c.execute("""
        SELECT m.id, m.lobby_id, m.match_number, l.map_name, l.mode, m.host_id, m.score
        FROM matches m
        JOIN lobbies l ON m.lobby_id = l.id
        WHERE m.status = 'finished'
        ORDER BY m.created_at DESC
    """)
    return c.fetchall()

def update_match_score(match_id, new_ct_score, new_t_score):
    """Обновляет счёт матча и пересчитывает ELO."""
    # Получаем старый счёт
    c.execute("SELECT score FROM matches WHERE id=?", (match_id,))
    row = c.fetchone()
    if not row or not row[0]:
        return False
    old_score = row[0].split('-')
    old_ct = int(old_score[0])
    old_t = int(old_score[1])

    # Обновляем счёт
    c.execute("UPDATE matches SET score=? WHERE id=?", (f"{new_ct_score}-{new_t_score}", match_id))

    # Получаем игроков матча
    c.execute("SELECT user_id, team FROM match_players WHERE match_id=?", (match_id,))
    players = c.fetchall()

    # Сначала откатываем старые изменения ELO
    for uid, team in players:
        is_winner_old = (team == 'CT' and old_ct > old_t) or (team == 'T' and old_t > old_ct)
        premium = is_premium(uid)
        delta_old = (50 if premium else 25) if is_winner_old else (-15 if premium else -10)
        current_elo = get_elo(uid)
        # Откатываем: вычитаем то, что добавили
        c.execute("UPDATE users SET elo = ?, wins = CASE WHEN ? THEN wins - 1 ELSE wins END, losses = CASE WHEN ? THEN losses ELSE losses - 1 END WHERE user_id=?",
                  (max(0, current_elo - delta_old), is_winner_old, is_winner_old, uid))

    # Начисляем ELO заново
    for uid, team in players:
        is_winner_new = (team == 'CT' and new_ct_score > new_t_score) or (team == 'T' and new_t_score > new_ct_score)
        premium = is_premium(uid)
        delta_new = (50 if premium else 25) if is_winner_new else (-15 if premium else -10)
        new_elo = max(0, get_elo(uid) + delta_new)
        if is_winner_new:
            c.execute("UPDATE users SET wins = wins + 1, elo = ? WHERE user_id=?", (new_elo, uid))
        else:
            c.execute("UPDATE users SET losses = losses + 1, elo = ? WHERE user_id=?", (new_elo, uid))

    conn.commit()
    return True
