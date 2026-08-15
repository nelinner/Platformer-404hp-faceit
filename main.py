import asyncio
import logging
import random
import os
import sys
import requests
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from math import pi, cos, sin
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    BufferedInputFile, InputMediaPhoto, ChatPermissions,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters.callback_data import CallbackData
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from config import *
from database import *

# ------------------------------------------------------------
# Глобальные объекты
# ------------------------------------------------------------
dp = Dispatcher(storage=MemoryStorage())
report_router = Router()
bot: Bot = None

font_cache = {}
map_images_cache = {}
avatar_cache = {}
banner_cache = {}
menu_messages: dict[int, Message] = {}

# ------------------------------------------------------------
# Вспомогательные функции
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

def player_display_name(user_id):
    nick = get_nickname(user_id)
    c.execute("SELECT username FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row and row[0]:
        return f"{nick} (@{row[0]})"
    return nick

def is_owner_of_menu(query: CallbackQuery) -> bool:
    user_id = query.from_user.id
    if user_id not in menu_messages:
        return False
    saved_msg = menu_messages[user_id]
    return (query.message.message_id == saved_msg.message_id and
            query.message.chat.id == saved_msg.chat.id)

# ------------------------------------------------------------
# Генерация изображений
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
    badge = get_user_badge(user_id)
    frame = get_user_frame(user_id)

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

    frame_color = FACEIT_ORANGE
    frame_width = 4
    if frame == "Gold frame":
        frame_color = (255, 215, 0)
        frame_width = 6
    elif frame == "Snow frame":
        frame_color = (173, 216, 230)
        frame_width = 6
    elif frame == "Infernal frame":
        frame_color = (255, 69, 0)
        frame_width = 6

    draw.ellipse([avatar_x-5, avatar_cy-avatar_size//2-5,
                  avatar_x+avatar_size+5, avatar_cy+avatar_size//2+5],
                 outline=frame_color, width=frame_width)

    try:
        font_nick = ImageFont.truetype(FONT_PATH, 34)
        font_id = ImageFont.truetype(FONT_PATH, 22)
    except:
        font_nick = ImageFont.load_default()
        font_id = ImageFont.load_default()
    draw.text((avatar_x + avatar_size + 20, cell_y+30), nickname, font=font_nick, fill='white')
    draw.text((avatar_x + avatar_size + 20, cell_y+80), f"ID: {standoff_id}", font=font_id, fill=TEXT_GRAY)

    if badge:
        badge_text = badge.upper()
        badge_bg = (255,0,0) if badge == "youtuber" else (0,0,0)
        badge_fg = (255,255,255)
        font_badge = get_font(16)
        bbox = draw.textbbox((0,0), badge_text, font=font_badge)
        bw, bh = bbox[2]-bbox[0]+12, bbox[3]-bbox[1]+6
        draw.rounded_rectangle([avatar_x+avatar_size+20, cell_y+110, avatar_x+avatar_size+20+bw, cell_y+110+bh],
                               radius=6, fill=badge_bg, outline=badge_fg)
        draw.text((avatar_x+avatar_size+26, cell_y+112), badge_text, font=font_badge, fill=badge_fg)

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

def draw_player_slot(draw, x, y, w, h, user_id, elo, sid, avatar=None, banner=None,
                     is_owner=False, is_admin=False, premium=False, compact=False,
                     show_username=True):
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

        # Оптимизированный оверлей
        overlay = Image.new('RGBA', (w, overlay_h), (0,0,0,80))
        draw._image.paste(overlay, (x, y + h - overlay_h))

        avatar_cx = x + (avatar_r + 18)
        avatar_cy = y + h//2
        if avatar:
            mask = Image.new('L', (avatar_r*2, avatar_r*2), 0)
            ImageDraw.Draw(mask).ellipse((0,0,avatar_r*2,avatar_r*2), fill=255)
            avatar_resized = avatar.resize((avatar_r*2, avatar_r*2), Image.Resampling.LANCZOS)
            draw._image.paste(avatar_resized, (avatar_cx-avatar_r, avatar_cy-avatar_r), mask)
            draw.ellipse([avatar_cx-avatar_r-2, avatar_cy-avatar_r-2,
                          avatar_cx+avatar_r+2, avatar_cy+avatar_r+2],
                         outline=FACEIT_ORANGE, width=3)
        else:
            draw.ellipse([avatar_cx-avatar_r, avatar_cy-avatar_r,
                          avatar_cx+avatar_r, avatar_cy+avatar_r],
                         fill=(35,38,55), outline=FACEIT_ORANGE, width=3)

        if show_username:
            display_name = player_display_name(user_id)
        else:
            display_name = get_nickname(user_id)

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
                elo = get_elo(pid)
                sid = get_standoff_id(pid)
                av = avatars[pid]
                bn = banners[pid]
                c.execute("SELECT username FROM users WHERE user_id=?", (pid,))
                uname = c.fetchone()
                is_own = (pid == OWNER_ID)
                is_adm = uname and is_admin(uname[0])
                premium = is_premium(pid)
                draw_player_slot(draw, x, y, slot_w, slot_h, pid, elo, sid, av, bn, is_own, is_adm, premium,
                                 show_username=False)
            else:
                draw.rounded_rectangle([x, y, x+slot_w, y+slot_h], radius=18, fill=(255,255,255,12), outline=(255,255,255,40))
                font_wait = get_font(32)
                wait_text = "WAITING\nFOR PLAYER..."
                lines = wait_text.split('\n')
                # Исправлено: расчёт высоты строки через textbbox
                bbox = draw.textbbox((0,0), "WAITING", font=font_wait)
                line_h = (bbox[3] - bbox[1]) * 1.2
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

        host_text = f"👑 Хост: {player_display_name(host_id)} | Матч #{match_num}"
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
            draw_player_slot(draw, ct_x+30, y, ct_w-60, slot_h, pid, get_elo(pid), get_standoff_id(pid),
                             avatars.get(pid), banners.get(pid), is_own, is_adm, premium, compact=True)

        for i, pid in enumerate(t_ids[:5]):
            y = slot_y + i * (slot_h + 10)
            c.execute("SELECT username FROM users WHERE user_id=?", (pid,))
            uname = c.fetchone()
            is_own = (pid == OWNER_ID)
            is_adm = uname and is_admin(uname[0])
            premium = is_premium(pid)
            draw_player_slot(draw, t_x+30, y, ct_w-60, slot_h, pid, get_elo(pid), get_standoff_id(pid),
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
    img_bytes = img_data.read() if img_data else None

    c.execute("SELECT mode, thread_id, match_number FROM lobbies WHERE id=?", (lobby_id,))
    mode, thread_id, match_num = c.fetchone()

    c.execute("SELECT user_id FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
    players = c.fetchall()

    lines = [f"👤 {get_nickname(p[0])} | ID {get_standoff_id(p[0])}" for p in players]
    header = f"🎮 Лобби {mode} · lobby #{lobby_id} | Матч #{match_num}"
    text = f"{header}\nИгроки: {len(players)}/{MAX_PLAYERS[mode]}\n——————\n" + ("\n".join(lines) if lines else "Пока никого нет.") + "\n——————"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚪 Вступить", callback_data=f"lobby_join_{lobby_id}"),
         InlineKeyboardButton(text="🔙 Выйти", callback_data=f"lobby_leave_{lobby_id}")]
    ])

    c.execute("SELECT message_id, thread_id FROM lobbies WHERE id=?", (lobby_id,))
    msg_id, thread_id = c.fetchone()

    if msg_id:
        try:
            if img_bytes:
                photo = BufferedInputFile(img_bytes, filename="lobby.png")
                await bot.edit_message_media(
                    chat_id=GROUP_CHAT_ID,
                    message_id=msg_id,
                    media=InputMediaPhoto(media=photo, caption=text),
                    reply_markup=keyboard,
                    message_thread_id=thread_id  # добавлено
                )
            else:
                await bot.edit_message_text(
                    chat_id=GROUP_CHAT_ID,
                    message_id=msg_id,
                    text=text,
                    reply_markup=keyboard,
                    message_thread_id=thread_id  # добавлено
                )
            return
        except Exception as e:
            logging.warning(f"Не удалось отредактировать сообщение {msg_id}: {e}")
            try:
                await bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=msg_id)
            except Exception as del_e:
                logging.warning(f"Не удалось удалить сообщение {msg_id}: {del_e}")

    if img_bytes:
        photo = BufferedInputFile(img_bytes, filename="lobby.png")
        msg = await bot.send_photo(GROUP_CHAT_ID, photo, caption=text, reply_markup=keyboard, message_thread_id=thread_id)
    else:
        msg = await bot.send_message(GROUP_CHAT_ID, text, reply_markup=keyboard, message_thread_id=thread_id)

    c.execute("UPDATE lobbies SET message_id=? WHERE id=?", (msg.message_id, lobby_id))
    conn.commit()

async def start_draw(bot: Bot, lobby_id: int, mode: str):
    c.execute("SELECT user_id FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
    players = [row[0] for row in c.fetchall()]
    if len(players) < MAX_PLAYERS[mode]:
        return

    host_id = None
    for pid in players:
        c.execute("SELECT username FROM users WHERE user_id=?", (pid,))
        row = c.fetchone()
        if row and is_admin(row[0]):
            host_id = pid
            break
    if not host_id:
        host_id = max(players, key=get_elo)

    duo_pairs = set()
    for pid in players:
        c.execute("SELECT friend_nickname FROM duos WHERE user_id=?", (pid,))
        for (fnick,) in c.fetchall():
            friend_id = None
            for p2 in players:
                if get_nickname(p2).lower() == fnick.lower():
                    friend_id = p2
                    break
            if friend_id and friend_id != pid:
                pair = tuple(sorted((pid, friend_id)))
                duo_pairs.add(pair)

    half = MAX_PLAYERS[mode] // 2
    ct = []
    t = []
    used = set()

    duo_list = list(duo_pairs)
    random.shuffle(duo_list)
    for p1, p2 in duo_list:
        if p1 in used or p2 in used:
            continue
        if len(ct) <= len(t):
            target_team = ct
        else:
            target_team = t
        if len(target_team) + 2 <= half:
            target_team.append(p1)
            target_team.append(p2)
            used.add(p1)
            used.add(p2)

    remaining = [p for p in players if p not in used]
    random.shuffle(remaining)
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

    now = datetime.now().isoformat()
    c.execute("SELECT match_number, map_name FROM lobbies WHERE id=?", (lobby_id,))
    match_num, map_name = c.fetchone()
    c.execute("INSERT INTO matches (lobby_id, match_number, status, created_at, host_id) VALUES (?,?, 'drawn',?,?)",
              (lobby_id, match_num, now, host_id))
    match_id = c.lastrowid
    for p in ct:
        c.execute("INSERT INTO match_players (match_id, user_id, team) VALUES (?,?,'CT')", (match_id, p))
    for p in t:
        c.execute("INSERT INTO match_players (match_id, user_id, team) VALUES (?,?,'T')", (match_id, p))
    c.execute("UPDATE lobbies SET match_number = match_number + 1 WHERE id=?", (lobby_id,))
    c.execute("DELETE FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
    conn.commit()

    img_data = await generate_draft_image(bot, lobby_id, mode, ct, t, map_name, match_num, host_id)
    if img_data:
        photo = BufferedInputFile(img_data.read(), filename="draft.png")
        await bot.send_photo(GROUP_CHAT_ID, photo, message_thread_id=TOPIC_DRAW)

    host_nick = player_display_name(host_id)
    text_draw = (
        f"ℹ️ Жеребьёвка игроков по командам\n"
        f"404hp faceit | host by: {host_nick}\n"
        f"——————\n"
        f"⚔️ Counter-terrorists:\n"
    )
    for p in ct:
        text_draw += f"> 👤 {player_display_name(p)} | ID {get_standoff_id(p)}\n"
    text_draw += "\n🔫 Terrorists:\n"
    for p in t:
        text_draw += f"> 👤 {player_display_name(p)} | ID {get_standoff_id(p)}\n"
    text_draw += (
        "——————\n"
        f"ℹ️ host lobby, должен пригласить вас в лобби в течении 5-10 минут, может с маленькой задержкой, "
        f"если в течении 11 минут вас не пригласили то обратитесь в тикет поддержки"
    )
    await bot.send_message(GROUP_CHAT_ID, text_draw, message_thread_id=TOPIC_DRAW)

    await update_lobby_post(bot, lobby_id)

# ------------------------------------------------------------
# Автосброс лобби
# ------------------------------------------------------------
async def auto_reset_lobbies():
    while True:
        await asyncio.sleep(15 * 60)
        try:
            c.execute("SELECT id FROM lobbies")
            lobbies = [row[0] for row in c.fetchall()]
            for lobby_id in lobbies:
                c.execute("SELECT message_id FROM lobbies WHERE id=?", (lobby_id,))
                msg_id = c.fetchone()[0]
                if msg_id:
                    try:
                        await bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=msg_id)
                    except Exception as e:
                        logging.warning(f"Не удалось удалить сообщение лобби {lobby_id}: {e}")
                c.execute("DELETE FROM lobby_registrations WHERE lobby_id=?", (lobby_id,))
                new_map = random.choice(list(map_images_cache.keys())) if map_images_cache else None
                c.execute("UPDATE lobbies SET map_name=?, message_id=NULL WHERE id=?", (new_map, lobby_id))
                conn.commit()
                await update_lobby_post(bot, lobby_id)
            logging.info("Автосброс лобби выполнен")
        except Exception as e:
            logging.error(f"Ошибка в автосбросе лобби: {e}")

# ------------------------------------------------------------
# FSM, CallbackData
# ------------------------------------------------------------
class RegStates(StatesGroup):
    nick = State()
    sid = State()
    confirm = State()

class AdminStates(StatesGroup):
    waiting_add = State()
    waiting_remove = State()
    waiting_replace_new_user = State()
    waiting_new_score = State()
    waiting_coins_user = State()
    waiting_coins_amount = State()

class BanPlayerStates(StatesGroup):
    waiting_nick = State()
    waiting_time_choice = State()
    waiting_custom_time = State()
    waiting_reason = State()

class AvatarStates(StatesGroup):
    waiting_avatar = State()

class BannerStates(StatesGroup):
    waiting_banner = State()

class ResultStates(StatesGroup):
    waiting_screenshot = State()
    waiting_score = State()

class DuoStates(StatesGroup):
    waiting_nickname = State()

class ManageAccountStates(StatesGroup):
    waiting_user = State()
    waiting_action = State()
    waiting_value = State()

class CancelMatchStates(StatesGroup):
    waiting_reason = State()

class ReportStates(StatesGroup):
    waiting_target = State()
    waiting_text = State()

class ProblemTicketStates(StatesGroup):
    waiting_text = State()
    waiting_screenshot_choice = State()
    waiting_screenshot = State()
    waiting_admin_reply = State()

class PromoStates(StatesGroup):
    waiting_code = State()

class PromoCreationStates(StatesGroup):
    waiting_code_text = State()
    waiting_activations = State()
    waiting_custom_activations = State()
    waiting_reward_type = State()
    waiting_reward_amount = State()
    waiting_custom_amount = State()

class ReportAction(CallbackData, prefix="report"):
    action: str
    report_id: int

class TicketAction(CallbackData, prefix="ticket"):
    action: str
    ticket_id: int
    user_id: int

# ------------------------------------------------------------
# Главное меню
# ------------------------------------------------------------
async def main_menu_keyboard(user_id, username):
    btns = [
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile")],
        [InlineKeyboardButton(text="👥 My duo", callback_data="my_duo")],
        [InlineKeyboardButton(text="🤝 Пригласить", callback_data="menu_invite")],
        [InlineKeyboardButton(text="🎟 Промокод", callback_data="menu_promo")],
        [InlineKeyboardButton(text="🔎 Поиск лобби", callback_data="menu_search")],
        [InlineKeyboardButton(text="🎮 Мои лобби", callback_data="my_lobbies")],
        [InlineKeyboardButton(text="🏆 Лидерборд", callback_data="menu_leaderboard")],
        [InlineKeyboardButton(text="💰 Магазин", callback_data="menu_shop")],
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
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        inviter_id = int(args[1].split("_")[1])
        await state.update_data(inviter_id=inviter_id)

    if is_registered(user_id):
        if is_banned(user_id):
            await message.answer("❌ Вы забанены в боте.")
            return
        msg = await message.answer("Главное меню:", reply_markup=await main_menu_keyboard(user_id, message.from_user.username))
        menu_messages[user_id] = msg
        return
    await message.answer("Введите игровой никнейм из Standoff 2:")
    await state.set_state(RegStates.nick)

@dp.message(RegStates.nick)
async def reg_nick(message: Message, state: FSMContext):
    await state.update_data(nick=message.text.strip())
    await message.answer("Введите ваш ID из Standoff 2:")
    await state.set_state(RegStates.sid)

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
        user = query.from_user
        data = await state.get_data()
        inviter_id = data.get("inviter_id")
        c.execute("INSERT OR REPLACE INTO users (user_id, username, nickname, standoff_id, elo, wins, losses, registered) VALUES (?,?,?,?,0,0,0,1)",
                  (user.id, user.username or "", data['nick'], data['sid']))
        conn.commit()

        if inviter_id and inviter_id != user.id:
            if create_referral(user.id, inviter_id):
                add_premium_days(inviter_id, 1)
                add_coins(inviter_id, 5)

        await query.message.edit_text("Регистрация успешна! ✅")
        msg = await bot.send_message(chat_id=user.id,
                                     text="Главное меню:",
                                     reply_markup=await main_menu_keyboard(user.id, user.username))
        menu_messages[user.id] = msg
    else:
        await query.message.edit_text("Регистрация отменена.")
    await state.clear()

# Профиль
@dp.callback_query(F.data == "menu_profile")
async def profile(query: CallbackQuery, bot: Bot):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if not is_registered(query.from_user.id): await query.answer("Сначала /start", show_alert=True); return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    user_id = query.from_user.id; username = query.from_user.username
    nick = get_nickname(user_id); sid = get_standoff_id(user_id)
    elo, wins, losses = get_user_stats(user_id)
    total = wins + losses
    winrate = (wins / total * 100) if total > 0 else 0
    coins = get_coins(user_id)
    info_text = (
        f"ℹ️ Информация об игроке:\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"[👤] Nickname: {nick}\n"
        f"[🪪] ID: {sid}\n"
        f"[💲] Coins: {coins}\n"
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
    msg = await query.message.answer_photo(photo, caption=info_text, reply_markup=profile_kb)
    menu_messages[user_id] = msg
    await query.message.delete()

@dp.callback_query(F.data == "set_avatar")
async def set_avatar_prompt(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
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
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
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
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    c.execute("UPDATE users SET custom_banner=NULL WHERE user_id=?", (query.from_user.id,))
    conn.commit()
    banner_cache.pop(query.from_user.id, None)
    await query.answer("Баннер сброшен.")

@dp.callback_query(F.data == "reset_avatar")
async def reset_avatar(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    c.execute("UPDATE users SET custom_avatar=NULL WHERE user_id=?", (query.from_user.id,))
    conn.commit()
    avatar_cache.pop(query.from_user.id, None)
    await query.answer("Аватарка сброшена.")

# ------------------------------------------------------------
# My duo
# ------------------------------------------------------------
@dp.callback_query(F.data == "my_duo")
async def my_duo(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return

    user_id = query.from_user.id
    partner = get_duo_partner(user_id)

    if partner:
        partner_name = player_display_name(partner)
        text = (
            f"ℹ️ Ваше DUO\n"
            f"———\n"
            f"[👤] С кем вы находитесь в duo: {partner_name}\n"
            f"———"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить DUO", callback_data="duo_cancel")],
            [InlineKeyboardButton(text="🔎 Пригласить игрока в duo", callback_data="duo_invite")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")],
        ])
    else:
        text = (
            f"ℹ️ Ваше DUO\n"
            f"———\n"
            f"[👤] Вы не находитесь в DUO.\n"
            f"———"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Пригласить игрока в duo", callback_data="duo_invite")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")],
        ])

    msg = await query.message.edit_text(text, reply_markup=keyboard)
    menu_messages[user_id] = msg

@dp.callback_query(F.data == "duo_cancel")
async def duo_cancel(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return

    user_id = query.from_user.id
    remove_duo(user_id)
    await query.answer("DUO отменено.")
    await my_duo(query)

@dp.callback_query(F.data == "duo_invite")
async def duo_invite(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return

    await state.set_state(DuoStates.waiting_nickname)
    msg = await query.message.edit_text("[👤] Отправьте nickname друга, с которым вы хотите быть в одной команде:")
    menu_messages[query.from_user.id] = msg

@dp.message(Command("playduo"))
async def cmd_playduo(message: Message, state: FSMContext, bot: Bot):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    await message.answer("[👤] Отправьте nickname друга, с которым вы хотите быть в одной команде:")
    await state.set_state(DuoStates.waiting_nickname)

@dp.message(DuoStates.waiting_nickname, F.text)
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

# ------------------------------------------------------------
# Поиск лобби
# ------------------------------------------------------------
@dp.callback_query(F.data == "menu_search")
async def search_mode_menu(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    msg = await query.message.edit_text("Выберите режим для поиска лобби:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5x5", callback_data="search_5x5")],
        [InlineKeyboardButton(text="2x2", callback_data="search_2x2")],
        [InlineKeyboardButton(text="1x1", callback_data="search_1x1")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")]]))
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data.startswith("search_"))
async def show_lobbies_by_mode(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
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
    msg = await query.message.edit_text("Доступные лобби:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    menu_messages[query.from_user.id] = msg

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
    c.execute("SELECT 1 FROM lobby_registrations WHERE lobby_id=? AND user_id=?", (lobby_id, user_id))
    if not c.fetchone():
        await query.answer("Вы не в этом лобби.", show_alert=True)
        return
    c.execute("DELETE FROM lobby_registrations WHERE lobby_id=? AND user_id=?", (lobby_id, user_id)); conn.commit()
    await query.answer("Вы вышли.")
    await update_lobby_post(bot, lobby_id)

# ------------------------------------------------------------
# Админ панель
# ------------------------------------------------------------
@dp.callback_query(F.data == "menu_admin")
async def admin_menu(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    if not is_admin(query.from_user.username): await query.answer("Нет доступа."); return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_list")],
        [InlineKeyboardButton(text="🛡️ Управление админами", callback_data="admin_manage")],
        [InlineKeyboardButton(text="🎮 Управление лобби", callback_data="admin_lobbies")],
        [InlineKeyboardButton(text="⚙️ Управление lobby", callback_data="admin_manage_lobby")],
        [InlineKeyboardButton(text="⚙️ Управление результатами", callback_data="admin_manage_results")],
        [InlineKeyboardButton(text="⚙️ Управление аккаунтом", callback_data="admin_manage_account")],
        [InlineKeyboardButton(text="🏞️ Управление картой", callback_data="admin_manage_map")],
        [InlineKeyboardButton(text="🔴 Бан игрока", callback_data="admin_ban_player")],
        [InlineKeyboardButton(text="⚙️ Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="💰 Выдать coins", callback_data="admin_give_coins")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")],
    ])
    if query.from_user.id == OWNER_ID:
        keyboard.inline_keyboard.insert(3, [InlineKeyboardButton(text="🗑 Сбросить ВСЕ лобби", callback_data="admin_reset_all_lobbies")])
    msg = await query.message.edit_text("Админ панель:", reply_markup=keyboard)
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data == "admin_reset_all_lobbies")
async def admin_reset_all_lobbies(query: CallbackQuery, bot: Bot):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
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

# ========== Управление картой ==========
@dp.callback_query(F.data == "admin_manage_map")
async def admin_manage_map_lobby_list(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if not is_admin(query.from_user.username): await query.answer("Нет доступа."); return
    c.execute("SELECT id, mode, map_name FROM lobbies")
    lobbies = c.fetchall()
    if not lobbies:
        await query.answer("Нет лобби.", show_alert=True)
        return
    buttons = []
    for lid, mode, map_name in lobbies:
        buttons.append([InlineKeyboardButton(text=f"Лобби {lid} ({mode}) - текущая: {map_name}", callback_data=f"admin_maplobby_{lid}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu_admin")])
    msg = await query.message.edit_text("Выберите лобби для смены карты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data.startswith("admin_maplobby_"))
async def admin_manage_map_choose_map(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    lobby_id = int(query.data.split("_")[-1])
    maps = list(map_images_cache.keys())
    if not maps:
        await query.answer("Карты не загружены.", show_alert=True)
        return
    buttons = []
    for map_name in maps:
        buttons.append([InlineKeyboardButton(text=map_name, callback_data=f"admin_setmap_{lobby_id}_{map_name}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_manage_map")])
    msg = await query.message.edit_text("Выберите новую карту:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data.startswith("admin_setmap_"))
async def admin_manage_map_set(query: CallbackQuery, bot: Bot):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    # Исправлен парсинг с учетом возможных "_" в названии карты
    parts = query.data.split("_", 3)
    lobby_id = int(parts[2])
    map_name = parts[3]
    c.execute("UPDATE lobbies SET map_name=? WHERE id=?", (map_name, lobby_id))
    conn.commit()
    await update_lobby_post(bot, lobby_id)
    await query.answer(f"Карта лобби #{lobby_id} изменена на {map_name}")
    await admin_manage_map_lobby_list(query)

# ========== Бан игрока ==========
@dp.callback_query(F.data == "admin_ban_player")
async def admin_ban_player_start(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if not is_admin(query.from_user.username): await query.answer("Нет доступа."); return
    await state.set_state(BanPlayerStates.waiting_nick)
    msg = await query.message.edit_text("Введите nickname игрока для бана:")
    menu_messages[query.from_user.id] = msg

@dp.message(BanPlayerStates.waiting_nick, F.text)
async def admin_ban_player_nick(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        await message.answer("Нет доступа."); await state.clear(); return
    nick = message.text.strip()
    c.execute("SELECT user_id FROM users WHERE nickname=?", (nick,))
    row = c.fetchone()
    if not row:
        await message.answer("Пользователь с таким никнеймом не найден.")
        await state.clear()
        return
    user_id = row[0]
    if user_id == OWNER_ID:
        await message.answer("Нельзя забанить создателя.")
        await state.clear()
        return
    await state.update_data(target_id=user_id, target_nick=nick)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="10 минут", callback_data="bantime_10")],
        [InlineKeyboardButton(text="1 час", callback_data="bantime_60")],
        [InlineKeyboardButton(text="1 день", callback_data="bantime_1440")],
        [InlineKeyboardButton(text="1 месяц", callback_data="bantime_43200")],
        [InlineKeyboardButton(text="Своё время", callback_data="bantime_custom")],
        [InlineKeyboardButton(text="Отмена", callback_data="bantime_cancel")],
    ])
    await message.answer(f"Выберите время бана для {nick}:", reply_markup=keyboard)
    await state.set_state(BanPlayerStates.waiting_time_choice)

@dp.callback_query(BanPlayerStates.waiting_time_choice, F.data.startswith("bantime_"))
async def admin_ban_player_time_choice(query: CallbackQuery, state: FSMContext):
    data = query.data
    if data == "bantime_cancel":
        await state.clear()
        await query.message.edit_text("Отменено.")
        return
    if data == "bantime_custom":
        await query.message.edit_text("Введите количество минут бана:")
        await state.set_state(BanPlayerStates.waiting_custom_time)
        return
    minutes = int(data.split("_")[1])
    await state.update_data(duration=minutes)
    await query.message.edit_text("Введите причину бана:")
    await state.set_state(BanPlayerStates.waiting_reason)

@dp.message(BanPlayerStates.waiting_custom_time, F.text)
async def admin_ban_player_custom_time(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        await message.answer("Нет доступа."); await state.clear(); return
    try:
        minutes = int(message.text.strip())
        if minutes <= 0:
            raise ValueError
    except:
        await message.answer("Введите положительное число минут.")
        return
    await state.update_data(duration=minutes)
    await message.answer("Введите причину бана:")
    await state.set_state(BanPlayerStates.waiting_reason)

@dp.message(BanPlayerStates.waiting_reason, F.text)
async def admin_ban_player_reason(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.username):
        await message.answer("Нет доступа."); await state.clear(); return
    reason = message.text.strip()
    if not reason:
        await message.answer("Причина не может быть пустой.")
        return
    data = await state.get_data()
    target_id = data["target_id"]
    target_nick = data["target_nick"]
    duration = data["duration"]
    now = datetime.now()
    banned_until = now + timedelta(minutes=duration)
    c.execute("UPDATE users SET banned_until=? WHERE user_id=?", (banned_until.isoformat(), target_id))
    conn.commit()
    admin_username = message.from_user.username or "без username"
    c.execute("SELECT username FROM users WHERE user_id=?", (target_id,))
    target_username = c.fetchone()
    target_username = target_username[0] if target_username and target_username[0] else "без username"
    ban_text = (
        f"🔴 Забанен игрок\n"
        f"——————\n"
        f"[👤] Admin: @{admin_username}\n"
        f"[👤] Player: @{target_username}\n"
        f"——————\n"
        f"[📃] Причина бана: {reason}\n"
        f"[🕓] Time: {duration} мин.\n"
        f"——————\n"
        f"[✅] Бан будет снят: {banned_until.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await bot.send_message(GROUP_CHAT_ID, ban_text, message_thread_id=TOPIC_BAN)
    await message.answer(f"Пользователь {target_nick} забанен на {duration} минут.")
    await state.clear()

# ========== Выдать Coins ==========
@dp.callback_query(F.data == "admin_give_coins")
async def admin_give_coins_start(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if not is_admin(query.from_user.username):
        await query.answer("Нет доступа.")
        return
    await state.set_state(AdminStates.waiting_coins_user)
    msg = await query.message.edit_text("📃 Введите @username игрока:")
    menu_messages[query.from_user.id] = msg

@dp.message(AdminStates.waiting_coins_user, F.text)
async def admin_give_coins_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        await message.answer("Нет доступа."); await state.clear(); return
    identifier = message.text.strip()
    target_id = find_user(identifier)
    if not target_id:
        await message.answer("Пользователь не найден.")
        await state.clear()
        return
    await state.update_data(target_id=target_id)
    await message.answer("📃 Введите количество coins для выдачи:")
    await state.set_state(AdminStates.waiting_coins_amount)

@dp.message(AdminStates.waiting_coins_amount, F.text)
async def admin_give_coins_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        await message.answer("Нет доступа."); await state.clear(); return
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except:
        await message.answer("Введите положительное число.")
        return
    data = await state.get_data()
    target_id = data["target_id"]
    add_coins(target_id, amount)
    await message.answer(f"✅ Выдано {amount} Coins пользователю {get_nickname(target_id)}.")
    await state.clear()

# ========== Создание промокода ==========
@dp.callback_query(F.data == "admin_create_promo")
async def admin_create_promo_start(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if not is_admin(query.from_user.username):
        await query.answer("Нет доступа.")
        return
    await state.set_state(PromoCreationStates.waiting_code_text)
    msg = await query.message.edit_text("📃 Введите текст промокода:")
    menu_messages[query.from_user.id] = msg

@dp.message(PromoCreationStates.waiting_code_text, F.text)
async def promo_code_text(message: Message, state: FSMContext):
    code_text = message.text.strip().upper()
    if not code_text:
        await message.answer("Промокод не может быть пустым.")
        return
    await state.update_data(code_text=code_text)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1", callback_data="promo_act_1")],
        [InlineKeyboardButton(text="10", callback_data="promo_act_10")],
        [InlineKeyboardButton(text="100", callback_data="promo_act_100")],
        [InlineKeyboardButton(text="1000", callback_data="promo_act_1000")],
        [InlineKeyboardButton(text="10000", callback_data="promo_act_10000")],
        [InlineKeyboardButton(text="Своё", callback_data="promo_act_custom")],
        [InlineKeyboardButton(text="Отмена", callback_data="promo_cancel")],
    ])
    await message.answer("Выберите количество активаций промокода:", reply_markup=keyboard)
    await state.set_state(PromoCreationStates.waiting_activations)

@dp.callback_query(PromoCreationStates.waiting_activations, F.data.startswith("promo_act_"))
async def promo_activations_choice(query: CallbackQuery, state: FSMContext):
    data = query.data
    if data == "promo_act_custom":
        await query.message.edit_text("Введите количество активаций:")
        await state.set_state(PromoCreationStates.waiting_custom_activations)
        return
    if data == "promo_cancel":
        await state.clear()
        await query.message.edit_text("Создание промокода отменено.")
        return
    activations = int(data.split("_")[-1])
    await state.update_data(activations=activations)
    await query.message.edit_text("Выберите тип награды:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Премиум", callback_data="promo_type_premium")],
        [InlineKeyboardButton(text="Coins", callback_data="promo_type_coins")],
        [InlineKeyboardButton(text="Баланс (старый)", callback_data="promo_type_balance")],
        [InlineKeyboardButton(text="Отмена", callback_data="promo_cancel")],
    ]))
    await state.set_state(PromoCreationStates.waiting_reward_type)

@dp.message(PromoCreationStates.waiting_custom_activations, F.text)
async def promo_custom_activations(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        await message.answer("Нет доступа."); await state.clear(); return
    try:
        activations = int(message.text.strip())
        if activations <= 0:
            raise ValueError
    except:
        await message.answer("Введите положительное число.")
        return
    await state.update_data(activations=activations)
    await message.answer("Выберите тип награды:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Премиум", callback_data="promo_type_premium")],
        [InlineKeyboardButton(text="Coins", callback_data="promo_type_coins")],
        [InlineKeyboardButton(text="Баланс (старый)", callback_data="promo_type_balance")],
        [InlineKeyboardButton(text="Отмена", callback_data="promo_cancel")],
    ]))
    await state.set_state(PromoCreationStates.waiting_reward_type)

@dp.callback_query(PromoCreationStates.waiting_reward_type, F.data.startswith("promo_type_"))
async def promo_reward_type_choice(query: CallbackQuery, state: FSMContext):
    data = query.data
    if data == "promo_cancel":
        await state.clear()
        await query.message.edit_text("Создание промокода отменено.")
        return
    reward_type = data.split("_")[-1]  # premium, coins, balance
    await state.update_data(reward_type=reward_type)
    if reward_type == "premium":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="1 день", callback_data="promo_amount_1")],
            [InlineKeyboardButton(text="3 дня", callback_data="promo_amount_3")],
            [InlineKeyboardButton(text="7 дней", callback_data="promo_amount_7")],
            [InlineKeyboardButton(text="30 дней", callback_data="promo_amount_30")],
            [InlineKeyboardButton(text="Своё", callback_data="promo_amount_custom")],
            [InlineKeyboardButton(text="Отмена", callback_data="promo_cancel")],
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="10", callback_data="promo_amount_10")],
            [InlineKeyboardButton(text="50", callback_data="promo_amount_50")],
            [InlineKeyboardButton(text="100", callback_data="promo_amount_100")],
            [InlineKeyboardButton(text="500", callback_data="promo_amount_500")],
            [InlineKeyboardButton(text="1000", callback_data="promo_amount_1000")],
            [InlineKeyboardButton(text="Своё", callback_data="promo_amount_custom")],
            [InlineKeyboardButton(text="Отмена", callback_data="promo_cancel")],
        ])
    await query.message.edit_text("Выберите количество награды:", reply_markup=keyboard)
    await state.set_state(PromoCreationStates.waiting_reward_amount)

@dp.callback_query(PromoCreationStates.waiting_reward_amount, F.data.startswith("promo_amount_"))
async def promo_reward_amount_choice(query: CallbackQuery, state: FSMContext):
    data = query.data
    if data == "promo_amount_custom":
        await query.message.edit_text("Введите количество награды:")
        await state.set_state(PromoCreationStates.waiting_custom_amount)
        return
    if data == "promo_cancel":
        await state.clear()
        await query.message.edit_text("Создание промокода отменено.")
        return
    amount = int(data.split("_")[-1])
    await state.update_data(amount=amount)
    await finalize_promo_creation(query, state)

@dp.message(PromoCreationStates.waiting_custom_amount, F.text)
async def promo_custom_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        await message.answer("Нет доступа."); await state.clear(); return
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except:
        await message.answer("Введите положительное число.")
        return
    await state.update_data(amount=amount)
    await finalize_promo_creation(message, state)

async def finalize_promo_creation(source: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    activations = data.get("activations")
    reward_type = data.get("reward_type")
    amount = data.get("amount")
    code_text = data.get("code_text")
    create_promo(code_text, reward_type, amount, activations)
    text = (
        f"✅ Промокод создан!\n"
        f"Код: {code_text}\n"
        f"Активаций: {activations}\n"
        f"Тип награды: { 'Премиум (дней)' if reward_type == 'premium' else 'Coins' if reward_type == 'coins' else 'Баланс (старый)' }\n"
        f"Количество: {amount}"
    )
    if isinstance(source, CallbackQuery):
        await source.message.edit_text(text)
        await source.answer("Промокод создан.")
    else:
        await source.answer(text)
    await state.clear()

# ------------------------------------------------------------
# Магазин
# ------------------------------------------------------------
@dp.callback_query(F.data == "menu_shop")
async def menu_shop(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id):
        await query.answer("Вы забанены в боте.", show_alert=True)
        return
    user_id = query.from_user.id
    coins = get_coins(user_id)
    text = (
        "💰 shop 404faceit \n"
        "——————\n"
        "Привет, ты находишься в магазине 404hp FACEIT где ты можешь приобрести товары от нашего проекта\n"
        "1) Рамки\n"
        "2) Премиум\n"
        "3) Разбан\n\n"
        "♥️ Выбирай нужный ассортимент\n"
        "——————\n"
        f"[💲] Твой баланс: {coins} Coins"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Рамки", callback_data="shop_frames")],
        [InlineKeyboardButton(text="Премиум", callback_data="shop_premium")],
        [InlineKeyboardButton(text="Разбан", callback_data="shop_unban")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")],
    ])
    msg = await query.message.edit_text(text, reply_markup=keyboard)
    menu_messages[user_id] = msg

@dp.callback_query(F.data == "shop_frames")
async def shop_frames(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    text = (
        "Доступные рамки для покупки:\n"
        f"Gold frame: {GOLD_FRAME_PRICE} Coins\n"
        f"Snow frame: {SNOW_FRAME_PRICE} Coins\n"
        f"Infernal frame: {INFERNAL_FRAME_PRICE} Coins\n\n"
        "Нажмите на рамку, чтобы посмотреть."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Gold frame", callback_data="view_frame_gold")],
        [InlineKeyboardButton(text="Snow frame", callback_data="view_frame_snow")],
        [InlineKeyboardButton(text="Infernal frame", callback_data="view_frame_infernal")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_shop")],
    ])
    msg = await query.message.edit_text(text, reply_markup=keyboard)
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data.startswith("view_frame_"))
async def view_frame(query: CallbackQuery):
    frame_key = query.data.split("_")[-1]
    frames = {
        "gold": (GOLD_FRAME_IMAGE_URL, "Gold frame", GOLD_FRAME_PRICE),
        "snow": (SNOW_FRAME_IMAGE_URL, "Snow frame", SNOW_FRAME_PRICE),
        "infernal": (INFERNAL_FRAME_IMAGE_URL, "Infernal frame", INFERNAL_FRAME_PRICE),
    }
    if frame_key not in frames:
        await query.answer("Неизвестная рамка")
        return
    image_url, frame_name, price = frames[frame_key]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Купить за {price} Coins", callback_data=f"buy_frame_{frame_key}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="shop_frames")],
    ])
    # Отвечаем сразу, чтобы избежать "query is too old"
    await query.answer()
    try:
        sent_msg = await bot.send_photo(query.message.chat.id, image_url, caption=frame_name, reply_markup=keyboard)
        # Обновляем menu_messages на новое сообщение с фото
        menu_messages[query.from_user.id] = sent_msg
    except Exception as e:
        logging.error(f"Не удалось отправить рамку: {e}")
        # Если не удалось, отправляем текстовое сообщение с кнопкой
        sent_msg = await bot.send_message(query.message.chat.id, frame_name, reply_markup=keyboard)
        menu_messages[query.from_user.id] = sent_msg
    # Удаляем исходное сообщение с текстовым меню
    try:
        await bot.delete_message(chat_id=query.message.chat.id, message_id=query.message.message_id)
    except:
        pass

@dp.callback_query(F.data.startswith("buy_frame_"))
async def buy_frame(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    user_id = query.from_user.id
    frame_key = query.data.split("_")[-1]
    frames = {
        "gold": ("Gold frame", GOLD_FRAME_PRICE),
        "snow": ("Snow frame", SNOW_FRAME_PRICE),
        "infernal": ("Infernal frame", INFERNAL_FRAME_PRICE)
    }
    if frame_key not in frames:
        await query.answer("Неизвестная рамка.")
        return
    frame_name, price = frames[frame_key]
    coins = get_coins(user_id)
    if coins < price:
        await query.answer(f"Недостаточно Coins. Нужно {price}, у вас {coins}.", show_alert=True)
        return
    add_coins(user_id, -price)
    set_user_frame(user_id, frame_name)
    await query.answer(f"Вы купили рамку {frame_name} за {price} Coins!")
    # Удаляем фото с кнопкой покупки
    try:
        await bot.delete_message(chat_id=query.message.chat.id, message_id=query.message.message_id)
    except:
        pass
    # Возвращаемся в магазин
    await menu_shop(query)

@dp.callback_query(F.data == "shop_premium")
async def shop_premium(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    user_id = query.from_user.id
    coins = get_coins(user_id)
    if coins < PREMIUM_30_DAYS_PRICE:
        await query.answer(f"Недостаточно Coins. Нужно {PREMIUM_30_DAYS_PRICE}, у вас {coins}.", show_alert=True)
        return
    add_coins(user_id, -PREMIUM_30_DAYS_PRICE)
    add_premium_days(user_id, 30)
    await query.answer("Вы купили Премиум на 30 дней!")
    await menu_shop(query)

@dp.callback_query(F.data == "shop_unban")
async def shop_unban(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    user_id = query.from_user.id
    if not is_game_banned(user_id):
        await query.answer("У вас нет игрового бана.")
        return
    coins = get_coins(user_id)
    if coins < UNBAN_PRICE:
        await query.answer(f"Недостаточно Coins. Нужно {UNBAN_PRICE}, у вас {coins}.", show_alert=True)
        return
    add_coins(user_id, -UNBAN_PRICE)
    c.execute("UPDATE users SET game_ban=0 WHERE user_id=?", (user_id,))
    conn.commit()
    await query.answer("Вы успешно сняли игровой бан!")
    await menu_shop(query)

# ------------------------------------------------------------
# Пригласить и промокод (пользовательские)
# ------------------------------------------------------------
@dp.callback_query(F.data == "menu_invite")
async def menu_invite(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id):
        await query.answer("Вы забанены в боте.", show_alert=True)
        return
    user_id = query.from_user.id
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    text = (
        "👥 Реферальная система\n"
        "——————\n"
        f"[🔗] Ваша реферальная ссылка: {ref_link}\n"
        "——————\n"
        "Вы получаете: За каждого приглашённого друга вы будете получать день премиум статуса, и также 5 Coins на свой баланс"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")],
    ])
    msg = await query.message.edit_text(text, reply_markup=keyboard)
    menu_messages[user_id] = msg

@dp.callback_query(F.data == "menu_promo")
async def menu_promo(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id):
        await query.answer("Вы забанены в боте.", show_alert=True)
        return
    msg = await query.message.edit_text("Введите промокод:")
    menu_messages[query.from_user.id] = msg
    await state.set_state(PromoStates.waiting_code)

@dp.message(PromoStates.waiting_code, F.text)
async def process_promo(message: Message, state: FSMContext):
    if is_banned(message.from_user.id):
        await message.answer("Вы забанены в боте.")
        await state.clear()
        return
    code = message.text.strip()
    success, msg = use_promo(message.from_user.id, code)
    await message.answer(msg)
    await state.clear()
    user_id = message.from_user.id
    menu_msg = await message.answer("Главное меню:", reply_markup=await main_menu_keyboard(user_id, message.from_user.username))
    menu_messages[user_id] = menu_msg

# ------------------------------------------------------------
# Остальные обработчики (пользователи, тикеты, результаты и т.д.)
# ------------------------------------------------------------
@dp.callback_query(F.data == "admin_list")
async def admin_list(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    c.execute("SELECT user_id, nickname FROM users")
    users = c.fetchall()
    buttons = [[InlineKeyboardButton(text=f"{n} (ID:{uid})", callback_data=f"admin_user_{uid}")] for uid, n in users]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu_admin")])
    msg = await query.message.edit_text("Выберите пользователя:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data.startswith("admin_user_"))
async def admin_user(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    uid = int(query.data.split("_")[-1])
    c.execute("SELECT nickname, banned_until, muted_until, premium, game_ban, badge FROM users WHERE user_id=?", (uid,))
    row = c.fetchone()
    if not row: await query.answer("Не найден."); return
    nick, banned, muted, premium, gban, badge = row
    status = []
    if banned and datetime.fromisoformat(banned) > datetime.now(): status.append("🔴 Забанен в боте")
    if muted and datetime.fromisoformat(muted) > datetime.now(): status.append("🔇 Замучен в чате")
    if premium: status.append("⭐ Premium")
    if gban: status.append("🚫 Запрет на игры")
    if badge: status.append(f"🏅 Бейдж: {badge.upper()}")
    status_str = "\n".join(status) if status else "✅ Активен"
    keyboard = [
        [InlineKeyboardButton(text="🚫 Забанить в боте", callback_data=f"admin_ban_{uid}"),
         InlineKeyboardButton(text="✅ Разбанить в боте", callback_data=f"admin_unban_{uid}")],
        [InlineKeyboardButton(text="🔇 Замутить в чате", callback_data=f"admin_mute_{uid}"),
         InlineKeyboardButton(text="🔈 Размутить в чате", callback_data=f"admin_unmute_{uid}")],
        [InlineKeyboardButton(text="⭐ Выдать/убрать Premium", callback_data=f"admin_premium_{uid}")],
        [InlineKeyboardButton(text="🚫 Запретить игры" if not gban else "✅ Разрешить игры", callback_data=f"admin_gameban_{uid}")],
        [InlineKeyboardButton(text="▶️ YOUTUBER", callback_data=f"admin_badge_{uid}_youtuber"),
         InlineKeyboardButton(text="🎵 TIKTOKER", callback_data=f"admin_badge_{uid}_tiktoker")],
        [InlineKeyboardButton(text="❌ Убрать бейдж", callback_data=f"admin_badge_{uid}_none")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_list")]
    ]
    msg = await query.message.edit_text(f"Пользователь: {nick} (ID: {uid})\n{status_str}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data.startswith("admin_ban_"))
async def admin_ban(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
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
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    uid = int(query.data.split("_")[-1])
    c.execute("UPDATE users SET banned_until=NULL WHERE user_id=?", (uid,)); conn.commit()
    await query.answer("Пользователь разбанен в боте.")
    await admin_user(query)

@dp.callback_query(F.data.startswith("admin_mute_"))
async def admin_mute(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
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
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
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
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
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
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    uid = int(query.data.split("_")[-1])
    c.execute("SELECT game_ban FROM users WHERE user_id=?", (uid,))
    row = c.fetchone()
    current = row[0] if row else 0
    new = 0 if current else 1
    c.execute("UPDATE users SET game_ban=? WHERE user_id=?", (new, uid))
    conn.commit()
    await query.answer("Игры запрещены." if new else "Игры разрешены.")
    await admin_user(query)

@dp.callback_query(F.data.startswith("admin_badge_"))
async def admin_set_badge(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    _, _, uid, badge = query.data.split("_")
    uid = int(uid)
    if badge == "none":
        badge = ""
    set_user_badge(uid, badge)
    await query.answer("Бейдж обновлён.")
    await admin_user(query)

# Управление админами
@dp.callback_query(F.data == "admin_manage")
async def admin_manage_menu(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    c.execute("SELECT username FROM admins")
    admins = [row[0] for row in c.fetchall()]
    text = "Администраторы:\n" + "\n".join(f"• @{a}" for a in admins)
    msg = await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="admin_add"),
         InlineKeyboardButton(text="➖ Удалить", callback_data="admin_remove")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_admin")]]))
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data == "admin_add")
async def admin_add_prompt(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    msg = await query.message.edit_text("Введите @username для добавления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="admin_manage")]]))
    menu_messages[query.from_user.id] = msg
    await state.set_state(AdminStates.waiting_add)

@dp.callback_query(F.data == "admin_remove")
async def admin_remove_prompt(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    msg = await query.message.edit_text("Введите @username для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="admin_manage")]]))
    menu_messages[query.from_user.id] = msg
    await state.set_state(AdminStates.waiting_remove)

@dp.message(AdminStates.waiting_add, F.text)
async def process_admin_add(message: Message, state: FSMContext):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    raw = message.text.strip().lstrip('@').lower()
    if not raw: await message.answer("Некорректный username."); return
    c.execute("INSERT OR IGNORE INTO admins (username) VALUES (?)", (raw,)); conn.commit()
    await message.answer(f"✅ @{raw} теперь администратор."); await state.clear()

@dp.message(AdminStates.waiting_remove, F.text)
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
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    c.execute("SELECT nickname, elo FROM users ORDER BY elo DESC LIMIT 10")
    rows = c.fetchall()
    text = "🏆 Лидерборд\n" + "\n".join(f"{i+1}. {r[0]} — {r[1]} ELO" for i, r in enumerate(rows)) if rows else "Пусто."
    msg = await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")]]))
    menu_messages[query.from_user.id] = msg

# Тикеты
@dp.callback_query(F.data == "menu_ticket")
async def ticket_menu(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 Подать жалобу", callback_data="ticket_report")],
        [InlineKeyboardButton(text="📝 Описать свою проблему", callback_data="ticket_problem")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")],
    ])
    msg = await query.message.edit_text("Выберите формат поддержки:", reply_markup=keyboard)
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data == "ticket_report")
async def ticket_report_start(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    await state.clear()
    msg = await query.message.edit_text("👤 Введите @username или nickname нарушителя:")
    menu_messages[query.from_user.id] = msg
    await state.set_state(ReportStates.waiting_target)

@dp.callback_query(F.data == "ticket_problem")
async def ticket_problem_start(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    await state.clear()
    msg = await query.message.edit_text("📝 Опишите свою проблему:")
    menu_messages[query.from_user.id] = msg
    await state.set_state(ProblemTicketStates.waiting_text)

@dp.message(ProblemTicketStates.waiting_text, F.text)
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
        msg = await query.message.edit_text("📸 Отправьте скриншот проблемы.")
        menu_messages[query.from_user.id] = msg
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

@dp.message(ProblemTicketStates.waiting_admin_reply, F.text)
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

# Система репортов
def report_keyboard(report_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Ограничить доступ к боту", callback_data=ReportAction(action="restrict", report_id=report_id).pack())],
            [InlineKeyboardButton(text="✅ Вернуть доступ к боту", callback_data=ReportAction(action="unrestrict", report_id=report_id).pack())],
            [InlineKeyboardButton(text="🔨 Забанить", callback_data=ReportAction(action="ban", report_id=report_id).pack()),
             InlineKeyboardButton(text="♻️ Разбанить", callback_data=ReportAction(action="unban", report_id=report_id).pack())],
            [InlineKeyboardButton(text="🔇 Замутить", callback_data=ReportAction(action="mute", report_id=report_id).pack()),
             InlineKeyboardButton(text="🔊 Размутить", callback_data=ReportAction(action="unmute", report_id=report_id).pack())],
            [InlineKeyboardButton(text="❌ Отменить репорт", callback_data=ReportAction(action="cancel", report_id=report_id).pack())],
        ]
    )

@dp.message(Command("report"))
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

@dp.message(ReportStates.waiting_target, F.text)
async def report_target(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    if not target:
        await message.answer("❌ Пользователь не указан.\n\nВведите @username или nickname:")
        return
    await state.update_data(target=target)
    await message.answer("📝 Введите текст жалобы:")
    await state.set_state(ReportStates.waiting_text)

@dp.message(ReportStates.waiting_text, F.text)
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

# ------------------------------------------------------------
# Назад
# ------------------------------------------------------------
@dp.callback_query(F.data == "menu_back")
async def back_to_menu(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    user_id = query.from_user.id
    try:
        msg = await query.message.edit_text("Главное меню:", reply_markup=await main_menu_keyboard(user_id, query.from_user.username))
        menu_messages[user_id] = msg
    except:
        try:
            await query.message.delete()
        except:
            pass
        msg = await bot.send_message(chat_id=query.from_user.id, text="Главное меню:", reply_markup=await main_menu_keyboard(user_id, query.from_user.username))
        menu_messages[user_id] = msg

# ------------------------------------------------------------
# Результаты /results
# ------------------------------------------------------------
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

@dp.message(ResultStates.waiting_score, F.text)
async def results_score(message: Message, state: FSMContext, bot: Bot):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return
    data = await state.get_data()
    match_id = data.get("match_id")
    screenshot_id = data.get("screenshot_id")

    if not match_id:
        user_id = message.from_user.id
        c.execute("SELECT id, lobby_id, match_number FROM matches WHERE host_id=? AND status='drawn' ORDER BY created_at DESC LIMIT 1",
                  (user_id,))
        match = c.fetchone()
        if not match:
            await message.answer("Нет активных матчей, где вы являетесь хостом.")
            await state.clear()
            return
        match_id, lobby_id, match_num = match
    else:
        match = get_match_info(match_id)
        if not match:
            await message.answer("Матч не найден.")
            await state.clear()
            return
        _, lobby_id, match_num, _, _, _, _ = match

    try:
        parts = message.text.strip().split()
        ct_score = int(parts[0])
        t_score = int(parts[1])
    except:
        await message.answer("Неверный формат. Введите два числа через пробел, например: 13 1")
        return

    c.execute("SELECT map_name FROM lobbies WHERE id=?", (lobby_id,))
    map_name = c.fetchone()[0]

    # Убрана строка переворота команд
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
            add_coins(uid, 10)  # +10 Coins за победу
        else:
            c.execute("UPDATE users SET losses = losses + 1, elo = ? WHERE user_id=?", (new_elo, uid))
        increment_map_count(uid, map_name)
    conn.commit()

    c.execute("SELECT user_id, team FROM match_players WHERE match_id=? ORDER BY team, user_id", (match_id,))
    rows = c.fetchall()
    ct_list = [(uid, get_nickname(uid), get_elo(uid)) for uid, team in rows if team == 'CT']
    t_list = [(uid, get_nickname(uid), get_elo(uid)) for uid, team in rows if team == 'T']

    host_id = message.from_user.id
    host_nick = get_nickname(host_id)

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

# ------------------------------------------------------------
# Мои лобби (для обычных пользователей)
# ------------------------------------------------------------
@dp.callback_query(F.data == "my_lobbies")
async def my_lobbies(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    user_id = query.from_user.id
    matches = get_user_active_matches(user_id)
    if not matches:
        await query.answer("У вас нет активных матчей.", show_alert=True)
        return

    buttons = []
    for (match_id, lobby_id, match_num, map_name, mode, created_at) in matches:
        # Сокращённый текст кнопки
        buttons.append([InlineKeyboardButton(text=f"Матч #{match_num} ({mode})", callback_data=f"my_match_{match_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")])
    msg = await query.message.edit_text("Ваши активные матчи:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    menu_messages[user_id] = msg

@dp.callback_query(F.data.startswith("my_match_"))
async def my_match_detail(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    match_id = int(query.data.split("_")[-1])
    match = get_match_info(match_id)
    if not match:
        await query.answer("Матч не найден.")
        return
    lobby_id, match_num, map_name, mode, host_id, status = match[1], match[2], match[3], match[4], match[5], match[6]
    c.execute("SELECT user_id FROM match_players WHERE match_id=? AND team='CT'", (match_id,))
    ct_ids = [row[0] for row in c.fetchall()]
    c.execute("SELECT user_id FROM match_players WHERE match_id=? AND team='T'", (match_id,))
    t_ids = [row[0] for row in c.fetchall()]
    ct_str = ", ".join([get_nickname(pid) for pid in ct_ids])
    t_str = ", ".join([get_nickname(pid) for pid in t_ids])
    text = f"Матч #{match_num} ({mode}) на {map_name}\n🔵 CT: {ct_str}\n🔴 T: {t_str}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Зарегистрировать результаты", callback_data=f"my_results_{match_id}")],
        [InlineKeyboardButton(text="❌ Отменить регистрацию", callback_data=f"my_cancel_{match_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="my_lobbies")],
    ])
    msg = await query.message.edit_text(text, reply_markup=keyboard)
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data.startswith("my_results_"))
async def my_results_start(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    match_id = int(query.data.split("_")[-1])
    await state.update_data(match_id=match_id)
    msg = await query.message.edit_text("📸 Отправьте скриншот результатов:")
    menu_messages[query.from_user.id] = msg
    await state.set_state(ResultStates.waiting_screenshot)

@dp.callback_query(F.data.startswith("my_cancel_"))
async def my_cancel_request(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    match_id = int(query.data.split("_")[-1])
    await state.update_data(cancel_match_id=match_id)
    msg = await query.message.edit_text("📝 Введите причину отмены матча:")
    menu_messages[query.from_user.id] = msg
    await state.set_state(CancelMatchStates.waiting_reason)

@dp.message(CancelMatchStates.waiting_reason, F.text)
async def my_cancel_reason(message: Message, state: FSMContext, bot: Bot):
    reason = message.text.strip()
    if not reason:
        await message.answer("❌ Причина не может быть пустой.")
        return
    data = await state.get_data()
    match_id = data["cancel_match_id"]
    match = get_match_info(match_id)
    if not match:
        await message.answer("Матч не найден.")
        await state.clear()
        return
    host_id = match[5]
    host_name = get_nickname(host_id)
    ct_ids = [row[0] for row in c.execute("SELECT user_id FROM match_players WHERE match_id=? AND team='CT'", (match_id,)).fetchall()]
    t_ids = [row[0] for row in c.execute("SELECT user_id FROM match_players WHERE match_id=? AND team='T'", (match_id,)).fetchall()]
    ct_str = ", ".join([get_nickname(pid) for pid in ct_ids])
    t_str = ", ".join([get_nickname(pid) for pid in t_ids])
    text = (
        f"🚨 <b>Заявка на отмену матча</b>\n"
        f"Матч #{match[2]} ({match[4]}) на {match[3]}\n"
        f"👤 Хост: {host_name}\n"
        f"🔵 CT: {ct_str}\n"
        f"🔴 T: {t_str}\n\n"
        f"📝 <b>Причина:</b> {message.text}\n\n"
        f"Администратор может отменить матч нажав на кнопку ниже."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отменить матч", callback_data=f"admin_cancel_match_{match_id}")],
    ])
    await bot.send_message(GROUP_CHAT_ID, text, parse_mode=ParseMode.HTML, reply_markup=keyboard, message_thread_id=TOPIC_TICKET)
    await message.answer("Заявка на отмену отправлена администраторам.")
    await state.clear()

@dp.callback_query(F.data.startswith("admin_cancel_match_"))
async def admin_cancel_match_handler(query: CallbackQuery, bot: Bot):
    try:
        member = await bot.get_chat_member(GROUP_CHAT_ID, query.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            await query.answer("❌ У вас нет прав.", show_alert=True)
            return
    except Exception as e:
        await query.answer("⚠️ Ошибка проверки прав.")
        return

    match_id = int(query.data.split("_")[-1])
    cancel_match(match_id)
    await query.message.edit_text(query.message.text + "\n\n✅ Матч отменён.")
    await query.answer("Матч отменён.")

# ------------------------------------------------------------
# АДМИН: Управление результатами
# ------------------------------------------------------------
@dp.callback_query(F.data == "admin_manage_results")
async def admin_manage_results_list(query: CallbackQuery):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    matches = get_all_finished_matches()
    if not matches:
        await query.answer("Нет завершённых матчей.", show_alert=True)
        return

    buttons = []
    for (match_id, lobby_id, match_num, map_name, mode, host_id, score) in matches:
        host_name = get_nickname(host_id) if host_id else "???"
        text = f"Матч #{match_num} ({mode}) на {map_name} – {score} (хост: {host_name})"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"admin_edit_result_{match_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu_admin")])
    msg = await query.message.edit_text("Выберите матч для изменения счёта:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    menu_messages[query.from_user.id] = msg

@dp.callback_query(F.data.startswith("admin_edit_result_"))
async def admin_edit_result_prompt(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    match_id = int(query.data.split("_")[-1])
    match = get_match_info(match_id)
    if not match or match[6] != 'finished':
        await query.answer("Матч не найден или не завершён.")
        return

    await state.update_data(edit_match_id=match_id)
    msg = await query.message.edit_text(
        f"Текущий счёт: {match[6] if len(match) > 6 else '???'}\n"
        f"Введите новый счёт в формате: CT T (например, 13 5)"
    )
    menu_messages[query.from_user.id] = msg
    await state.set_state(AdminStates.waiting_new_score)

@dp.message(AdminStates.waiting_new_score, F.text)
async def admin_edit_result_exec(message: Message, state: FSMContext):
    if is_banned(message.from_user.id): await message.answer("Вы забанены в боте."); return

    data = await state.get_data()
    match_id = data.get("edit_match_id")
    if not match_id:
        await message.answer("Сессия истекла.")
        await state.clear()
        return

    try:
        parts = message.text.strip().split()
        new_ct = int(parts[0])
        new_t = int(parts[1])
    except:
        await message.answer("Неверный формат. Введите два числа через пробел, например: 13 5")
        return

    success = update_match_score(match_id, new_ct, new_t)
    if success:
        await message.answer("✅ Счёт матча и ELO игроков обновлены.")
    else:
        await message.answer("❌ Не удалось обновить счёт (возможно, матч не найден).")
    await state.clear()

# ------------------------------------------------------------
# Управление аккаунтом (admin)
# ------------------------------------------------------------
@dp.callback_query(F.data == "admin_manage_account")
async def admin_manage_account(query: CallbackQuery, state: FSMContext):
    if not is_owner_of_menu(query):
        await query.answer("Это меню другого пользователя.", show_alert=True)
        return
    if is_banned(query.from_user.id): await query.answer("Вы забанены в боте.", show_alert=True); return
    if not is_admin(query.from_user.username): await query.answer("Нет доступа."); return
    msg = await query.message.edit_text("Введите @username или nickname игрока:")
    menu_messages[query.from_user.id] = msg
    await state.set_state(ManageAccountStates.waiting_user)

@dp.message(ManageAccountStates.waiting_user, F.text)
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
        msg = await query.message.edit_text("Введите новый никнейм:")
        await state.update_data(action="nick")
        await state.set_state(ManageAccountStates.waiting_value)
    elif action == "sid":
        msg = await query.message.edit_text("Введите новый ID Standoff 2:")
        await state.update_data(action="sid")
        await state.set_state(ManageAccountStates.waiting_value)
    elif action == "avatar":
        msg = await query.message.edit_text("Отправьте новую аватарку:")
        await state.update_data(action="avatar")
        await state.set_state(ManageAccountStates.waiting_value)
    elif action == "banner":
        msg = await query.message.edit_text("Отправьте новый баннер:")
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
    if action in ("nick", "sid", "avatar", "banner"):
        menu_messages[query.from_user.id] = msg

@dp.message(ManageAccountStates.waiting_value)
async def manage_account_value(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("target_id")
    action = data.get("action")
    if action in ("nick", "sid"):
        if not message.text:
            await message.answer("Отправьте текст.")
            return
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
    else:
        await message.answer("Неизвестное действие.")
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
        try:
            await update_lobby_post(bot, lobby_id)
        except Exception as e:
            logging.error(f"Не удалось восстановить пост лобби {lobby_id}: {e}")
        await asyncio.sleep(0.5)

# ------------------------------------------------------------
# Блокировка от повторного запуска
# ------------------------------------------------------------
LOCK_FILE = "bot.lock"

async def main():
    global bot
    if os.path.exists(LOCK_FILE):
        print("Бот уже запущен. Завершение.")
        sys.exit(0)
    else:
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))

    try:
        init_db()
        download_font()
        download_default_banner()
        load_map_images()
        init_lobbies()

        bot = Bot(token=TOKEN)
        dp.include_router(report_router)

        # Сброс webhook и очистка ожидающих апдейтов
        await bot.delete_webhook(drop_pending_updates=True)

        await restore_all_lobby_posts()
        asyncio.create_task(auto_reset_lobbies())
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
