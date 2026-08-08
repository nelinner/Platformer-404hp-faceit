# bot.py
import asyncio
import logging
import random
import os
import requests
from datetime import datetime, timedelta
from io import BytesIO
from math import pi, cos, sin
from typing import Optional
from html import escape as html_escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BufferedInputFile,
    InputMediaPhoto,
    ChatPermissions,
    ForceReply,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# Импорт конфигурации и базы данных
from config import *
from database import (
    conn, c, init_db,
    is_registered, is_banned, is_muted, is_game_banned,
    is_admin, is_premium, get_elo, get_nickname, get_standoff_id,
    get_user_stats, get_best_map, increment_map_count,
    find_user, save_report, save_ticket, get_ticket, update_ticket_status,
    get_report, update_report_target_id, get_admin_ids
)

# ------------------------------------------------------------
# Глобальные объекты
# ------------------------------------------------------------
dp = Dispatcher(storage=MemoryStorage())
report_router = Router()
bot: Bot = None

# Кэши и временные словари
font_cache = {}
map_images_cache = {}
avatar_cache = {}
banner_cache = {}
last_report_time = {}

# ------------------------------------------------------------
# Вспомогательные функции (изображения, загрузка ресурсов)
# ------------------------------------------------------------
def get_font(size):
    try:
        if size not in font_cache:
            font_cache[size] = ImageFont.truetype(FONT_PATH, size)
    except:
        if size not in font_cache:
            font_cache[size] = ImageFont.load_default()
    return font_cache[size]

def draw_ring(draw, cx, cy, r, width, percent, color, bg_color):
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=bg_color, width=width)
    if percent <= 0: return
    steps = int(50 * percent / 100)
    for i in range(steps):
        start_ang = -90 + (i * 360 / 100 * percent / steps)
        end_ang = start_ang + 3
        x1 = cx + r * cos(start_ang * pi / 180)
        y1 = cy + r * sin(start_ang * pi / 180)
        x2 = cx + r * cos(end_ang * pi / 180)
        y2 = cy + r * sin(end_ang * pi / 180)
        draw.line([(x1, y1), (x2, y2)], fill=color, width=width)

def fit_image(img, w, h):
    iw, ih = img.size
    ratio = max(w / iw, h / ih)
    new_size = (int(iw * ratio), int(ih * ratio))
    img = img.resize(new_size, Image.Resampling.LANCZOS)
    left = (new_size[0] - w) // 2
    top = (new_size[1] - h) // 2
    return img.crop((left, top, left + w, top + h))

def download_map_image(map_name):
    if map_name in map_images_cache:
        return map_images_cache[map_name].copy()
    return None

def load_map_images():
    for name, url in MAP_URLS.items():
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                img = Image.open(BytesIO(resp.content)).convert("RGBA")
                map_images_cache[name] = img
        except Exception as e:
            logging.error(f"Не удалось загрузить карту {name}: {e}")

def download_font():
    if os.path.exists(FONT_PATH): return
    try:
        r = requests.get(FONT_URL, timeout=15)
        if r.status_code == 200 and len(r.content) > 1000:
            with open(FONT_PATH, "wb") as f:
                f.write(r.content)
    except: pass

def download_default_banner():
    if os.path.exists(DEFAULT_BANNER_PATH): return
    try:
        r = requests.get(DEFAULT_BANNER_URL, timeout=15)
        if r.status_code == 200 and len(r.content) > 1000:
            with open(DEFAULT_BANNER_PATH, "wb") as f:
                f.write(r.content)
    except: pass

async def check_subscription(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status not in [ChatMemberStatus.LEFT, ChatMemberStatus.RESTRICTED]
    except: return False

async def get_user_avatar(bot: Bot, user_id: int) -> Optional[BytesIO]:
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count > 0:
            file = await bot.get_file(photos.photos[0][-1].file_id)
            bio = BytesIO()
            await bot.download_file(file.file_path, bio)
            bio.seek(0)
            return bio
    except: return None

async def get_custom_avatar(bot: Bot, user_id: int) -> Optional[BytesIO]:
    c.execute("SELECT custom_avatar FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row and row[0]:
        try:
            file = await bot.get_file(row[0])
            bio = BytesIO()
            await bot.download_file(file.file_path, bio)
            bio.seek(0)
            return bio
        except: pass
    return None

async def get_custom_banner(bot: Bot, user_id: int) -> Optional[BytesIO]:
    c.execute("SELECT custom_banner FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row and row[0]:
        try:
            file = await bot.get_file(row[0])
            bio = BytesIO()
            await bot.download_file(file.file_path, bio)
            bio.seek(0)
            return bio
        except: pass
    return None

async def get_avatar_image_cached(bot: Bot, user_id: int) -> Optional[Image.Image]:
    if user_id in avatar_cache:
        return avatar_cache[user_id]
    custom = await get_custom_avatar(bot, user_id)
    if custom:
        img = Image.open(custom).convert("RGBA")
    else:
        tg = await get_user_avatar(bot, user_id)
        if tg:
            img = Image.open(tg).convert("RGBA")
        else:
            img = None
    avatar_cache[user_id] = img
    return img

async def get_banner_cached(bot: Bot, user_id: int) -> Optional[Image.Image]:
    if user_id in banner_cache:
        return banner_cache[user_id]
    bn = await get_custom_banner(bot, user_id)
    if bn:
        img = Image.open(bn).convert("RGBA")
    else:
        if os.path.exists(DEFAULT_BANNER_PATH):
            try:
                img = Image.open(DEFAULT_BANNER_PATH).convert("RGBA")
            except:
                img = None
        else:
            img = None
    banner_cache[user_id] = img
    return img

# ------------------------------------------------------------
# Генерация профиля и слотов
# ------------------------------------------------------------
def generate_profile_card(user_id: int, username: Optional[str] = None,
                          cached_avatar: Optional[Image.Image] = None,
                          cached_banner: Optional[Image.Image] = None) -> BytesIO:
    elo, wins, losses = get_user_stats(user_id)
    total = wins + losses
    winrate = (wins / total * 100) if total > 0 else 0
    best_map = get_best_map(user_id)
    c.execute("SELECT nickname, standoff_id FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row: row = ("Неизвестный", "0")
    nickname, standoff_id = row
    premium = is_premium(user_id)

    w, h = 800, 480
    img = Image.new("RGBA", (w, h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        ratio = y / h
        r = int(10 + 30 * ratio)
        g = int(11 + 40 * ratio)
        b = int(16 + 45 * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

    cell_h = 180
    cell_y = 15
    if cached_banner:
        try:
            banner_resized = cached_banner.resize((w-30, cell_h), Image.Resampling.LANCZOS)
            dark = Image.new('RGBA', (w-30, cell_h), (0,0,0,115))
            banner_resized = Image.alpha_composite(banner_resized, dark)
            img.paste(banner_resized, (15, cell_y))
        except: pass
    draw.rounded_rectangle([15, cell_y, w-15, cell_y+cell_h], radius=20, outline=FACEIT_ORANGE, width=4)

    avatar_size = 120
    avatar_x = 40
    avatar_cy = cell_y + cell_h//2
    if cached_avatar:
        mask = Image.new('L', (avatar_size, avatar_size), 0)
        ImageDraw.Draw(mask).ellipse((0,0,avatar_size,avatar_size), fill=255)
        avatar_img = cached_avatar.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
        img.paste(avatar_img, (avatar_x, avatar_cy - avatar_size//2), mask)
        draw.ellipse([avatar_x-5, avatar_cy-avatar_size//2-5, avatar_x+avatar_size+5, avatar_cy+avatar_size//2+5],
                     outline=FACEIT_ORANGE, width=4)

    try:
        font_nick = ImageFont.truetype(FONT_PATH, 34)
        font_id = ImageFont.truetype(FONT_PATH, 22)
    except:
        font_nick = ImageFont.load_default()
        font_id = ImageFont.load_default()
    draw.text((avatar_x + avatar_size + 20, cell_y+30), nickname, font=font_nick, fill='white')
    draw.text((avatar_x + avatar_size + 20, cell_y+80), f"ID: {standoff_id}", font=font_id, fill=TEXT_GRAY)
    if username:
        if user_id == OWNER_ID:
            badge = "OWNER"
            bg = (255,215,0)
            fg = (0,0,0)
        elif is_admin(username):
            badge = "ADMIN"
            bg = (200,0,0)
            fg = (255,255,255)
        elif premium:
            badge = "PREMIUM"
            bg = (255,215,0)
            fg = (0,0,0)
        else:
            badge = None
        if badge:
            font_badge = get_font(16)
            bbox = draw.textbbox((0,0), badge, font=font_badge)
            bw, bh = bbox[2]-bbox[0]+12, bbox[3]-bbox[1]+6
            draw.rounded_rectangle([avatar_x+avatar_size+20, cell_y+110, avatar_x+avatar_size+20+bw, cell_y+110+bh],
                                   radius=6, fill=bg)
            draw.text((avatar_x+avatar_size+26, cell_y+112), badge, font=font_badge, fill=fg)

    draw.line([(20, cell_y+cell_h+20), (w-20, cell_y+cell_h+20)], fill=FACEIT_ORANGE, width=3)

    stat_x = 30
    stat_y = cell_y+cell_h+50
    try:
        font_stat = ImageFont.truetype(FONT_PATH, 24)
        font_small = ImageFont.truetype(FONT_PATH, 18)
    except:
        font_stat = ImageFont.load_default()
        font_small = ImageFont.load_default()
    draw.text((stat_x, stat_y), "Статистика", font=font_stat, fill='white')
    draw.text((stat_x, stat_y+40), f"Матчей сыграно: {total}", font=font_small, fill=TEXT_GRAY)
    draw.text((stat_x, stat_y+65), f"Побед: {wins}", font=font_small, fill=TEXT_GRAY)
    draw.text((stat_x, stat_y+90), f"Поражений: {losses}", font=font_small, fill=TEXT_GRAY)

    bar_x = stat_x
    bar_y = stat_y + 120
    bar_w = 200
    bar_h = 14
    draw.rounded_rectangle([bar_x, bar_y, bar_x+bar_w, bar_y+bar_h], radius=7, fill=(60,60,70))
    if winrate > 0:
        fill_w = int(bar_w * winrate / 100)
        draw.rounded_rectangle([bar_x, bar_y, bar_x+fill_w, bar_y+bar_h], radius=7, fill=FACEIT_ORANGE)
    draw.text((bar_x+bar_w//2, bar_y+bar_h//2), f"{winrate:.1f}%", font=font_small, fill='white', anchor="mm")

    if best_map and best_map in map_images_cache:
        map_img = map_images_cache[best_map].copy().resize((180, 100), Image.Resampling.LANCZOS)
        map_x = w - 220
        map_y = stat_y
        draw.rounded_rectangle([map_x-8, map_y-8, map_x+188, map_y+108], radius=10, outline=FACEIT_ORANGE, width=3)
        if map_img.mode == 'RGBA':
            img.paste(map_img, (map_x, map_y), mask=map_img.split()[3])
        else:
            img.paste(map_img, (map_x, map_y))
        draw.text((map_x+90, map_y+115), f"Best map: {best_map}", font=font_small, fill='white', anchor="mt")

    img = img.filter(ImageFilter.SHARPEN)
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

def draw_player_slot(draw, x, y, w, h, nickname, elo, sid, avatar=None, banner=None,
                     is_owner=False, is_admin=False, premium=False, compact=False):
    try:
        if compact:
            avatar_r = 40
            base_font_size = 30
            font_elo_size = 24
            font_id_size = 18
            font_badge_size = 14
            overlay_h = 40
            max_width_nick = w - 130
            text_y_nick = y + 16
            text_y_elo = y + 50
            text_y_id = y + 74
        else:
            avatar_r = 54
            base_font_size = 38
            font_elo_size = 28
            font_id_size = 22
            font_badge_size = 16
            overlay_h = 50
            max_width_nick = w - 180
            text_y_nick = y + 22
            text_y_elo = y + 68
            text_y_id = y + 100

        if banner:
            try:
                bg_img = banner.resize((w, h), Image.Resampling.LANCZOS)
                dark = Image.new('RGBA', (w, h), (0,0,0,153))
                bg_img = Image.alpha_composite(bg_img, dark)
                draw._image.paste(bg_img, (x, y))
            except:
                draw.rounded_rectangle([x, y, x+w, y+h], radius=18, fill=(22,25,35), outline=(55,60,75), width=2)
        else:
            draw.rounded_rectangle([x, y, x+w, y+h], radius=18, fill=(22,25,35), outline=(55,60,75), width=2)

        draw.rounded_rectangle([x+3, y+3, x+w-3, y+h-3], radius=16, outline=(255,160,50,75), width=3)

        overlay = Image.new('RGBA', (w, overlay_h))
        for oy in range(overlay_h):
            alpha = int(40 * (1 - oy / overlay_h))
            for ox in range(w):
                overlay.putpixel((ox, oy), (0,0,0,alpha))
        draw._image.paste(overlay, (x, y + h - overlay_h))

        avatar_cx = x + (avatar_r + 18)
        avatar_cy = y + h//2
        if avatar:
            mask = Image.new('L', (avatar_r*2, avatar_r*2), 0)
            ImageDraw.Draw(mask).ellipse((0,0,avatar_r*2,avatar_r*2), fill=255)
            avatar_resized = avatar.resize((avatar_r*2, avatar_r*2), Image.Resampling.LANCZOS)
            draw._image.paste(avatar_resized, (avatar_cx-avatar_r, avatar_cy-avatar_r), mask)
            draw.ellipse([avatar_cx-avatar_r-2, avatar_cy-avatar_r-2, avatar_cx+avatar_r+2, avatar_cy+avatar_r+2],
                         outline=FACEIT_ORANGE, width=3)
        else:
            draw.ellipse([avatar_cx-avatar_r, avatar_cy-avatar_r, avatar_cx+avatar_r, avatar_cy+avatar_r],
                         fill=(35,38,55), outline=FACEIT_ORANGE, width=3)

        nick = nickname[:18]
        display_name = nick

        font_size = base_font_size
        font_nick = get_font(font_size)
        while font_size > 14:
            bbox = draw.textbbox((0,0), display_name, font=font_nick)
            if (bbox[2]-bbox[0]) <= max_width_nick:
                break
            font_size -= 2
            font_nick = get_font(font_size)

        if is_admin or is_owner or premium:
            badge_text = "OWNER" if is_owner else ("ADMIN" if is_admin else "PREMIUM")
            badge_bg = (255, 215, 0, 200)
            badge_fg = (0, 0, 0)
            if is_admin and not is_owner:
                badge_bg = (200, 0, 0, 200)
                badge_fg = (255, 255, 255)
            font_badge = get_font(font_badge_size)
            nick_bbox = draw.textbbox((0,0), display_name, font=font_nick)
            nick_w = nick_bbox[2] - nick_bbox[0]
            badge_bbox = draw.textbbox((0,0), badge_text, font=font_badge)
            badge_w = badge_bbox[2] - badge_bbox[0] + 12
            badge_h = badge_bbox[3] - badge_bbox[1] + 6
            badge_x = avatar_cx + avatar_r + 20 + nick_w + 6
            badge_y = text_y_nick - 4
            draw.rounded_rectangle([badge_x, badge_y, badge_x+badge_w, badge_y+badge_h], radius=6, fill=badge_bg)
            draw.text((badge_x+6, badge_y+2), badge_text, fill=badge_fg, font=font_badge)

        draw.text((avatar_cx + avatar_r + 16, text_y_nick), display_name, font=font_nick, fill='white')
        font_elo = get_font(font_elo_size)
        draw.text((avatar_cx + avatar_r + 16, text_y_elo), f"{elo} ELO", font=font_elo, fill='#b0d0ff')
        font_id = get_font(font_id_size)
        draw.text((avatar_cx + avatar_r + 16, text_y_id), f"ID: {sid}", font=font_id, fill=TEXT_GRAY)

    except Exception as e:
        logging.error(f"draw_player_slot error: {e}")

def draw_lobby_background(draw, width, height):
    for y in range(height):
        ratio = y / height
        r = int(10 + 30 * ratio)
        g = int(11 + 40 * ratio)
        b = int(16 + 45 * ratio)
        draw.rectangle([(0, y), (width, y+1)], fill=(r, g, b, 255))

async def generate_lobby_image(bot: Bot, lobby_id: int) -> Optional[BytesIO]:
    try:
        c.execute("SELECT mode, map_name FROM lobbies WHERE id=?", (lobby_id,))
        lobby = c.fetchone()
        if not lobby: return None
        mode, map_name = lobby

        c.execute("SELECT user_id FROM lobby_registrations WHERE lobby_id=? ORDER BY joined_at", (lobby_id,))
        player_ids = [row[0] for row in c.fetchall()]
        filled = len(player_ids)
        maxp = MAX_PLAYERS[mode]
        percent = int((filled / maxp) * 100) if maxp else 0

        width, height = 1720, 1080
        img = Image.new('RGBA', (width, height), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        draw_lobby_background(draw, width, height)

        draw.rounded_rectangle([18,18,width-18,height-18], radius=36, outline=FACEIT_ORANGE, width=6)
        draw.rounded_rectangle([26,26,width-26,height-26], radius=30, outline=(255,255,255,38), width=2)

        title1 = "404hp "
        title2 = "FACEIT"
        font_title = get_font(48)
        w1 = draw.textlength(title1, font=font_title)
        w2 = draw.textlength(title2, font=font_title)
        total_w = w1 + w2
        right_margin = width - 50 - total_w
        draw.text((right_margin, 38), title1, fill='white', font=font_title)
        draw.text((right_margin + w1, 38), title2, fill=FACEIT_ORANGE, font=font_title)

        draw.text((52,38), "MATCHMAKING", font=get_font(96), fill=FACEIT_ORANGE)
        draw.text((52,130), f"{mode} • COMPETITIVE", font=get_font(46), fill="#e0e0e0")

        map_x, map_y = 48, 205
        map_w, map_h = 780, 435
        draw.rounded_rectangle([map_x, map_y, map_x+map_w, map_y+map_h], radius=22, fill=(18,20,30), outline=(255,255,255,55), width=3)
        map_img = download_map_image(map_name)
        if map_img:
            map_img = fit_image(map_img, map_w, map_h)
            img.paste(map_img, (map_x, map_y))
        else:
            draw.rectangle([map_x, map_y, map_x+map_w, map_y+map_h], fill=(16,18,26))

        overlay = Image.new('RGBA', (map_w, 60), (0,0,0,200))
        odraw = ImageDraw.Draw(overlay)
        map_text = f"CURRENT MAP: {map_name.upper() if map_name else 'NO MAP'}"
        odraw.text((20, 10), map_text, font=get_font(28), fill='#ffddaa')
        img.paste(overlay, (map_x, map_y + map_h - 60))

        prog_x, prog_y = 48, 670
        prog_w, prog_h = 780, 158
        draw.rounded_rectangle([prog_x, prog_y, prog_x+prog_w, prog_y+prog_h], radius=22, fill=(18,20,30), outline=(255,255,255,45), width=3)
        draw_ring(draw, prog_x+112, prog_y+79, 72, 19, percent, FACEIT_ORANGE, (30,33,45))
        draw.text((prog_x+112, prog_y+79), f"{percent}%", font=get_font(56), fill='white', anchor='mm')
        draw.text((prog_x+400, prog_y+79), f"{filled}/{maxp}", font=get_font(82), fill='white', anchor='mm')
        draw.text((prog_x+400, prog_y+130), "PLAYERS IN LOBBY", font=get_font(34), fill='#a0b0d0', anchor='mt')

        tasks = [get_avatar_image_cached(bot, pid) for pid in player_ids] + \
                [get_banner_cached(bot, pid) for pid in player_ids]
        results = await asyncio.gather(*tasks)
        avatars = {pid: results[i] for i, pid in enumerate(player_ids)}
        banners = {pid: results[i+len(player_ids)] for i, pid in enumerate(player_ids)}

        slot_x, slot_y = 860, 155
        slot_w, slot_h = 400, 158
        gap_x, gap_y = 24, 16
        cols = 2

        for i in range(maxp):
            col = i % cols
            row = i // cols
            x = slot_x + col * (slot_w + gap_x)
            y = slot_y + row * (slot_h + gap_y)

            if i < filled:
                pid = player_ids[i]
                nick = get_nickname(pid)
                elo = get_elo(pid)
                sid = get_standoff_id(pid)
                av = avatars[pid]
                bn = banners[pid]
                c.execute("SELECT username FROM users WHERE user_id=?", (pid,))
                uname = c.fetchone()
                is_own = (pid == OWNER_ID)
                is_adm = uname and is_admin(uname[0])
                premium = is_premium(pid)
                draw_player_slot(draw, x, y, slot_w, slot_h, nick, elo, sid, av, bn, is_own, is_adm, premium)
            else:
                draw.rounded_rectangle([x, y, x+slot_w, y+slot_h], radius=18, fill=(255,255,255,12), outline=(255,255,255,40))
                font_wait = get_font(32)
                wait_text = "WAITING\nFOR PLAYER..."
                lines = wait_text.split('\n')
                line_h = font_wait.size * 1.2
                total_h = len(lines) * line_h
                cy = y + slot_h//2 - total_h//2
                for idx, line in enumerate(lines):
                    bbox = draw.textbbox((0,0), line, font=font_wait)
                    lw = bbox[2]-bbox[0]
                    draw.text((x+slot_w//2, cy + idx*line_h), line, font=font_wait, fill='#666688', anchor="ma")

        draw.text((width//2, height-38), "404hp FACEIT © 2026", font=get_font(32), fill='#777788', anchor='mt')

        final_img = Image.new('RGB', (width, height), (10,11,16))
        final_img.paste(img, (0,0), img)
        final_img = final_img.filter(ImageFilter.SHARPEN)
        bio = BytesIO()
        final_img.save(bio, format="PNG")
        bio.seek(0)
        return bio
    except Exception as e:
        logging.error(f"generate_lobby_image error: {e}")
        return None

async def generate_draft_image(bot, lobby_id, mode, ct_ids, t_ids, map_name, match_num, host_id):
    try:
        width, height = 1920, 1080
        img = Image.new('RGBA', (width, height), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        draw_lobby_background(draw, width, height)

        title1 = "404hp "
        title2 = "FACEIT"
        font_title = get_font(48)
        w1 = draw.textlength(title1, font=font_title)
        w2 = draw.textlength(title2, font=font_title)
        total_w = w1 + w2
        right_margin = width - 50 - total_w
        draw.text((right_margin, 38), title1, fill='white', font=font_title)
        draw.text((right_margin + w1, 38), title2, fill=FACEIT_ORANGE, font=font_title)

        draw.text((width//2, 60), "Жеребьёвка", font=get_font(72), fill='white', anchor="mt")

        ct_x, ct_y = 60, 140
        ct_w, ct_h = 520, 780
        draw.rounded_rectangle([ct_x,ct_y,ct_x+ct_w,ct_y+ct_h], radius=16, fill=PANEL_BG, outline=ACCENT_BLUE, width=4)
        draw.text((ct_x+40,ct_y+20), "COUNTER-TERRORISTS", font=get_font(36), fill=ACCENT_BLUE)

        t_x = width - ct_w - 60
        draw.rounded_rectangle([t_x,ct_y,t_x+ct_w,ct_y+ct_h], radius=16, fill=PANEL_BG, outline=ACCENT_RED, width=4)
        draw.text((t_x+40,ct_y+20), "TERRORISTS", font=get_font(36), fill=ACCENT_RED)

        center_x = ct_x+ct_w+40
        center_w = t_x - (ct_x+ct_w) - 80
        map_y = ct_y + 100
        map_img = download_map_image(map_name)
        if map_img:
            map_img = fit_image(map_img, center_w-80, 340)
            img.paste(map_img, (center_x+40, map_y))
            draw.rounded_rectangle([center_x+40-4, map_y-4, center_x+center_w-40+4, map_y+340+4],
                                   radius=12, outline=FACEIT_ORANGE, width=4)
        else:
            draw.rounded_rectangle([center_x+40,map_y,center_x+center_w-40,map_y+340], radius=8, fill=(30,35,45))
            draw.text((center_x+center_w//2,map_y+170), "NO MAP", font=get_font(48), fill=TEXT_GRAY, anchor="mm")

        host_text = f"👑 Хост: {get_nickname(host_id)} | Матч #{match_num}"
        draw.text((center_x+center_w//2, map_y+370), host_text, font=get_font(32), fill=GOLD, anchor="mt")

        elo_y = map_y + 420
        draw.rounded_rectangle([center_x+40,elo_y,center_x+center_w-40,elo_y+48], radius=8, fill=(40,40,48))
        ct_elo = sum(get_elo(p) for p in ct_ids)
        t_elo = sum(get_elo(p) for p in t_ids)
        total = max(ct_elo+t_elo, 1)
        ct_ratio = ct_elo/total
        bar_w = center_w-80
        draw.rounded_rectangle([center_x+40,elo_y,center_x+40+int(bar_w*ct_ratio),elo_y+48], radius=8, fill=ACCENT_BLUE)
        draw.rounded_rectangle([center_x+40+int(bar_w*ct_ratio),elo_y,center_x+center_w-40,elo_y+48], radius=8, fill=ACCENT_RED)
        draw.text((center_x+60,elo_y+8), str(ct_elo), font=get_font(42), fill='white')
        draw.text((center_x+center_w-60,elo_y+8), str(t_elo), font=get_font(42), fill='white', anchor="ra")

        all_ids = ct_ids + t_ids
        tasks = [get_avatar_image_cached(bot, pid) for pid in all_ids] + \
                [get_banner_cached(bot, pid) for pid in all_ids]
        results = await asyncio.gather(*tasks)
        avatars = {pid: results[i] for i, pid in enumerate(all_ids)}
        banners = {pid: results[i+len(all_ids)] for i, pid in enumerate(all_ids)}

        slot_y = ct_y + 100
        slot_h = 120
        for i, pid in enumerate(ct_ids[:5]):
            y = slot_y + i * (slot_h + 10)
            c.execute("SELECT username FROM users WHERE user_id=?", (pid,))
            uname = c.fetchone()
            is_own = (pid == OWNER_ID)
            is_adm = uname and is_admin(uname[0])
            premium = is_premium(pid)
            draw_player_slot(draw, ct_x+30, y, ct_w-60, slot_h,
                             get_nickname(pid), get_elo(pid), get_standoff_id(pid),
                             avatars.get(pid), banners.get(pid), is_own, is_adm, premium, compact=True)

        for i, pid in enumerate(t_ids[:5]):
            y = slot_y + i * (slot_h + 10)
            c.execute("SELECT username FROM users WHERE user_id=?", (pid,))
            uname = c.fetchone()
            is_own = (pid == OWNER_ID)
            is_adm = uname and is_admin(uname[0])
            premium = is_premium(pid)
            draw_player_slot(draw, t_x+30, y, ct_w-60, slot_h,
                             get_nickname(pid), get_elo(pid), get_standoff_id(pid),
                             avatars.get(pid), banners.get(pid), is_own, is_adm, premium, compact=True)

        draw.text((width//2, height-50), "404hp FACEIT © 2026", font=get_font(32), fill=TEXT_GRAY, anchor="mm")

        final_img = Image.new('RGB', (width, height), DARK_BG)
        final_img.paste(img, (0,0), img)
        final_img = final_img.filter(ImageFilter.SHARPEN)
        bio = BytesIO()
        final_img.save(bio, format="PNG")
        bio.seek(0)
        return bio
    except Exception as e:
        logging.error(f"generate_draft_image error: {e}")
        return None

async def update_lobby_post(bot: Bot, lobby_id: int):
    img_data = await generate_lobby_image(bot, lobby_id)
    if not img_data:
        c.execute("SELECT mode, thread_id, match_number FROM lobbies WHERE id=?", (lobby_id,))
        mode, thread_id, match_num = c.fetchone()
        c.execute("SELECT user_id FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
        players = c.fetchall()
        lines = [f"👤 {get_nickname(p[0])} | ID {get_standoff_id(p[0])}" for p in players]
        text = f"🎮 Лобби {mode} · lobby #{lobby_id} | Матч #{match_num}\nИгроки: {len(players)}/{MAX_PLAYERS[mode]}\n——————\n" + ("\n".join(lines) if lines else "Пока никого нет.") + "\n——————"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚪 Вступить", callback_data=f"lobby_join_{lobby_id}"),
             InlineKeyboardButton(text="🔙 Выйти", callback_data=f"lobby_leave_{lobby_id}")]])
        c.execute("SELECT message_id, thread_id FROM lobbies WHERE id=?", (lobby_id,))
        msg_id, thread_id = c.fetchone()
        if msg_id:
            try:
                await bot.edit_message_text(chat_id=GROUP_CHAT_ID, message_id=msg_id, text=text, reply_markup=keyboard)
            except:
                msg = await bot.send_message(GROUP_CHAT_ID, text, reply_markup=keyboard, message_thread_id=thread_id)
                c.execute("UPDATE lobbies SET message_id=? WHERE id=?", (msg.message_id, lobby_id)); conn.commit()
        else:
            msg = await bot.send_message(GROUP_CHAT_ID, text, reply_markup=keyboard, message_thread_id=thread_id)
            c.execute("UPDATE lobbies SET message_id=? WHERE id=?", (msg.message_id, lobby_id)); conn.commit()
        return

    c.execute("SELECT mode, thread_id, match_number FROM lobbies WHERE id=?", (lobby_id,))
    mode, thread_id, match_num = c.fetchone()
    c.execute("SELECT user_id FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
    players = c.fetchall()
    lines = [f"👤 {get_nickname(p[0])} | ID {get_standoff_id(p[0])}" for p in players]
    header = f"🎮 Лобби {mode} · lobby #{lobby_id} | Матч #{match_num}"
    text = f"{header}\nИгроки: {len(players)}/{MAX_PLAYERS[mode]}\n——————\n" + ("\n".join(lines) if lines else "Пока никого нет.") + "\n——————"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚪 Вступить", callback_data=f"lobby_join_{lobby_id}"),
         InlineKeyboardButton(text="🔙 Выйти", callback_data=f"lobby_leave_{lobby_id}")]])

    c.execute("SELECT message_id, thread_id FROM lobbies WHERE id=?", (lobby_id,))
    msg_id, thread_id = c.fetchone()
    photo = BufferedInputFile(img_data.read(), filename="lobby.png")
    if msg_id:
        try:
            await bot.edit_message_media(
                chat_id=GROUP_CHAT_ID, message_id=msg_id,
                media=InputMediaPhoto(media=photo, caption=text), reply_markup=keyboard)
        except:
            msg = await bot.send_photo(GROUP_CHAT_ID, photo, caption=text, reply_markup=keyboard, message_thread_id=thread_id)
            c.execute("UPDATE lobbies SET message_id=? WHERE id=?", (msg.message_id, lobby_id)); conn.commit()
    else:
        msg = await bot.send_photo(GROUP_CHAT_ID, photo, caption=text, reply_markup=keyboard, message_thread_id=thread_id)
        c.execute("UPDATE lobbies SET message_id=? WHERE id=?", (msg.message_id, lobby_id)); conn.commit()

async def start_draw(bot: Bot, lobby_id: int, mode: str):
    c.execute("SELECT user_id FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
    players = [row[0] for row in c.fetchall()]
    if len(players) < MAX_PLAYERS[mode]: return

    host_id = None
    for pid in players:
        c.execute("SELECT username FROM users WHERE user_id=?", (pid,))
        row = c.fetchone()
        if row and is_admin(row[0]): host_id = pid; break
    if not host_id: host_id = max(players, key=get_elo)

    duo_pairs = {}
    for pid in players:
        c.execute("SELECT friend_nickname FROM duos WHERE user_id=?", (pid,))
        for (fnick,) in c.fetchall():
            friend_id = None
            for p2 in players:
                if get_nickname(p2).lower() == fnick.lower():
                    friend_id = p2
                    break
            if friend_id and friend_id != pid:
                duo_pairs[pid] = friend_id

    random.shuffle(players)

    if mode == "5x5" and len(duo_pairs) <= 2:
        ct = []
        t = []
        used = set()
        duo_list = list(duo_pairs.items())
        if len(duo_list) >= 1:
            p1, p2 = duo_list[0][0], duo_list[0][1]
            ct.append(p1)
            ct.append(p2)
            used.add(p1)
            used.add(p2)
        if len(duo_list) >= 2:
            p1, p2 = duo_list[1][0], duo_list[1][1]
            t.append(p1)
            t.append(p2)
            used.add(p1)
            used.add(p2)
        remaining = [p for p in players if p not in used]
        random.shuffle(remaining)
        half = MAX_PLAYERS[mode] // 2
        for p in remaining:
            if len(ct) < half:
                ct.append(p)
            else:
                t.append(p)
        while len(ct) < half:
            for p in players:
                if p not in ct and p not in t:
                    ct.append(p)
                    break
        while len(t) < half:
            for p in players:
                if p not in ct and p not in t:
                    t.append(p)
                    break
    else:
        half = len(players)//2
        ct = players[:half]
        t = players[half:]
        if duo_pairs:
            for p1, p2 in duo_pairs.items():
                if (p1 in ct and p2 in ct) or (p1 in t and p2 in t):
                    if p1 in ct:
                        for idx, p_other in enumerate(t):
                            if p_other not in duo_pairs and p_other not in duo_pairs.values():
                                ct[ct.index(p1)] = p_other
                                t[idx] = p1
                                break
                    else:
                        for idx, p_other in enumerate(ct):
                            if p_other not in duo_pairs and p_other not in duo_pairs.values():
                                t[t.index(p1)] = p_other
                                ct[idx] = p1
                                break
            ct = [p for p in players if p in ct]
            t = [p for p in players if p in t]

    now = datetime.now().isoformat()
    c.execute("SELECT match_number, map_name FROM lobbies WHERE id=?", (lobby_id,))
    match_num, map_name = c.fetchone()
    c.execute("INSERT INTO matches (lobby_id, match_number, status, created_at, host_id) VALUES (?,?, 'drawn',?,?)",
              (lobby_id, match_num, now, host_id))
    match_id = c.lastrowid
    for p in ct: c.execute("INSERT INTO match_players (match_id, user_id, team) VALUES (?,?,'CT')", (match_id, p))
    for p in t: c.execute("INSERT INTO match_players (match_id, user_id, team) VALUES (?,?,'T')", (match_id, p))
    c.execute("UPDATE lobbies SET match_number = match_number + 1 WHERE id=?", (lobby_id,))
    c.execute("DELETE FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
    conn.commit()

    img_data = await generate_draft_image(bot, lobby_id, mode, ct, t, map_name, match_num, host_id)
    if img_data:
        photo = BufferedInputFile(img_data.read(), filename="draft.png")
        await bot.send_photo(GROUP_CHAT_ID, photo, message_thread_id=TOPIC_DRAW)

    host_nick = get_nickname(host_id)
    host_sid = get_standoff_id(host_id)
    text_draw = (
        f"ℹ️ Жеребьёвка игроков по командам\n"
        f"404hp faceit | host by: {host_nick}\n"
        f"——————\n"
        f"⚔️ Counter-terrorists:\n"
    )
    for p in ct:
        text_draw += f"> 👤 {get_nickname(p)} | ID {get_standoff_id(p)}\n"
    text_draw += f"\n🔫 Terrorists:\n"
    for p in t:
        text_draw += f"> 👤 {get_nickname(p)} | ID {get_standoff_id(p)}\n"
    text_draw += (
        "——————\n"
        f"ℹ️ host lobby, должен пригласить вас в лобби в течении 5-10 минут, может с маленькой задержкой, "
        f"если в течении 11 минут вас не пригласили то обратитесь в тикет поддержки"
    )
    await bot.send_message(GROUP_CHAT_ID, text_draw, message_thread_id=TOPIC_DRAW)

    await update_lobby_post(bot, lobby_id)

# ------------------------------------------------------------
# FSM (состояния)
# ------------------------------------------------------------
class RegStates(StatesGroup): nick = State(); sid = State(); confirm = State()
class AdminStates(StatesGroup): waiting_add = State(); waiting_remove = State()
class AvatarStates(StatesGroup): waiting_avatar = State()
class BannerStates(StatesGroup): waiting_banner = State()
class ResultStates(StatesGroup): waiting_screenshot = State(); waiting_score = State()
class DuoStates(StatesGroup): waiting_nickname = State()
class ManageAccountStates(StatesGroup): waiting_user = State(); waiting_action = State(); waiting_value = State()

class ReportStates(StatesGroup):
    waiting_target = State()
    waiting_text = State()

class ProblemTicketStates(StatesGroup):
    waiting_text = State()
    waiting_screenshot_choice = State()
    waiting_screenshot = State()
    waiting_admin_reply = State()

# CallbackData
class ReportAction(CallbackData, prefix="report"):
    action: str
    report_id: int

class TicketAction(CallbackData, prefix="ticket"):
    action: str
    ticket_id: int
    user_id: int

# ------------------------------------------------------------
# Главное меню и вспомогательные клавиатуры
# ------------------------------------------------------------
async def main_menu_keyboard(user_id, username):
    btns = [
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile")],
        [InlineKeyboardButton(text="🔎 Поиск лобби", callback_data="menu_search")],
        [InlineKeyboardButton(text="🏆 Лидерборд", callback_data="menu_leaderboard")],
        [InlineKeyboardButton(text="🎫 Тикет поддержки", callback_data="menu_ticket")],
    ]
    if is_admin(username):
        btns.append([InlineKeyboardButton(text="⚙️ Админ панель", callback_data="menu_admin")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

# ------------------------------------------------------------
# Хендлеры
# ------------------------------------------------------------
@dp.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    if not await check_subscription(bot, message.from_user.id):
        await message.answer(f"❌ Подпишитесь на {CHANNEL_USERNAME} и нажмите /start"); return
    if is_registered(message.from_user.id):
        if is_banned(message.from_user.id):
            await message.answer("❌ Вы забанены в боте.")
            return
        await message.answer("Главное меню:", reply_markup=await main_menu_keyboard(message.from_user.id, message.from_user.username)); return
    await message.answer("Введите игровой никнейм из Standoff 2:"); await state.set_state(RegStates.nick)

@dp.message(RegStates.nick)
async def reg_nick(message: Message, state: FSMContext):
    await state.update_data(nick=message.text.strip()); await message.answer("Введите ваш ID из Standoff 2:"); await state.set_state(RegStates.sid)

@dp.message(RegStates.sid)
async def reg_sid(message: Message, state: FSMContext):
    await state.update_data(sid=message.text.strip())
    data = await state.get_data()
    await message.answer(f"Проверьте:\nНикнейм: {data['nick']}\nID: {data['sid']}\n\nВсё верно?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="reg_confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="reg_cancel")]]))
    await state.set_state(RegStates.confirm)

@dp.callback_query(RegStates.confirm, F.data.startswith("reg_"))
async def reg_confirm(query: CallbackQuery, state: FSMContext):
    if query.data == "reg_confirm":
        user = query.from_user; data = await state.get_data()
        c.execute("INSERT OR REPLACE INTO users (user_id, username, nickname, standoff_id, elo, wins, losses, registered) VALUES (?,?,?,?,0,0,0,1)",
                  (user.id, user.username or "", data['nick'], data['sid']))
        conn.commit()
        await query.message.edit_text("Регистрация успешна! ✅")
        await query.message.answer("Главное меню:", reply_markup=await main_menu_keyboard(user.id, user.username))
    else: await query.message.edit_text("Регистрация отменена.")
    await state.clear()

# Профиль
@dp.callback_query(F.data == "menu_profile")
async def profile(query: CallbackQuery, bot: Bot):
    if not is_registered(query.from_user.id): await query.answer("Сначала /start", show_alert=True); return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    user_id = query.from_user.id; username = query.from_user.username
    nick = get_nickname(user_id); sid = get_standoff_id(user_id)
    elo, wins, losses = get_user_stats(user_id)
    total = wins + losses
    winrate = (wins / total * 100) if total > 0 else 0
    info_text = (
        f"ℹ️ Информация об игроке:\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"[👤] Nickname: {nick}\n"
        f"[🪪] ID: {sid}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"[⚡] Сыграно матчей: {total}\n"
        f"[🏆] Victory: {wins} ({winrate:.1f}%)\n"
        f"[❌] Defeat: {losses}"
    )
    cached_av = await get_avatar_image_cached(bot, user_id)
    cached_bn = await get_banner_cached(bot, user_id)
    card = generate_profile_card(user_id, username=username, cached_avatar=cached_av, cached_banner=cached_bn)
    photo = BufferedInputFile(card.read(), filename="profile.png")
    profile_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏞️ Загрузить аватар", callback_data="set_avatar"),
         InlineKeyboardButton(text="🪪 Загрузить баннер", callback_data="set_banner")],
        [InlineKeyboardButton(text="🔄 Сбросить баннер", callback_data="reset_banner"),
         InlineKeyboardButton(text="🔄 Сбросить аватарку", callback_data="reset_avatar")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")],
    ])
    await query.message.answer_photo(photo, caption=info_text, reply_markup=profile_kb)
    await query.message.delete()

@dp.callback_query(F.data == "set_avatar")
async def set_avatar_prompt(query: CallbackQuery, state: FSMContext):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    await query.message.delete(); await query.message.answer("Отправьте фотографию для аватарки:"); await state.set_state(AvatarStates.waiting_avatar)

@dp.message(AvatarStates.waiting_avatar, F.photo)
async def process_avatar(message: Message, state: FSMContext):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    file_id = message.photo[-1].file_id
    c.execute("UPDATE users SET custom_avatar=? WHERE user_id=?", (file_id, message.from_user.id)); conn.commit()
    avatar_cache.pop(message.from_user.id, None)
    await message.answer("Аватарка обновлена!"); await state.clear()

@dp.callback_query(F.data == "set_banner")
async def set_banner_prompt(query: CallbackQuery, state: FSMContext):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    await query.message.delete(); await query.message.answer("Отправьте изображение для баннера ячейки:"); await state.set_state(BannerStates.waiting_banner)

@dp.message(BannerStates.waiting_banner, F.photo)
async def process_banner(message: Message, state: FSMContext):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    file_id = message.photo[-1].file_id
    c.execute("UPDATE users SET custom_banner=? WHERE user_id=?", (file_id, message.from_user.id)); conn.commit()
    banner_cache.pop(message.from_user.id, None)
    await message.answer("Баннер обновлён!"); await state.clear()

@dp.callback_query(F.data == "reset_banner")
async def reset_banner(query: CallbackQuery):
    c.execute("UPDATE users SET custom_banner=NULL WHERE user_id=?", (query.from_user.id,))
    conn.commit()
    banner_cache.pop(query.from_user.id, None)
    await query.answer("Баннер сброшен.")

@dp.callback_query(F.data == "reset_avatar")
async def reset_avatar(query: CallbackQuery):
    c.execute("UPDATE users SET custom_avatar=NULL WHERE user_id=?", (query.from_user.id,))
    conn.commit()
    avatar_cache.pop(query.from_user.id, None)
    await query.answer("Аватарка сброшена.")

# Поиск лобби
@dp.callback_query(F.data == "menu_search")
async def search_mode_menu(query: CallbackQuery):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    await query.message.edit_text("Выберите режим для поиска лобби:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5x5", callback_data="search_5x5")],
        [InlineKeyboardButton(text="2x2", callback_data="search_2x2")],
        [InlineKeyboardButton(text="1x1", callback_data="search_1x1")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")]]))

@dp.callback_query(F.data.startswith("search_"))
async def show_lobbies_by_mode(query: CallbackQuery):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    mode = query.data.split("_")[1]
    c.execute("SELECT id, mode, thread_id FROM lobbies WHERE mode=?", (mode,))
    lobbies = c.fetchall()
    if not lobbies: await query.answer("Нет открытых лобби этого режима.", show_alert=True); return
    buttons = []
    for lid, m, tid in lobbies:
        c.execute("SELECT COUNT(*) FROM lobby_registrations WHERE lobby_id=?", (lid,))
        cnt = c.fetchone()[0]
        buttons.append([InlineKeyboardButton(text=f"{m} лобби {lid} [{cnt}/{MAX_PLAYERS[m]}]", callback_data=f"lobby_join_{lid}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu_search")])
    await query.message.edit_text("Доступные лобби:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

# Вступление/выход из лобби
@dp.callback_query(F.data.startswith("lobby_join_"))
async def lobby_join(query: CallbackQuery, bot: Bot):
    user_id = query.from_user.id; lobby_id = int(query.data.split("_")[-1])
    if is_banned(user_id): await query.answer("Вы забанены в боте.", show_alert=True); return
    if is_game_banned(user_id): await query.answer("Вам запрещено участвовать в играх.", show_alert=True); return
    if not is_registered(user_id): await query.answer("Сначала /start"); return
    if is_muted(user_id): await query.answer("Вы замучены."); return
    c.execute("SELECT * FROM lobby_registrations WHERE lobby_id=? AND user_id=?", (lobby_id, user_id))
    if c.fetchone(): await query.answer("Вы уже в лобби."); return
    c.execute("SELECT mode FROM lobbies WHERE id=?", (lobby_id,))
    mode_row = c.fetchone()
    if not mode_row: await query.answer("Лобби не найдено."); return
    mode = mode_row[0]
    c.execute("SELECT COUNT(*) FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
    if c.fetchone()[0] >= MAX_PLAYERS[mode]: await query.answer("Лобби заполнено."); return
    c.execute("INSERT INTO lobby_registrations (lobby_id, user_id, joined_at) VALUES (?,?,?)",
              (lobby_id, user_id, datetime.now().isoformat())); conn.commit()
    await query.answer("Вы вступили!")
    await update_lobby_post(bot, lobby_id)
    c.execute("SELECT COUNT(*) FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
    if c.fetchone()[0] >= MAX_PLAYERS[mode]:
        await start_draw(bot, lobby_id, mode)

@dp.callback_query(F.data.startswith("lobby_leave_"))
async def lobby_leave(query: CallbackQuery, bot: Bot):
    user_id = query.from_user.id; lobby_id = int(query.data.split("_")[-1])
    if is_banned(user_id): await query.answer("Вы забанены в боте.", show_alert=True); return
    c.execute("DELETE FROM lobby_registrations WHERE lobby_id=? AND user_id=?", (lobby_id, user_id)); conn.commit()
    await query.answer("Вы вышли."); await update_lobby_post(bot, lobby_id)

# Админ панель
@dp.callback_query(F.data == "menu_admin")
async def admin_menu(query: CallbackQuery):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    if not is_admin(query.from_user.username): await query.answer("Нет доступа."); return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_list")],
        [InlineKeyboardButton(text="🛡️ Управление админами", callback_data="admin_manage")],
        [InlineKeyboardButton(text="🎮 Управление лобби", callback_data="admin_lobbies")],
        [InlineKeyboardButton(text="⚙️ Управление аккаунтом", callback_data="admin_manage_account")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")],
    ])
    if query.from_user.id == OWNER_ID:
        keyboard.inline_keyboard.insert(3, [InlineKeyboardButton(text="🗑 Сбросить ВСЕ лобби", callback_data="admin_reset_all_lobbies")])
    await query.message.edit_text("Админ панель:", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_reset_all_lobbies")
async def admin_reset_all_lobbies(query: CallbackQuery, bot: Bot):
    if query.from_user.id != OWNER_ID: await query.answer("Нет доступа."); return
    try:
        c.execute("DELETE FROM lobby_registrations"); conn.commit()
        c.execute("SELECT id FROM lobbies")
        all_lobbies = [row[0] for row in c.fetchall()]
        for lid in all_lobbies:
            new_map = random.choice(list(map_images_cache.keys())) if map_images_cache else None
            c.execute("UPDATE lobbies SET message_id=NULL, map_name=? WHERE id=?", (new_map, lid))
        conn.commit()
        await restore_all_lobby_posts()
        await query.answer("Все лобби сброшены и пересозданы!")
    except Exception as e:
        logging.error(f"reset all lobbies error: {e}")
        await query.answer("Произошла ошибка при сбросе.")

@dp.callback_query(F.data == "admin_lobbies")
async def admin_lobbies_list(query: CallbackQuery):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    c.execute("SELECT id, mode FROM lobbies"); lobbies = c.fetchall()
    buttons = [[InlineKeyboardButton(text=f"{m} лобби {lid}", callback_data=f"admin_lobby_{lid}")] for lid, m in lobbies]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu_admin")])
    await query.message.edit_text("Выберите лобби:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("admin_lobby_"))
async def admin_lobby_actions(query: CallbackQuery, bot: Bot):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    lobby_id = int(query.data.split("_")[-1])
    if not is_admin(query.from_user.username): await query.answer("Нет доступа."); return
    await query.message.edit_text(f"Действия с лобби {lobby_id}:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin_refresh_{lobby_id}")],
        [InlineKeyboardButton(text="🗑 Сбросить игроков", callback_data=f"admin_reset_{lobby_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_lobbies")]]))

@dp.callback_query(F.data.startswith("admin_refresh_"))
async def admin_refresh_lobby(query: CallbackQuery, bot: Bot):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    lobby_id = int(query.data.split("_")[-1])
    if not is_admin(query.from_user.username): await query.answer("Нет доступа."); return
    await update_lobby_post(bot, lobby_id); await query.answer("Лобби обновлено.")

@dp.callback_query(F.data.startswith("admin_reset_"))
async def admin_reset_lobby(query: CallbackQuery, bot: Bot):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    lobby_id = int(query.data.split("_")[-1])
    if not is_admin(query.from_user.username): await query.answer("Нет доступа."); return
    c.execute("DELETE FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
    new_map = random.choice(list(map_images_cache.keys())) if map_images_cache else None
    c.execute("UPDATE lobbies SET map_name=? WHERE id=?", (new_map, lobby_id))
    conn.commit()
    await update_lobby_post(bot, lobby_id); await query.answer("Игроки сброшены, карта обновлена.")

# Пользователи и баны
@dp.callback_query(F.data == "admin_list")
async def admin_list(query: CallbackQuery):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    c.execute("SELECT user_id, nickname FROM users")
    users = c.fetchall()
    buttons = [[InlineKeyboardButton(text=f"{n} (ID:{uid})", callback_data=f"admin_user_{uid}")] for uid, n in users]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu_admin")])
    await query.message.edit_text("Выберите пользователя:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("admin_user_"))
async def admin_user(query: CallbackQuery):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    uid = int(query.data.split("_")[-1])
    c.execute("SELECT nickname, banned_until, muted_until, premium, game_ban FROM users WHERE user_id=?", (uid,))
    row = c.fetchone()
    if not row: await query.answer("Не найден."); return
    nick, banned, muted, premium, gban = row
    status = []
    if banned and datetime.fromisoformat(banned) > datetime.now(): status.append("🔴 Забанен в боте")
    if muted and datetime.fromisoformat(muted) > datetime.now(): status.append("🔇 Замучен в чате")
    if premium: status.append("⭐ Premium")
    if gban: status.append("🚫 Запрет на игры")
    status_str = "\n".join(status) if status else "✅ Активен"
    await query.message.edit_text(f"Пользователь: {nick} (ID: {uid})\n{status_str}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Забанить в боте", callback_data=f"admin_ban_{uid}"),
             InlineKeyboardButton(text="✅ Разбанить в боте", callback_data=f"admin_unban_{uid}")],
            [InlineKeyboardButton(text="🔇 Замутить в чате", callback_data=f"admin_mute_{uid}"),
             InlineKeyboardButton(text="🔈 Размутить в чате", callback_data=f"admin_unmute_{uid}")],
            [InlineKeyboardButton(text="⭐ Выдать/убрать Premium", callback_data=f"admin_premium_{uid}")],
            [InlineKeyboardButton(text="🚫 Запретить игры" if not gban else "✅ Разрешить игры", callback_data=f"admin_gameban_{uid}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_list")]]))

@dp.callback_query(F.data.startswith("admin_ban_"))
async def admin_ban(query: CallbackQuery):
    uid = int(query.data.split("_")[-1])
    if uid == OWNER_ID:
        await query.answer("❌ Нельзя забанить создателя.")
        return
    until = datetime.now() + timedelta(days=36500)
    c.execute("UPDATE users SET banned_until=? WHERE user_id=?", (until.isoformat(), uid)); conn.commit()
    await query.answer("Пользователь забанен в боте.")
    await admin_user(query)

@dp.callback_query(F.data.startswith("admin_unban_"))
async def admin_unban(query: CallbackQuery):
    uid = int(query.data.split("_")[-1])
    c.execute("UPDATE users SET banned_until=NULL WHERE user_id=?", (uid,)); conn.commit()
    await query.answer("Пользователь разбанен в боте.")
    await admin_user(query)

@dp.callback_query(F.data.startswith("admin_mute_"))
async def admin_mute(query: CallbackQuery):
    uid = int(query.data.split("_")[-1]); until = datetime.now() + timedelta(hours=24)
    c.execute("UPDATE users SET muted_until=? WHERE user_id=?", (until.isoformat(), uid)); conn.commit()
    try:
        await bot.restrict_chat_member(chat_id=GROUP_CHAT_ID, user_id=uid,
                                       permissions=ChatPermissions(can_send_messages=False, can_send_other_messages=False),
                                       until_date=until)
    except: pass
    await query.answer("Замучен в чате на 24 ч.")
    await admin_user(query)

@dp.callback_query(F.data.startswith("admin_unmute_"))
async def admin_unmute(query: CallbackQuery):
    uid = int(query.data.split("_")[-1])
    c.execute("UPDATE users SET muted_until=NULL WHERE user_id=?", (uid,)); conn.commit()
    try:
        await bot.restrict_chat_member(chat_id=GROUP_CHAT_ID, user_id=uid,
                                       permissions=ChatPermissions(can_send_messages=True, can_send_other_messages=True))
    except: pass
    await query.answer("Размучен в чате.")
    await admin_user(query)

@dp.callback_query(F.data.startswith("admin_premium_"))
async def admin_premium(query: CallbackQuery):
    uid = int(query.data.split("_")[-1])
    c.execute("SELECT premium FROM users WHERE user_id=?", (uid,))
    row = c.fetchone()
    current = row[0] if row else 0
    new = 0 if current else 1
    c.execute("UPDATE users SET premium=? WHERE user_id=?", (new, uid))
    conn.commit()
    await query.answer("Премиум выдан." if new else "Премиум убран.")
    await admin_user(query)

@dp.callback_query(F.data.startswith("admin_gameban_"))
async def admin_gameban(query: CallbackQuery):
    uid = int(query.data.split("_")[-1])
    c.execute("SELECT game_ban FROM users WHERE user_id=?", (uid,))
    row = c.fetchone()
    current = row[0] if row else 0
    new = 0 if current else 1
    c.execute("UPDATE users SET game_ban=? WHERE user_id=?", (new, uid))
    conn.commit()
    await query.answer("Игры запрещены." if new else "Игры разрешены.")
    await admin_user(query)

# Управление админами
@dp.callback_query(F.data == "admin_manage")
async def admin_manage_menu(query: CallbackQuery):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    c.execute("SELECT username FROM admins")
    admins = [row[0] for row in c.fetchall()]
    text = "Администраторы:\n" + "\n".join(f"• @{a}" for a in admins)
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="admin_add"),
         InlineKeyboardButton(text="➖ Удалить", callback_data="admin_remove")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_admin")]]))

@dp.callback_query(F.data == "admin_add")
async def admin_add_prompt(query: CallbackQuery, state: FSMContext):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    await query.message.edit_text("Введите @username для добавления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="admin_manage")]]))
    await state.set_state(AdminStates.waiting_add)

@dp.callback_query(F.data == "admin_remove")
async def admin_remove_prompt(query: CallbackQuery, state: FSMContext):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    await query.message.edit_text("Введите @username для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="admin_manage")]]))
    await state.set_state(AdminStates.waiting_remove)

@dp.message(AdminStates.waiting_add)
async def process_admin_add(message: Message, state: FSMContext):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    raw = message.text.strip().lstrip('@').lower()
    if not raw: await message.answer("Некорректный username."); return
    c.execute("INSERT OR IGNORE INTO admins (username) VALUES (?)", (raw,)); conn.commit()
    await message.answer(f"✅ @{raw} теперь администратор."); await state.clear()

@dp.message(AdminStates.waiting_remove)
async def process_admin_remove(message: Message, state: FSMContext):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    raw = message.text.strip().lstrip('@').lower()
    if raw == "nelinner": await message.answer("❌ Нельзя удалить главного руководителя."); return
    if not raw: await message.answer("Некорректный username."); return
    c.execute("DELETE FROM admins WHERE username=?", (raw,)); conn.commit()
    await message.answer(f"❌ @{raw} удалён из администраторов."); await state.clear()

# Лидерборд
@dp.callback_query(F.data == "menu_leaderboard")
async def leaderboard(query: CallbackQuery):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    c.execute("SELECT nickname, elo FROM users ORDER BY elo DESC LIMIT 10")
    rows = c.fetchall()
    text = "🏆 Лидерборд\n" + "\n".join(f"{i+1}. {r[0]} — {r[1]} ELO" for i, r in enumerate(rows)) if rows else "Пусто."
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")]]))

# Тикет поддержки (новое меню)
@dp.callback_query(F.data == "menu_ticket")
async def ticket_menu(query: CallbackQuery):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 Подать жалобу", callback_data="ticket_report")],
        [InlineKeyboardButton(text="📝 Описать свою проблему", callback_data="ticket_problem")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")],
    ])
    await query.message.edit_text("Выберите формат поддержки:", reply_markup=keyboard)

@dp.callback_query(F.data == "ticket_report")
async def ticket_report_start(query: CallbackQuery, state: FSMContext):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    await state.clear()
    await query.message.edit_text("👤 Введите @username или nickname нарушителя:")
    await state.set_state(ReportStates.waiting_target)

@dp.callback_query(F.data == "ticket_problem")
async def ticket_problem_start(query: CallbackQuery, state: FSMContext):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    await state.clear()
    await query.message.edit_text("📝 Опишите свою проблему:")
    await state.set_state(ProblemTicketStates.waiting_text)

@dp.message(ProblemTicketStates.waiting_text)
async def problem_text(message: Message, state: FSMContext):
    problem = message.text.strip()
    if not problem:
        await message.answer("❌ Текст проблемы не может быть пустым.")
        return
    await state.update_data(problem_text=problem)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="screenshot_yes"),
         InlineKeyboardButton(text="❌ Нет", callback_data="screenshot_no")]
    ])
    await message.answer("Есть скриншот проблемы?", reply_markup=keyboard)
    await state.set_state(ProblemTicketStates.waiting_screenshot_choice)

@dp.callback_query(F.data.startswith("screenshot_"))
async def screenshot_choice(query: CallbackQuery, state: FSMContext):
    choice = query.data.split("_")[1]
    if choice == "yes":
        await query.message.edit_text("📸 Отправьте скриншот проблемы.")
        await state.set_state(ProblemTicketStates.waiting_screenshot)
    else:
        await state.update_data(screenshot_file_id=None)
        await send_problem_report(query, state)

@dp.message(ProblemTicketStates.waiting_screenshot, F.photo)
async def screenshot_received(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(screenshot_file_id=file_id)
    await send_problem_report(message, state)

async def send_problem_report(source: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    problem_text = data.get("problem_text")
    screenshot_id = data.get("screenshot_file_id")
    user = source.from_user if isinstance(source, Message) else source.from_user

    ticket_id = save_ticket(user.id, user.username, problem_text, screenshot_id)

    if user.username:
        user_display = f"@{user.username}"
    else:
        user_display = user.full_name

    report_msg = (
        "ℹ️ <b>Проблема игрока | 404hp faceit</b>\n"
        "━━━━━━━━━━━\n"
        f"[👤] От пользователя: {user_display}\n"
        "━━━━━━━━━━━\n"
        f"[📃] Текст:\n{problem_text}"
    )

    await bot.send_message(GROUP_CHAT_ID, report_msg, parse_mode=ParseMode.HTML, message_thread_id=TOPIC_TICKET)

    for admin_id in get_admin_ids():
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Ответить", callback_data=TicketAction(action="reply", ticket_id=ticket_id, user_id=user.id).pack())],
            [InlineKeyboardButton(text="🔒 Закрыть тикет", callback_data=TicketAction(action="close", ticket_id=ticket_id, user_id=user.id).pack())],
            [InlineKeyboardButton(text="✅ Отметить как решённый", callback_data=TicketAction(action="resolve", ticket_id=ticket_id, user_id=user.id).pack())],
        ])
        if screenshot_id:
            try:
                await bot.send_photo(admin_id, screenshot_id, caption=report_msg, parse_mode=ParseMode.HTML, reply_markup=kb)
            except Exception as e:
                logging.error(f"Не удалось отправить тикет админу {admin_id}: {e}")
                await bot.send_message(admin_id, report_msg, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await bot.send_message(admin_id, report_msg, parse_mode=ParseMode.HTML, reply_markup=kb)

    if isinstance(source, CallbackQuery):
        await source.message.edit_text("✅ Ваше обращение отправлено. Мы скоро свяжемся с вами.")
    else:
        await source.answer("✅ Ваше обращение отправлено. Мы скоро свяжемся с вами.")
    await state.clear()

@dp.callback_query(TicketAction.filter())
async def ticket_callback_handler(query: CallbackQuery, callback_data: TicketAction, state: FSMContext, bot: Bot):
    try:
        member = await bot.get_chat_member(GROUP_CHAT_ID, query.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            await query.answer("❌ У вас нет прав.", show_alert=True)
            return
    except Exception as e:
        await query.answer("⚠️ Ошибка проверки прав.")
        return

    action = callback_data.action
    ticket_id = callback_data.ticket_id
    user_id = callback_data.user_id

    ticket = get_ticket(ticket_id)
    if not ticket:
        await query.answer("Тикет не найден.")
        return

    if action == "close":
        update_ticket_status(ticket_id, "closed")
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.edit_text(query.message.text + "\n\n🔒 Тикет закрыт.")
        await query.answer("Тикет закрыт.")
    elif action == "resolve":
        update_ticket_status(ticket_id, "resolved")
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.edit_text(query.message.text + "\n\n✅ Тикет отмечен как решённый.")
        await query.answer("Тикет отмечен как решённый.")
    elif action == "reply":
        await query.answer("Введите ответ пользователю. Для отмены /cancel")
        await state.update_data(reply_user_id=user_id, reply_ticket_id=ticket_id)
        await state.set_state(ProblemTicketStates.waiting_admin_reply)
        await query.message.answer("✏️ Введите текст ответа:")

@dp.message(ProblemTicketStates.waiting_admin_reply)
async def admin_reply_text(message: Message, state: FSMContext, bot: Bot):
    reply_text = message.text.strip()
    if not reply_text:
        await message.answer("❌ Ответ не может быть пустым.")
        return

    data = await state.get_data()
    user_id = data.get("reply_user_id")
    ticket_id = data.get("reply_ticket_id")

    try:
        await bot.send_message(
            user_id,
            f"📬 <b>Ответ от поддержки 404hp faceit:</b>\n\n{reply_text}",
            parse_mode=ParseMode.HTML
        )
        await message.answer("✅ Ответ отправлен пользователю.")
    except Exception as e:
        logging.error(f"Не удалось отправить ответ пользователю {user_id}: {e}")
        await message.answer("⚠️ Не удалось отправить ответ. Возможно, пользователь заблокировал бота.")

    update_ticket_status(ticket_id, "answered")
    await state.clear()

# Система репортов (жалоб)
def report_keyboard(report_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚫 Ограничить доступ к боту",
                    callback_data=ReportAction(action="restrict", report_id=report_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Вернуть доступ к боту",
                    callback_data=ReportAction(action="unrestrict", report_id=report_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔨 Забанить",
                    callback_data=ReportAction(action="ban", report_id=report_id).pack(),
                ),
                InlineKeyboardButton(
                    text="♻️ Разбанить",
                    callback_data=ReportAction(action="unban", report_id=report_id).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔇 Замутить",
                    callback_data=ReportAction(action="mute", report_id=report_id).pack(),
                ),
                InlineKeyboardButton(
                    text="🔊 Размутить",
                    callback_data=ReportAction(action="unmute", report_id=report_id).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить репорт",
                    callback_data=ReportAction(action="cancel", report_id=report_id).pack(),
                )
            ],
        ]
    )

@report_router.message(Command("report"))
async def report_command(message: Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        target = args[1].strip().lstrip("@")
        await state.update_data(target=target)
        await message.answer("📝 Введите текст жалобы:")
        await state.set_state(ReportStates.waiting_text)
        return
    await message.answer("👤 Введите @username или nickname пользователя, на которого хотите пожаловаться:")
    await state.set_state(ReportStates.waiting_target)

# Эти хендлеры также используются колбэком "Подать жалобу"
@dp.message(ReportStates.waiting_target)
@report_router.message(ReportStates.waiting_target)
async def report_target(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    if not target:
        await message.answer("❌ Пользователь не указан.\n\nВведите @username или nickname:")
        return
    await state.update_data(target=target)
    await message.answer("📝 Введите текст жалобы:")
    await state.set_state(ReportStates.waiting_text)

@dp.message(ReportStates.waiting_text)
@report_router.message(ReportStates.waiting_text)
async def report_text(message: Message, state: FSMContext, bot: Bot):
    report_text = message.text.strip()
    if not report_text:
        await message.answer("❌ Текст жалобы не может быть пустым.")
        return

    data = await state.get_data()
    target = data["target"]
    reporter = message.from_user

    if reporter.username:
        reporter_display = f"@{reporter.username}"
    else:
        reporter_display = reporter.full_name

    # Ищем target_id в базе по username (если есть)
    target_id = None
    c.execute("SELECT user_id FROM users WHERE username=?", (target.lower(),))
    row = c.fetchone()
    if row:
        target_id = row[0]

    report_id = save_report(
        reporter_id=reporter.id,
        reporter_username=reporter.username,
        target_id=target_id,
        target_username=target,
        report_text=report_text,
    )

    report_message = (
        "ℹ️ <b>Жалоба | 404hp faceit</b>\n"
        "━━━━━━━━━━━\n"
        f"👤 <b>От пользователя:</b> {reporter_display}\n"
        f"👤 <b>На пользователя:</b> @{target}\n"
        "━━━━━━━━━━━\n"
        "📃 <b>Текст жалобы на игрока:</b>\n\n"
        f"{report_text}"
    )

    sent_message = await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=report_message,
        parse_mode=ParseMode.HTML,
        reply_markup=report_keyboard(report_id),
        message_thread_id=TOPIC_TICKET,
    )

    c.execute("UPDATE reports SET message_id=? WHERE id=?", (sent_message.message_id, report_id))
    conn.commit()

    await message.answer("✅ Жалоба успешно отправлена модераторам.")
    await state.clear()

@dp.callback_query(ReportAction.filter())
@report_router.callback_query(ReportAction.filter())
async def report_callback_handler(query: CallbackQuery, callback_data: ReportAction, bot: Bot):
    try:
        member = await bot.get_chat_member(GROUP_CHAT_ID, query.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            await query.answer("❌ У вас нет прав для выполнения этого действия.", show_alert=True)
            return
    except Exception as e:
        logging.error(f"Ошибка проверки прав: {e}")
        await query.answer("⚠️ Не удалось проверить права.")
        return

    action = callback_data.action
    report_id = callback_data.report_id

    report = get_report(report_id)
    if not report:
        await query.answer("❌ Репорт не найден.", show_alert=True)
        return

    reporter_id, target_id, target_username, report_text = report

    if not target_id:
        c.execute("SELECT user_id FROM users WHERE username=?", (target_username.lower(),))
        row = c.fetchone()
        if row:
            target_id = row[0]
            update_report_target_id(report_id, target_id)

    if not target_id:
        await query.answer("❌ Пользователь не найден в базе.", show_alert=True)
        return

    if action == "restrict":
        c.execute("UPDATE users SET game_ban=1 WHERE user_id=?", (target_id,))
        conn.commit()
        await query.answer(f"🚫 Доступ к играм ограничен: @{target_username}")
    elif action == "unrestrict":
        c.execute("UPDATE users SET game_ban=0 WHERE user_id=?", (target_id,))
        conn.commit()
        await query.answer(f"✅ Доступ к играм возвращён: @{target_username}")
    elif action == "ban":
        if target_id == OWNER_ID:
            await query.answer("❌ Нельзя забанить создателя.")
            return
        until = datetime.now() + timedelta(days=36500)
        c.execute("UPDATE users SET banned_until=? WHERE user_id=?", (until.isoformat(), target_id))
        conn.commit()
        try:
            await bot.ban_chat_member(chat_id=GROUP_CHAT_ID, user_id=target_id)
        except Exception as e:
            logging.error(f"Ban error: {e}")
        await query.answer(f"🔨 Пользователь забанен: @{target_username}")
    elif action == "unban":
        c.execute("UPDATE users SET banned_until=NULL WHERE user_id=?", (target_id,))
        conn.commit()
        try:
            await bot.unban_chat_member(chat_id=GROUP_CHAT_ID, user_id=target_id)
            await bot.restrict_chat_member(chat_id=GROUP_CHAT_ID, user_id=target_id,
                                           permissions=ChatPermissions(can_send_messages=True, can_send_other_messages=True))
        except Exception as e:
            logging.error(f"Unban error: {e}")
        await query.answer(f"♻️ Пользователь разбанен: @{target_username}")
    elif action == "mute":
        if target_id == OWNER_ID:
            await query.answer("❌ Нельзя замутить создателя.")
            return
        until = datetime.now() + timedelta(hours=24)
        c.execute("UPDATE users SET muted_until=? WHERE user_id=?", (until.isoformat(), target_id))
        conn.commit()
        try:
            await bot.restrict_chat_member(chat_id=GROUP_CHAT_ID, user_id=target_id,
                                           permissions=ChatPermissions(can_send_messages=False, can_send_other_messages=False),
                                           until_date=until)
        except Exception as e:
            logging.error(f"Mute error: {e}")
        await query.answer(f"🔇 Пользователь замучен: @{target_username}")
    elif action == "unmute":
        c.execute("UPDATE users SET muted_until=NULL WHERE user_id=?", (target_id,))
        conn.commit()
        try:
            await bot.restrict_chat_member(chat_id=GROUP_CHAT_ID, user_id=target_id,
                                           permissions=ChatPermissions(can_send_messages=True, can_send_other_messages=True))
        except Exception as e:
            logging.error(f"Unmute error: {e}")
        await query.answer(f"🔊 Пользователь размучен: @{target_username}")
    elif action == "cancel":
        await query.message.edit_reply_markup(reply_markup=None)
        await query.answer("❌ Репорт отменён.")
        return
    else:
        await query.answer("❌ Неизвестное действие.", show_alert=True)
        return

    await query.message.edit_reply_markup(reply_markup=None)
    await query.message.edit_text(query.message.text + "\n\n✅ Жалоба обработана.")

# Назад
@dp.callback_query(F.data == "menu_back")
async def back_to_menu(query: CallbackQuery):
    try:
        await query.message.edit_text("Главное меню:", reply_markup=await main_menu_keyboard(query.from_user.id, query.from_user.username))
    except:
        try:
            await query.message.delete()
        except:
            pass
        await bot.send_message(chat_id=query.from_user.id, text="Главное меню:", reply_markup=await main_menu_keyboard(query.from_user.id, query.from_user.username))

# /results
@dp.message(Command("results"))
async def cmd_results(message: Message, state: FSMContext):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    user_id = message.from_user.id
    c.execute("SELECT id, lobby_id, match_number FROM matches WHERE host_id=? AND status='drawn' ORDER BY created_at DESC LIMIT 1",
              (user_id,))
    match = c.fetchone()
    if not match:
        await message.answer("Нет активных матчей, где вы являетесь хостом.")
        return
    match_id, lobby_id, match_num = match
    await state.update_data(match_id=match_id, lobby_id=lobby_id, match_num=match_num)
    await message.answer("📸 Отправьте скриншот результатов:")
    await state.set_state(ResultStates.waiting_screenshot)

@dp.message(ResultStates.waiting_screenshot, F.photo)
async def results_screenshot(message: Message, state: FSMContext, bot: Bot):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    file_id = message.photo[-1].file_id
    await state.update_data(screenshot_id=file_id)
    await message.answer("📃 Введите счет игры (например: 13 1)")
    await state.set_state(ResultStates.waiting_score)

@dp.message(ResultStates.waiting_score)
async def results_score(message: Message, state: FSMContext, bot: Bot):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    data = await state.get_data()
    match_id = data['match_id']
    lobby_id = data['lobby_id']
    match_num = data['match_num']
    screenshot_id = data['screenshot_id']

    try:
        parts = message.text.strip().split()
        ct_score = int(parts[0])
        t_score = int(parts[1])
    except:
        await message.answer("Неверный формат. Введите два числа через пробел, например: 13 1")
        return

    c.execute("SELECT map_name FROM lobbies WHERE id=?", (lobby_id,))
    map_name = c.fetchone()[0]

    c.execute("UPDATE match_players SET team = CASE team WHEN 'CT' THEN 'T' WHEN 'T' THEN 'CT' END WHERE match_id=?", (match_id,))
    c.execute("UPDATE matches SET score=?, status='finished' WHERE id=?", (f"{ct_score}-{t_score}", match_id))
    conn.commit()

    c.execute("SELECT user_id, team FROM match_players WHERE match_id=?", (match_id,))
    players = c.fetchall()
    for uid, team in players:
        is_winner = (team == 'CT' and ct_score > t_score) or (team == 'T' and t_score > ct_score)
        premium = is_premium(uid)
        delta = (50 if premium else 25) if is_winner else (-15 if premium else -10)
        new_elo = max(0, get_elo(uid) + delta)
        if is_winner:
            c.execute("UPDATE users SET wins = wins + 1, elo = ? WHERE user_id=?", (new_elo, uid))
        else:
            c.execute("UPDATE users SET losses = losses + 1, elo = ? WHERE user_id=?", (new_elo, uid))
        increment_map_count(uid, map_name)
    conn.commit()

    c.execute("SELECT mode, map_name FROM lobbies WHERE id=?", (lobby_id,))
    lobby = c.fetchone()
    mode, map_name = lobby if lobby else ("?", "?")

    c.execute("SELECT user_id, team FROM match_players WHERE match_id=? ORDER BY team, user_id", (match_id,))
    rows = c.fetchall()
    ct_list = [(uid, get_nickname(uid), get_elo(uid)) for uid, team in rows if team == 'CT']
    t_list = [(uid, get_nickname(uid), get_elo(uid)) for uid, team in rows if team == 'T']

    host_nick = get_nickname(message.from_user.id)

    if ct_score > t_score:
        winner = "CT"
    elif t_score > ct_score:
        winner = "T"
    else:
        winner = "Ничья"

    result_text = (
        f"📊 РЕЗУЛЬТАТ МАТЧА\n"
        f"Лобби #{lobby_id} | Матч #{match_num} | host: {host_nick}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"[🗺] Current map: {map_name}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔵 CT: \n"
    )
    for i, (uid, nick, elo) in enumerate(ct_list, 1):
        result_text += f"{i}. {nick} (ELO: {elo})\n"
    result_text += f"\n🔴 T:\n"
    for i, (uid, nick, elo) in enumerate(t_list, 1):
        result_text += f"{i}. {nick} (ELO: {elo})\n"
    result_text += (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Победитель: {winner}"
    )

    await bot.send_photo(GROUP_CHAT_ID, screenshot_id, caption=result_text, message_thread_id=TOPIC_RESULTS)
    await message.answer("Результат сохранён и отправлен в тему результатов.")
    await state.clear()

# /playduo
@dp.message(Command("playduo"))
async def cmd_playduo(message: Message, state: FSMContext, bot: Bot):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    await message.answer("[👤] Отправьте nickname друга, с которым вы хотите быть в одной команде:")
    await state.set_state(DuoStates.waiting_nickname)

@dp.message(DuoStates.waiting_nickname)
async def duo_nickname(message: Message, state: FSMContext, bot: Bot):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    friend_nick = message.text.strip()
    if not friend_nick:
        await message.answer("Никнейм не может быть пустым.")
        return
    user_id = message.from_user.id
    c.execute("SELECT user_id FROM users WHERE nickname=?", (friend_nick,))
    friend_row = c.fetchone()
    if not friend_row:
        await message.answer("Пользователь с таким никнеймом не найден в системе.")
        await state.clear()
        return
    friend_id = friend_row[0]
    if friend_id == user_id:
        await message.answer("Нельзя добавить себя в дуо.")
        await state.clear()
        return

    if not is_registered(friend_id):
        await message.answer("Пользователь ещё не зарегистрирован в боте. Ему нужно написать /start.")
        await state.clear()
        return

    c.execute("INSERT OR REPLACE INTO duo_requests (sender_id, receiver_id) VALUES (?, ?)", (user_id, friend_id))
    conn.commit()

    sender_nick = get_nickname(user_id)
    try:
        await bot.send_message(
            friend_id,
            f"💌 Игрок {sender_nick} хочет быть с вами в DUO.\n"
            f"Примите или отклоните запрос:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Принять", callback_data=f"duo_accept_{user_id}")],
                [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"duo_decline_{user_id}")],
            ])
        )
        await message.answer(f"Запрос отправлен игроку {friend_nick}.")
    except Exception as e:
        await message.answer("Не удалось отправить запрос. Убедитесь, что пользователь начал диалог с ботом.")
        logging.error(f"Ошибка отправки дуо-запроса: {e}")
    await state.clear()

@dp.callback_query(F.data.startswith("duo_accept_"))
async def duo_accept(query: CallbackQuery, bot: Bot):
    sender_id = int(query.data.split("_")[-1])
    receiver_id = query.from_user.id
    c.execute("SELECT * FROM duo_requests WHERE sender_id=? AND receiver_id=?", (sender_id, receiver_id))
    if not c.fetchone():
        await query.answer("Запрос уже недействителен.")
        return

    sender_nick = get_nickname(sender_id)
    receiver_nick = get_nickname(receiver_id)
    c.execute("INSERT OR REPLACE INTO duos (user_id, friend_nickname) VALUES (?, ?)", (sender_id, receiver_nick))
    c.execute("INSERT OR REPLACE INTO duos (user_id, friend_nickname) VALUES (?, ?)", (receiver_id, sender_nick))
    conn.commit()

    c.execute("DELETE FROM duo_requests WHERE sender_id=? AND receiver_id=?", (sender_id, receiver_id))
    conn.commit()

    await query.message.edit_text(f"✅ Вы приняли запрос на DUO с игроком {sender_nick}.")
    await bot.send_message(sender_id, f"✅ {receiver_nick} принял ваш запрос на DUO!")

@dp.callback_query(F.data.startswith("duo_decline_"))
async def duo_decline(query: CallbackQuery, bot: Bot):
    sender_id = int(query.data.split("_")[-1])
    receiver_id = query.from_user.id
    c.execute("DELETE FROM duo_requests WHERE sender_id=? AND receiver_id=?", (sender_id, receiver_id))
    conn.commit()
    await query.message.edit_text("❌ Вы отклонили запрос на DUO.")
    await bot.send_message(sender_id, f"❌ Игрок отказался от дуо.")

# Управление аккаунтом (admin)
@dp.callback_query(F.data == "admin_manage_account")
async def admin_manage_account(query: CallbackQuery, state: FSMContext):
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    if not is_admin(query.from_user.username): await query.answer("Нет доступа."); return
    await query.message.edit_text("Введите @username или nickname игрока:")
    await state.set_state(ManageAccountStates.waiting_user)

@dp.message(ManageAccountStates.waiting_user)
async def manage_account_user(message: Message, state: FSMContext):
    arg = message.text.strip()
    target_id = None
    if arg.startswith('@'):
        username = arg.lstrip('@').lower()
        c.execute("SELECT user_id FROM users WHERE username=?", (username,))
        row = c.fetchone()
        if row: target_id = row[0]
    else:
        c.execute("SELECT user_id FROM users WHERE nickname=?", (arg,))
        row = c.fetchone()
        if row: target_id = row[0]
    if not target_id:
        await message.answer("Пользователь не найден.")
        await state.clear()
        return
    await state.update_data(target_id=target_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить Nickname", callback_data="acc_nick")],
        [InlineKeyboardButton(text="Изменить ID Standoff 2", callback_data="acc_sid")],
        [InlineKeyboardButton(text="Изменить аватарку", callback_data="acc_avatar")],
        [InlineKeyboardButton(text="Изменить баннер", callback_data="acc_banner")],
        [InlineKeyboardButton(text="Сбросить баннер", callback_data="acc_resetbanner")],
        [InlineKeyboardButton(text="Сбросить аватарку", callback_data="acc_resetavatar")],
        [InlineKeyboardButton(text="Отмена", callback_data="acc_cancel")],
    ])
    await message.answer("Выберите действие:", reply_markup=keyboard)
    await state.set_state(ManageAccountStates.waiting_action)

@dp.callback_query(ManageAccountStates.waiting_action, F.data.startswith("acc_"))
async def manage_account_action(query: CallbackQuery, state: FSMContext, bot: Bot):
    action = query.data[4:]
    data = await state.get_data()
    target_id = data.get("target_id")
    if action == "cancel":
        await query.message.edit_text("Действие отменено.")
        await state.clear()
        return
    if action == "nick":
        await query.message.edit_text("Введите новый никнейм:")
        await state.update_data(action="nick")
        await state.set_state(ManageAccountStates.waiting_value)
    elif action == "sid":
        await query.message.edit_text("Введите новый ID Standoff 2:")
        await state.update_data(action="sid")
        await state.set_state(ManageAccountStates.waiting_value)
    elif action == "avatar":
        await query.message.edit_text("Отправьте новую аватарку:")
        await state.update_data(action="avatar")
        await state.set_state(ManageAccountStates.waiting_value)
    elif action == "banner":
        await query.message.edit_text("Отправьте новый баннер:")
        await state.update_data(action="banner")
        await state.set_state(ManageAccountStates.waiting_value)
    elif action == "resetbanner":
        c.execute("UPDATE users SET custom_banner=NULL WHERE user_id=?", (target_id,))
        conn.commit()
        banner_cache.pop(target_id, None)
        await query.message.edit_text("Баннер сброшен.")
        await state.clear()
    elif action == "resetavatar":
        c.execute("UPDATE users SET custom_avatar=NULL WHERE user_id=?", (target_id,))
        conn.commit()
        avatar_cache.pop(target_id, None)
        await query.message.edit_text("Аватарка сброшена.")
        await state.clear()

@dp.message(ManageAccountStates.waiting_value)
async def manage_account_value(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("target_id")
    action = data.get("action")
    if action in ("nick", "sid"):
        value = message.text.strip()
        if action == "nick":
            c.execute("UPDATE users SET nickname=? WHERE user_id=?", (value, target_id))
        else:
            c.execute("UPDATE users SET standoff_id=? WHERE user_id=?", (value, target_id))
        conn.commit()
        await message.answer("Обновлено.")
    elif action in ("avatar", "banner"):
        if not message.photo:
            await message.answer("Отправьте изображение.")
            return
        file_id = message.photo[-1].file_id
        if action == "avatar":
            c.execute("UPDATE users SET custom_avatar=? WHERE user_id=?", (file_id, target_id))
            avatar_cache.pop(target_id, None)
        else:
            c.execute("UPDATE users SET custom_banner=? WHERE user_id=?", (file_id, target_id))
            banner_cache.pop(target_id, None)
        conn.commit()
        await message.answer("Обновлено.")
    await state.clear()

# ------------------------------------------------------------
# Инициализация и восстановление
# ------------------------------------------------------------
def init_lobbies():
    c.execute("DELETE FROM lobbies WHERE thread_id NOT IN (18,20,12,13,10,2)"); conn.commit()
    for mode, tid in [("5x5",18),("5x5",20),("2x2",12),("2x2",13),("1x1",10),("1x1",2)]:
        c.execute("SELECT id FROM lobbies WHERE mode=? AND thread_id=?", (mode, tid))
        if not c.fetchone():
            map_name = random.choice(list(map_images_cache.keys())) if map_images_cache else None
            c.execute("INSERT OR IGNORE INTO lobbies (mode, thread_id, map_name) VALUES (?,?,?)", (mode, tid, map_name))
    conn.commit()

async def restore_all_lobby_posts():
    c.execute("SELECT id FROM lobbies WHERE message_id IS NULL")
    lobbies_to_restore = [row[0] for row in c.fetchall()]
    for lobby_id in lobbies_to_restore:
        await update_lobby_post(bot, lobby_id)
        await asyncio.sleep(0.5)

async def main():
    global bot
    init_db()                     # Инициализация БД
    download_font()
    download_default_banner()
    load_map_images()
    init_lobbies()

    bot = Bot(token=TOKEN)
    dp.include_router(report_router)
    await restore_all_lobby_posts()
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
