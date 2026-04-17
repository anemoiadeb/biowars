#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════╗
║  ☣️   БИО-ВОЙНЫ  |  Bio-Wars Bot  v2.0   ☣️   ║
║  aiogram 3.x  |  JSON storage              ║
║  Pydroid 3 + Render.com (polling + health) ║
╚══════════════════════════════════════════════╝
Команды (текстом, без слэша):
  Био-война           — главное меню
  Профиль             — ваш БК
  Заразить @ник       — атака
  Вылечиться          — антидот (30 патогенов)
  Ферма / Фарма / Работать — заработать (КД 4ч)
  Лаба                — лаборатория
  Магазин             — бесконечная прокачка
  Ник Название        — задать имя вируса
  Топ заражённых      — рейтинг атакующих
  Топ патогенов       — рейтинг богатых
  Помощь              — список команд
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
TOKEN        = os.getenv("BOT_TOKEN", "YOUR_TOKEN_HERE")
PORT         = int(os.getenv("PORT", 8080))
DATA_FILE    = Path("bio_wars.json")
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "https://biowars.onrender.com")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL  = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bio_wars")

SEP = "\u2500" * 26

LAB_CD          = 3600        # 1 ч   смена в лабе
FARM_CD         = 4 * 3600    # 4 ч   ферма
INFECT_CD       = 1800        # 30 м  кд атаки
INFECT_DURATION = 5 * 3600    # 5 ч   автоснятие заражения

BASE_COST  = {"infectivity": 40, "lethality": 50, "resistance": 60}
VAC_BASE   = 30
MAX_VAC    = 5
LAB_MAX    = 99

# ══════════════════════════════════════════════════════════════════════════════
#  DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

def load_db() -> Dict:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"DB load: {e}")
    return {}


def save_db(data: Dict) -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"DB save: {e}")


def _blank(uid: int, uname: str, name: str) -> Dict:
    return {
        "id": uid, "username": uname, "name": name,
        "virus_name": "",
        "pathogens": 15, "total_earned": 15,
        "level": 1, "xp": 0,
        "is_infected": False, "infected_at": 0,
        "infected_by_id": None, "infected_by_name": None,
        "infected_count": 0, "cured_count": 0,
        "lab_level": 1,
        "infectivity": 0, "lethality": 0, "resistance": 0, "vaccines": 0,
        "last_lab_work": 0, "last_farm": 0, "last_infect": 0,
        "last_passive": time.time(),
    }


def get_or_create(data: Dict, fu) -> Dict:
    uid = str(fu.id)
    if uid not in data:
        data[uid] = _blank(fu.id, fu.username or "", fu.full_name)
    else:
        if fu.username:
            data[uid]["username"] = fu.username
        data[uid]["name"] = fu.full_name
    u = data[uid]
    for k, v in _blank(0, "", "").items():
        if k not in u:
            u[k] = v
    return u


# ══════════════════════════════════════════════════════════════════════════════
#  GAME LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def upgrade_cost(key: str, lv: int) -> int:
    """Бесконечная прокачка: cost = base*(lv+1)*(1+lv*0.25)"""
    return int(BASE_COST.get(key, 40) * (lv + 1) * (1 + lv * 0.25))


def _xp_need(lv: int) -> int:
    return lv * 100


def give_xp(u: Dict, amt: int) -> List[str]:
    msgs: List[str] = []
    u["xp"] += amt
    while u["xp"] >= _xp_need(u["level"]):
        u["xp"] -= _xp_need(u["level"])
        u["level"] += 1
        msgs.append(f"\u2b06\ufe0f <b>\u0423\u0440\u043e\u0432\u0435\u043d\u044c \u0411\u041a: {u['level']}</b>")
    return msgs


def check_auto_cure(u: Dict) -> bool:
    if u["is_infected"] and u.get("infected_at", 0) > 0:
        if time.time() - u["infected_at"] >= INFECT_DURATION:
            u.update(is_infected=False, infected_at=0,
                     infected_by_id=None, infected_by_name=None)
            return True
    return False


def infection_left(u: Dict) -> float:
    if not u["is_infected"]:
        return 0.0
    return max(0.0, u.get("infected_at", 0) + INFECT_DURATION - time.time())


def apply_passive(u: Dict) -> int:
    if u["is_infected"]:
        u["last_passive"] = time.time()
        return 0
    now = time.time()
    h   = (now - u.get("last_passive", now)) / 3600.0
    if h < 0.5:
        return 0
    earned = min(int(h * (2 + u["lab_level"] * 0.5)), 120)
    u["pathogens"]    += earned
    u["total_earned"] += earned
    u["last_passive"]  = now
    return earned


def inf_chance(att: Dict, dfn: Dict) -> float:
    c = 0.40 + att["infectivity"] * 0.04 - dfn["resistance"] * 0.05 - dfn["vaccines"] * 0.12
    return max(0.05, min(0.95, c))


def cd_str(sec: float) -> str:
    s = int(sec)
    h, m, ss = s // 3600, (s % 3600) // 60, s % 60
    if h > 0:
        return f"{h}\u0447 {m}\u043c"
    return f"{m}\u043c {ss}\u0441"


def vname_taken(name: str, skip_uid: str) -> bool:
    n = name.lower()
    return any(
        uid != skip_uid and ud.get("virus_name", "").lower() == n
        for uid, ud in DB.items()
    )


def infected_msg(u: Dict) -> str:
    left = infection_left(u)
    src  = u.get("infected_by_name") or "???"
    return (
        f"\u2623\ufe0f <b>\u0422\u042b \u0417\u0410\u0420\u0410\u0416\u0401\u041d!</b>\n"
        f"\u0412\u0438\u0440\u0443\u0441 <b>{src}</b> \u0431\u043b\u043e\u043a\u0438\u0440\u0443\u0435\u0442 \u0432\u0441\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044f.\n"
        f"\u0410\u0432\u0442\u043e\u043b\u0435\u0447\u0435\u043d\u0438\u0435 \u0447\u0435\u0440\u0435\u0437: <b>{cd_str(left)}</b>\n\n"
        f"\u0418\u043b\u0438 \u0432\u0432\u0435\u0434\u0438 <b>\u0412\u044b\u043b\u0435\u0447\u0438\u0442\u044c\u0441\u044f</b> (30🧪)."
    )


# ══════════════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════

def kb_main() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🧫 \u041f\u0440\u043e\u0444\u0438\u043b\u044c",        callback_data="profile")
    b.button(text="🔬 \u041b\u0430\u0431\u043e\u0440\u0430\u0442\u043e\u0440\u0438\u044f",   callback_data="lab")
    b.button(text="\u26cf\ufe0f \u0424\u0435\u0440\u043c\u0430",            callback_data="farm")
    b.button(text="💊 \u041c\u0430\u0433\u0430\u0437\u0438\u043d",          callback_data="shop")
    b.button(text="\u2623\ufe0f \u0422\u043e\u043f \u0437\u0430\u0440\u0430\u0436\u0451\u043d\u043d\u044b\u0445", callback_data="top_inf")
    b.button(text="🏆 \u0422\u043e\u043f \u043f\u0430\u0442\u043e\u0433\u0435\u043d\u043e\u0432",   callback_data="top_pat")
    b.adjust(2, 2, 2)
    return b.as_markup()


def kb_lab(u: Dict) -> InlineKeyboardMarkup:
    b   = InlineKeyboardBuilder()
    cdr = max(0.0, LAB_CD - (time.time() - u["last_lab_work"]))
    if cdr > 0:
        b.button(text=f"\u23f3 \u0421\u043c\u0435\u043d\u0430 ({cd_str(cdr)})", callback_data="lab_noop")
    else:
        b.button(text="\u2697\ufe0f \u0420\u0430\u0431\u043e\u0442\u0430\u0442\u044c \u0432 \u043b\u0430\u0431\u0435", callback_data="lab_work")
    cost = u["lab_level"] * 80
    b.button(text=f"📈 \u0423\u043b\u0443\u0447\u0448\u0438\u0442\u044c [{cost}🧪] \u2192 \u0443\u0440.{u['lab_level']+1}", callback_data="lab_upgrade")
    b.button(text="\u25c0\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="main_menu")
    b.adjust(1)
    return b.as_markup()


def kb_farm(u: Dict) -> InlineKeyboardMarkup:
    b   = InlineKeyboardBuilder()
    cdr = max(0.0, FARM_CD - (time.time() - u.get("last_farm", 0)))
    if cdr > 0:
        b.button(text=f"\u23f3 \u0421\u0431\u043e\u0440 ({cd_str(cdr)})", callback_data="farm_noop")
    else:
        b.button(text="\u26cf\ufe0f \u0421\u043e\u0431\u0440\u0430\u0442\u044c \u043f\u0430\u0442\u043e\u0433\u0435\u043d\u044b", callback_data="farm_work")
    b.button(text="\u25c0\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="main_menu")
    b.adjust(1)
    return b.as_markup()


def kb_shop(u: Dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for emoji, label, key in [
        ("🦫", "\u0417\u0430\u0440\u0430\u0437\u043d\u043e\u0441\u0442\u044c",  "infectivity"),
        ("\u2620\ufe0f",  "\u041b\u0435\u0442\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u044c", "lethality"),
        ("🛡\ufe0f", "\u0420\u0435\u0437\u0438\u0441\u0442\u0435\u043d\u0442\u043d.",  "resistance"),
    ]:
        lv   = u[key]
        cost = upgrade_cost(key, lv)
        b.button(text=f"{emoji} {label} \u0443\u0440.{lv} [{cost}🧪]", callback_data=f"buy_{key}")
    vac  = u["vaccines"]
    vcst = VAC_BASE * (vac + 1)
    if vac < MAX_VAC:
        b.button(text=f"💉 \u0412\u0430\u043a\u0446\u0438\u043d\u0430 {vac}/{MAX_VAC} [{vcst}🧪]", callback_data="buy_vaccines")
    else:
        b.button(text=f"💉 \u0412\u0430\u043a\u0446\u0438\u043d\u0430 MAX", callback_data="shop_noop")
    b.button(text="\u25c0\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="main_menu")
    b.adjust(1)
    return b.as_markup()


def kb_back() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="\u25c0\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="main_menu")
    return b.as_markup()


# ══════════════════════════════════════════════════════════════════════════════
#  FORMATTERS
# ══════════════════════════════════════════════════════════════════════════════

def _bar(val: int) -> str:
    w = min(val, 20)
    return "\u2588" * w + "\u2591" * (20 - w)


def fmt_profile(u: Dict) -> str:
    if u["is_infected"]:
        left   = infection_left(u)
        status = f"\u2623\ufe0f \u0417\u0410\u0420\u0410\u0416\u0401\u041d (cd_str(left)={cd_str(left)})"
        src    = f"\n   \u2192 \u0432\u0438\u0440\u0443\u0441: <b>{u.get('infected_by_name','???')}</b>"
    else:
        status = "\u2705 \u0417\u0434\u043e\u0440\u043e\u0432"
        src    = ""
    vn = f"🦫 \u0412\u0438\u0440\u0443\u0441: <b>{u['virus_name']}</b>\n" if u.get("virus_name") else ""
    return (
        f"🧫 <b>\u0411\u0418\u041e-\u041a\u041e\u041d\u0422\u0415\u0419\u041d\u0415\u0420</b> \u00b7 {u['name']}\n"
        f"{vn}{SEP}\n"
        f"📊 \u0423\u0440. \u0411\u041a: <b>{u['level']}</b>  ({u['xp']}/{_xp_need(u['level'])} XP)\n"
        f"🧪 \u041f\u0430\u0442\u043e\u0433\u0435\u043d\u044b:  <b>{u['pathogens']}</b>\n"
        f"\u2697\ufe0f  \u041b\u0430\u0431\u0430:      <b>\u0443\u0440.{u['lab_level']}</b>\n{SEP}\n"
        f"🦫 \u0417\u0430\u0440\u0430\u0437\u043d\u043e\u0441\u0442\u044c  \u0443\u0440.<b>{u['infectivity']}</b>\n"
        f"\u2620\ufe0f  \u041b\u0435\u0442\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u044c \u0443\u0440.<b>{u['lethality']}</b>\n"
        f"🛡  \u0420\u0435\u0437\u0438\u0441\u0442\u0435\u043d\u0442\u043d. \u0443\u0440.<b>{u['resistance']}</b>\n"
        f"💉 \u0412\u0430\u043a\u0446\u0438\u043d\u044b: <b>{u['vaccines']}/{MAX_VAC}</b>\n{SEP}\n"
        f"\u2623\ufe0f \u0417\u0430\u0440\u0430\u0437\u0438\u043b: <b>{u['infected_count']}</b>  \u00b7  "
        f"💊 \u0412\u044b\u043b\u0435\u0447\u0438\u043b\u0441\u044f: <b>{u['cured_count']}</b>\n"
        f"📈 \u0421\u0442\u0430\u0442\u0443\u0441: <b>{status}</b>{src}\n"
    )


def fmt_lab(u: Dict) -> str:
    cdr  = max(0.0, LAB_CD - (time.time() - u["last_lab_work"]))
    return (
        f"🔬 <b>\u041b\u0410\u0411\u041e\u0420\u0410\u0422\u041e\u0420\u0418\u042f</b> \u00b7 \u0443\u0440.{u['lab_level']}\n{SEP}\n"
        f"\u23f1  \u0421\u043b\u0435\u0434. \u0441\u043c\u0435\u043d\u0430: <b>{''+cd_str(cdr) if cdr>0 else chr(9989)+' \u0413\u043e\u0442\u043e\u0432\u043e'}</b>\n"
        f"💰 \u0414\u043e\u0445\u043e\u0434: ~<b>{10+u['lab_level']*5}🧪</b>\n"
        f"📡 \u041f\u0430\u0441\u0441\u0438\u0432/\u0447: ~<b>{2+u['lab_level']*0.5:.1f}🧪</b>\n"
        f"📈 \u0423\u043b\u0443\u0447\u0448\u0438\u0442\u044c: <b>{u['lab_level']*80}🧪</b>\n{SEP}\n"
        f"💼 \u0412\u0441\u0435\u0433\u043e: <b>{u['total_earned']}🧪</b>\n"
    )


def fmt_farm_page(u: Dict) -> str:
    cdr = max(0.0, FARM_CD - (time.time() - u.get("last_farm", 0)))
    return (
        f"\u26cf\ufe0f <b>\u0424\u0415\u0420\u041c\u0410 \u041f\u0410\u0422\u041e\u0413\u0415\u041d\u041e\u0412</b>\n{SEP}\n"
        f"\u23f1  \u0421\u043b\u0435\u0434. \u0441\u0431\u043e\u0440: <b>{''+cd_str(cdr) if cdr>0 else chr(9989)+' \u0413\u043e\u0442\u043e\u0432\u043e'}</b>\n"
        f"💰 \u0414\u043e\u0445\u043e\u0434: ~<b>{30+u['lab_level']*3}🧪</b>\n"
        f"\u26a0\ufe0f  \u041a\u0443\u043b\u0434\u0430\u0443\u043d: 4 \u0447\u0430\u0441\u0430\n"
    )


def fmt_shop(u: Dict) -> str:
    return (
        f"💊 <b>\u041c\u0410\u0413\u0410\u0417\u0418\u041d \u041c\u0423\u0422\u0410\u0426\u0418\u0419</b>\n{SEP}\n"
        f"🦫 \u0417\u0430\u0440\u0430\u0437\u043d\u043e\u0441\u0442\u044c \u2014 +4% \u043a \u0448\u0430\u043d\u0441\u0443/\u0443\u0440.\n"
        f"\u2620\ufe0f \u041b\u0435\u0442\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u044c \u2014 \u043a\u0440\u0430\u0434\u0451\u0442 \u043f\u0430\u0442\u043e\u0433\u0435\u043d\u044b\n"
        f"🛡\ufe0f \u0420\u0435\u0437\u0438\u0441\u0442\u0435\u043d\u0442. \u2014 -5% \u043a \u0432\u0445\u043e\u0434\u044f\u0449\u0435\u043c\u0443/\u0443\u0440.\n"
        f"💉 \u0412\u0430\u043a\u0446\u0438\u043d\u0430 \u2014 -12% \u043a \u0437\u0430\u0440\u0430\u0436\u0435\u043d\u0438\u044e\n"
        f"{SEP}\n"
        f"💰 \u0411\u0430\u043b\u0430\u043d\u0441: <b>{u['pathogens']}🧪</b>\n"
    )


def fmt_top_inf() -> str:
    medals = ["🥇", "🥈", "🥉"] + ["🔬"] * 17
    top    = sorted(DB.values(), key=lambda x: x["infected_count"], reverse=True)[:10]
    if not top:
        return "\u041f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445."
    lines = [f"\u2623\ufe0f <b>\u0422\u041e\u041f \u0420\u0410\u0421\u041f\u0420\u041e\u0421\u0422\u0420\u0410\u041d\u0418\u0422\u0415\u041b\u0415\u0419</b>\n{SEP}"]
    for i, ud in enumerate(top):
        s  = "\u2623\ufe0f" if ud["is_infected"] else "\u2705"
        vn = f" [{ud['virus_name']}]" if ud.get("virus_name") else ""
        lines.append(
            f"{medals[i]} <b>{ud['name']}</b>{vn} {s}\n"
            f"   \u2514 \u0437\u0430\u0440\u0430\u0437\u0438\u043b: {ud['infected_count']} \u00b7 \u0411\u041a:{ud['level']}"
        )
    return "\n".join(lines)


def fmt_top_pat() -> str:
    medals = ["🥇", "🥈", "🥉"] + ["💰"] * 17
    top    = sorted(DB.values(), key=lambda x: x["pathogens"], reverse=True)[:10]
    if not top:
        return "\u041f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445."
    lines = [f"🧪 <b>\u0422\u041e\u041f \u041f\u041e \u041f\u0410\u0422\u041e\u0413\u0415\u041d\u0410\u041c</b>\n{SEP}"]
    for i, ud in enumerate(top):
        lines.append(
            f"{medals[i]} <b>{ud['name']}</b>\n"
            f"   \u2514 {ud['pathogens']}🧪 \u00b7 \u0411\u041a:{ud['level']} \u00b7 \u0437\u0430\u0440\u0430\u0437\u0438\u043b:{ud['infected_count']}"
        )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  CUSTOM FILTER
# ══════════════════════════════════════════════════════════════════════════════

class SW(Filter):
    def __init__(self, prefix: str) -> None:
        self.p = prefix.lower()
    async def __call__(self, m: Message) -> bool:
        return bool(m.text and m.text.lower().startswith(self.p))


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTER
# ══════════════════════════════════════════════════════════════════════════════

router = Router()
DB: Dict = {}


@router.errors()
async def error_handler(event, exception: Exception) -> None:
    log.error(f"Handler error: {type(exception).__name__}: {exception}", exc_info=exception)

HELP = (
    f"\u2623\ufe0f <b>\u0411\u0418\u041e-\u0412\u041e\u0419\u041d\u042b v2</b>\n{SEP}\n"
    "\u2022 <b>\u0411\u0438\u043e-\u0432\u043e\u0439\u043d\u0430</b>         \u2014 \u0433\u043b\u0430\u0432\u043d\u043e\u0435 \u043c\u0435\u043d\u044e\n"
    "\u2022 <b>\u041f\u0440\u043e\u0444\u0438\u043b\u044c</b>           \u2014 \u0432\u0430\u0448 \u0411\u041a\n"
    "\u2022 <b>\u0417\u0430\u0440\u0430\u0437\u0438\u0442\u044c @\u043d\u0438\u043a</b>     \u2014 \u0430\u0442\u0430\u043a\u0430\n"
    "\u2022 <b>\u0412\u044b\u043b\u0435\u0447\u0438\u0442\u044c\u0441\u044f</b>        \u2014 \u0430\u043d\u0442\u0438\u0434\u043e\u0442 (30🧪)\n"
    "\u2022 <b>\u0424\u0435\u0440\u043c\u0430</b>             \u2014 \u0441\u0431\u043e\u0440 \u043f\u0430\u0442\u043e\u0433\u0435\u043d\u043e\u0432 (\u041a\u0414 4\u0447)\n"
    "\u2022 <b>\u041b\u0430\u0431\u0430</b>               \u2014 \u043b\u0430\u0431\u043e\u0440\u0430\u0442\u043e\u0440\u0438\u044f\n"
    "\u2022 <b>\u041c\u0430\u0433\u0430\u0437\u0438\u043d</b>           \u2014 \u0431\u0435\u0441\u043a\u043e\u043d\u0435\u0447\u043d\u0430\u044f \u043f\u0440\u043e\u043a\u0430\u0447\u043a\u0430\n"
    "\u2022 <b>\u041d\u0438\u043a \u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435</b>      \u2014 \u0438\u043c\u044f \u0432\u0438\u0440\u0443\u0441\u0430\n"
    "\u2022 <b>\u0422\u043e\u043f \u0437\u0430\u0440\u0430\u0436\u0451\u043d\u043d\u044b\u0445</b>    \u2014 \u0440\u0435\u0439\u0442\u0438\u043d\u0433\n"
    "\u2022 <b>\u0422\u043e\u043f \u043f\u0430\u0442\u043e\u0433\u0435\u043d\u043e\u0432</b>    \u2014 \u0431\u043e\u0433\u0430\u0447\u0438\n\n"
    f"{SEP}\n"
    "\u2623\ufe0f \u0417\u0430\u0440\u0430\u0436\u0435\u043d\u0438\u0435 \u0431\u043b\u043e\u043a\u0438\u0440\u0443\u0435\u0442 \u0432\u0441\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044f \u043d\u0430 5 \u0447\u0430\u0441\u043e\u0432.\n"
    "🧪 \u041f\u0430\u0442\u043e\u0433\u0435\u043d\u044b \u043d\u0430\u043a\u0430\u043f\u043b\u0438\u0432\u0430\u044e\u0442\u0441\u044f \u043f\u0430\u0441\u0441\u0438\u0432\u043d\u043e, \u043f\u043e\u043a\u0430 \u0437\u0434\u043e\u0440\u043e\u0432."
)


# ── /start, помощь ────────────────────────────────────────────────────────────

@router.message(CommandStart())
@router.message(F.text.lower().in_({"помощь", "команды", "help", "хелп"}))
async def cmd_help(msg: Message) -> None:
    get_or_create(DB, msg.from_user); save_db(DB)
    await msg.answer(HELP)


# ── Главное меню ──────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"био-война", "биовойна", "биовойны", "bio war", "bio wars"}))
async def cmd_menu(msg: Message) -> None:
    u      = get_or_create(DB, msg.from_user)
    cured  = check_auto_cure(u)
    earned = apply_passive(u)
    save_db(DB)
    txt = "\u2623\ufe0f <b>\u0411\u0418\u041e-\u0412\u041e\u0419\u041d\u042b</b>\n\u041c\u0438\u0440 \u043f\u0430\u0442\u043e\u0433\u0435\u043d\u043e\u0432 \u0436\u0434\u0451\u0442:"
    if cured:  txt += "\n\n💊 <i>\u0412\u0438\u0440\u0443\u0441 \u0441\u0430\u043c\u043e\u0443\u0441\u0442\u0440\u0430\u043d\u0438\u043b\u0441\u044f. \u0422\u044b \u0441\u043d\u043e\u0432\u0430 \u0437\u0434\u043e\u0440\u043e\u0432.</i>"
    if earned: txt += f"\n💡 <i>+{earned}🧪 \u043f\u0430\u0441\u0441\u0438\u0432\u043d\u043e</i>"
    await msg.answer(txt, reply_markup=kb_main())


# ── Профиль ───────────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"профиль", "мой профиль", "бк", "стат"}))
async def cmd_profile(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    check_auto_cure(u); apply_passive(u); save_db(DB)
    await msg.answer(fmt_profile(u))


# ── Ник вируса ────────────────────────────────────────────────────────────────

@router.message(SW("ник "))
async def cmd_nick(msg: Message) -> None:
    u    = get_or_create(DB, msg.from_user)
    uid  = str(msg.from_user.id)
    name = re.sub(r"(?i)^\u043d\u0438\u043a\s+", "", (msg.text or "").strip()).strip()
    if not name:
        await msg.answer("\u274c \u0423\u043a\u0430\u0436\u0438 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435: <b>\u041d\u0438\u043a \u041c\u043e\u0439\u0412\u0438\u0440\u0443\u0441</b>"); return
    if not (2 <= len(name) <= 30):
        await msg.answer("\u274c \u0414\u043b\u0438\u043d\u0430 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u044f: 2\u201330 \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432."); return
    if vname_taken(name, uid):
        await msg.answer(f"\u274c \u0418\u043c\u044f <b>{name}</b> \u0443\u0436\u0435 \u0437\u0430\u043d\u044f\u0442\u043e."); return
    u["virus_name"] = name; save_db(DB)
    await msg.answer(f"🦫 \u0422\u0432\u043e\u0439 \u0432\u0438\u0440\u0443\u0441: <b>{name}</b>")


# ── Ферма (текст) ─────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"ферма", "фарма", "работать", "фарм", "farm"}))
async def cmd_farm(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    check_auto_cure(u)
    if u["is_infected"]:
        await msg.answer(infected_msg(u)); return
    cdr = max(0.0, FARM_CD - (time.time() - u.get("last_farm", 0)))
    if cdr > 0:
        await msg.answer(f"\u26cf\ufe0f <b>\u0424\u0415\u0420\u041c\u0410 \u041d\u0410 \u041f\u0415\u0420\u0415\u0417\u0410\u0420\u042f\u0414\u041a\u0415</b>\n\u0421\u043b\u0435\u0434. \u0441\u0431\u043e\u0440 \u0447\u0435\u0440\u0435\u0437: <b>{cd_str(cdr)}</b>"); return
    earned = 30 + u["lab_level"] * 3 + random.randint(0, 10)
    u["pathogens"] += earned; u["total_earned"] += earned; u["last_farm"] = time.time()
    lvl = give_xp(u, 10); save_db(DB)
    r = f"\u26cf\ufe0f <b>\u0421\u0411\u041e\u0420 \u0417\u0410\u0412\u0415\u0420\u0428\u0401\u041d</b>\n\u0421\u043e\u0431\u0440\u0430\u043d\u043e: <b>+{earned}🧪</b>\n\u0421\u043b\u0435\u0434. \u0441\u0431\u043e\u0440 \u0447\u0435\u0440\u0435\u0437 <b>4 \u0447\u0430\u0441\u0430</b>."
    if lvl: r += "\n" + "\n".join(lvl)
    await msg.answer(r)


# ── Лаба (текст) ──────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"лаба", "лаборатория", "лаб", "моя лаба"}))
async def cmd_lab(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    check_auto_cure(u); apply_passive(u)
    if u["is_infected"]:
        await msg.answer(infected_msg(u)); return
    save_db(DB)
    await msg.answer(fmt_lab(u), reply_markup=kb_lab(u))


# ── Магазин (текст) ───────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"магазин", "шоп", "улучшения", "апгрейд"}))
async def cmd_shop(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    check_auto_cure(u); apply_passive(u)
    if u["is_infected"]:
        await msg.answer(infected_msg(u)); return
    save_db(DB)
    await msg.answer(fmt_shop(u), reply_markup=kb_shop(u))


# ── Заразить ──────────────────────────────────────────────────────────────────

@router.message(SW("заразить"))
async def cmd_infect(msg: Message) -> None:
    att = get_or_create(DB, msg.from_user)
    check_auto_cure(att); apply_passive(att)
    if att["is_infected"]:
        await msg.answer(infected_msg(att)); return

    tgt: Optional[Dict] = None
    if msg.reply_to_message and msg.reply_to_message.from_user:
        fu = msg.reply_to_message.from_user
        if not fu.is_bot:
            tgt = get_or_create(DB, fu)
    else:
        m = re.search(r"@(\w+)", msg.text or "", re.I)
        if m:
            sn = m.group(1).lower()
            tgt = next((ud for ud in DB.values() if ud.get("username","").lower()==sn), None)

    if tgt is None:
        await msg.answer("\u274c \u0423\u043a\u0430\u0436\u0438 \u0446\u0435\u043b\u044c:\n\u2022 <b>\u0417\u0430\u0440\u0430\u0437\u0438\u0442\u044c @\u043d\u0438\u043a</b>\n\u2022 \u0418\u043b\u0438 \u0440\u0435\u043f\u043b\u0430\u0435\u043c \u043d\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435"); return
    if tgt["id"] == msg.from_user.id:
        await msg.answer("🤡 \u0421\u0430\u043c\u043e\u0437\u0430\u0440\u0430\u0436\u0435\u043d\u0438\u0435 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e."); return
    if tgt["is_infected"]:
        left = infection_left(tgt)
        await msg.answer(f"\u2623\ufe0f <b>{tgt['name']}</b> \u0443\u0436\u0435 \u0437\u0430\u0440\u0430\u0436\u0451\u043d. \u041e\u043f\u0440\u0430\u0432\u0438\u0442\u0441\u044f \u0447\u0435\u0440\u0435\u0437 <b>{cd_str(left)}</b>."); return

    el = time.time() - att["last_infect"]
    if el < INFECT_CD:
        await msg.answer(f"\u23f3 \u0428\u0442\u0430\u043c\u043c \u0441\u043e\u0437\u0440\u0435\u0432\u0430\u0435\u0442 \u0435\u0449\u0451 <b>{cd_str(INFECT_CD-el)}</b>."); return

    att["last_infect"] = time.time()
    chance = inf_chance(att, tgt)
    vname  = att.get("virus_name") or att["name"]

    if random.random() <= chance:
        tgt.update(is_infected=True, infected_at=time.time(),
                   infected_by_id=att["id"], infected_by_name=vname)
        att["infected_count"] += 1
        stolen = 0
        if att["lethality"] > 0:
            stolen = min(int(tgt["pathogens"] * att["lethality"] * 0.06), tgt["pathogens"])
            tgt["pathogens"]      -= stolen
            att["pathogens"]      += stolen
            att["total_earned"]   += stolen
        lvl = give_xp(att, 20); save_db(DB)
        r = (
            f"\u2623\ufe0f <b>\u0417\u0410\u0420\u0410\u0416\u0415\u041d\u0418\u0415 \u0423\u0421\u041f\u0415\u0428\u041d\u041e!</b>\n"
            f"\u0416\u0435\u0440\u0442\u0432\u0430: <b>{tgt['name']}</b>\n"
            f"\u0412\u0438\u0440\u0443\u0441 <b>{vname}</b> \u043f\u0440\u043e\u043d\u0438\u043a.\n"
            f"\u0428\u0430\u043d\u0441: <b>{int(chance*100)}%</b> \u00b7 \u0421\u043f\u0430\u0434\u0451\u0442 \u0447\u0435\u0440\u0435\u0437 5 \u0447."
        )
        if stolen: r += f"\n💀 \u041f\u043e\u0445\u0438\u0449\u0435\u043d\u043e: <b>{stolen}🧪</b>"
        if lvl:    r += "\n" + "\n".join(lvl)
    else:
        save_db(DB)
        r = f"💨 <b>\u0428\u0422\u0410\u041c\u041c \u041d\u0415 \u041f\u0420\u0418\u0416\u0418\u041b\u0421\u042f</b>\n<b>{tgt['name']}</b> \u0443\u0441\u0442\u043e\u044f\u043b.\n\u0428\u0430\u043d\u0441: {int(chance*100)}%"
    await msg.answer(r)


# ── Вылечиться ────────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"вылечиться", "антидот", "лечение", "вылечить себя"}))
async def cmd_cure(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    if check_auto_cure(u):
        save_db(DB); await msg.answer("💊 \u0412\u0438\u0440\u0443\u0441 \u0441\u0430\u043c\u043e\u0443\u0441\u0442\u0440\u0430\u043d\u0438\u043b\u0441\u044f \u2014 \u0432\u044b \u0443\u0436\u0435 \u0437\u0434\u043e\u0440\u043e\u0432\u044b."); return
    if not u["is_infected"]:
        await msg.answer("\u2705 \u0412\u044b \u0437\u0434\u043e\u0440\u043e\u0432\u044b. \u0412\u0438\u0440\u0443\u0441 \u043d\u0435 \u043e\u0431\u043d\u0430\u0440\u0443\u0436\u0435\u043d."); return
    if u["pathogens"] < 30:
        left = infection_left(u)
        await msg.answer(
            f"\u274c \u041d\u0443\u0436\u043d\u043e <b>30🧪</b> \u0434\u043b\u044f \u0430\u043d\u0442\u0438\u0434\u043e\u0442\u0430.\n"
            f"\u0423 \u0432\u0430\u0441: <b>{u['pathogens']}🧪</b>.\n"
            f"\u0412\u0438\u0440\u0443\u0441 \u0441\u043f\u0430\u0434\u0451\u0442 \u0441\u0430\u043c \u0447\u0435\u0440\u0435\u0437: <b>{cd_str(left)}</b>."
        ); return
    u.update(pathogens=u["pathogens"]-30, is_infected=False, infected_at=0,
             infected_by_id=None, infected_by_name=None)
    u["cured_count"] += 1
    lvl = give_xp(u, 10); save_db(DB)
    r = "💊 <b>\u0410\u041d\u0422\u0418\u0414\u041e\u0422 \u0412\u0412\u0415\u0414\u0401\u041d</b>\n\u041f\u043e\u0442\u0440\u0430\u0447\u0435\u043d\u043e: <b>30🧪</b>\n\u0412\u0438\u0440\u0443\u0441 \u043d\u0435\u0439\u0442\u0440\u0430\u043b\u0438\u0437\u043e\u0432\u0430\u043d. \u0411\u0435\u0440\u0435\u0433\u0438\u0442\u0435\u0441\u044c..."
    if lvl: r += "\n" + "\n".join(lvl)
    await msg.answer(r)


# ── Топ (текст) ───────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"топ заражённых", "топ зараженных", "топ", "рейтинг"}))
async def cmd_top_inf_txt(msg: Message) -> None:
    await msg.answer(fmt_top_inf(), reply_markup=kb_back())


@router.message(F.text.lower().in_({"топ патогенов", "богачи"}))
async def cmd_top_pat_txt(msg: Message) -> None:
    await msg.answer(fmt_top_pat(), reply_markup=kb_back())


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "main_menu")
async def cb_menu(cq: CallbackQuery) -> None:
    await cq.message.edit_text("\u2623\ufe0f <b>\u0411\u0418\u041e-\u0412\u041e\u0419\u041d\u042b</b>\n\u041c\u0438\u0440 \u043f\u0430\u0442\u043e\u0433\u0435\u043d\u043e\u0432 \u0436\u0434\u0451\u0442:", reply_markup=kb_main())
    await cq.answer()


@router.callback_query(F.data == "profile")
async def cb_profile(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    check_auto_cure(u); apply_passive(u); save_db(DB)
    await cq.message.edit_text(fmt_profile(u), reply_markup=kb_back())
    await cq.answer()


# ── Ферма inline ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "farm")
async def cb_farm(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    check_auto_cure(u)
    if u["is_infected"]:
        await cq.answer("\u2623\ufe0f \u0422\u044b \u0437\u0430\u0440\u0430\u0436\u0451\u043d! \u0414\u0435\u0439\u0441\u0442\u0432\u0438\u044f \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d\u044b.", show_alert=True); return
    save_db(DB)
    await cq.message.edit_text(fmt_farm_page(u), reply_markup=kb_farm(u))
    await cq.answer()


@router.callback_query(F.data == "farm_work")
async def cb_farm_work(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    check_auto_cure(u)
    if u["is_infected"]:
        await cq.answer("\u2623\ufe0f \u0422\u044b \u0437\u0430\u0440\u0430\u0436\u0451\u043d!", show_alert=True); return
    cdr = max(0.0, FARM_CD - (time.time() - u.get("last_farm", 0)))
    if cdr > 0:
        await cq.answer(f"\u23f3 \u0415\u0449\u0451 {cd_str(cdr)}", show_alert=True); return
    earned = 30 + u["lab_level"] * 3 + random.randint(0, 10)
    u["pathogens"] += earned; u["total_earned"] += earned; u["last_farm"] = time.time()
    lvl = give_xp(u, 10); save_db(DB)
    txt = f"\u26cf\ufe0f <b>\u0421\u0411\u041e\u0420 \u0417\u0410\u0412\u0415\u0420\u0428\u0401\u041d</b>\n+<b>{earned}🧪</b>\n\u0421\u043b\u0435\u0434. \u0441\u0431\u043e\u0440 \u0447\u0435\u0440\u0435\u0437 <b>4 \u0447\u0430\u0441\u0430</b>."
    if lvl: txt += "\n" + "\n".join(lvl)
    await cq.message.edit_text(txt, reply_markup=kb_farm(u))
    await cq.answer(f"+{earned}🧪")


@router.callback_query(F.data == "farm_noop")
async def cb_farm_noop(cq: CallbackQuery) -> None:
    u   = get_or_create(DB, cq.from_user)
    cdr = max(0.0, FARM_CD - (time.time() - u.get("last_farm", 0)))
    await cq.answer(f"\u23f3 \u0421\u0431\u043e\u0440 \u0447\u0435\u0440\u0435\u0437 {cd_str(cdr)}" if cdr > 0 else "\u2705 \u041c\u043e\u0436\u043d\u043e \u0441\u043e\u0431\u0438\u0440\u0430\u0442\u044c!", show_alert=True)


# ── Лаба inline ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "lab")
async def cb_lab(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    check_auto_cure(u); apply_passive(u)
    if u["is_infected"]:
        await cq.answer("\u2623\ufe0f \u0417\u0430\u0440\u0430\u0436\u0451\u043d! \u041b\u0430\u0431\u0430 \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d\u0430.", show_alert=True); return
    save_db(DB)
    await cq.message.edit_text(fmt_lab(u), reply_markup=kb_lab(u))
    await cq.answer()


@router.callback_query(F.data == "lab_work")
async def cb_lab_work(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    check_auto_cure(u)
    if u["is_infected"]:
        await cq.answer("\u2623\ufe0f \u0422\u044b \u0437\u0430\u0440\u0430\u0436\u0451\u043d!", show_alert=True); return
    cdr = LAB_CD - (time.time() - u["last_lab_work"])
    if cdr > 0:
        await cq.answer(f"\u23f3 \u0415\u0449\u0451 {cd_str(cdr)}", show_alert=True); return
    earned = 10 + u["lab_level"] * 5 + random.randint(0, u["lab_level"] * 3)
    u["pathogens"] += earned; u["total_earned"] += earned; u["last_lab_work"] = time.time()
    lvl = give_xp(u, 15); save_db(DB)
    txt = f"\u2697\ufe0f <b>\u0421\u041c\u0415\u041d\u0410 \u0417\u0410\u0412\u0415\u0420\u0428\u0415\u041d\u0410</b>\n+<b>{earned}🧪</b>\n\u0421\u043b\u0435\u0434. \u0447\u0435\u0440\u0435\u0437 <b>1 \u0447\u0430\u0441</b>."
    if lvl: txt += "\n" + "\n".join(lvl)
    await cq.message.edit_text(txt, reply_markup=kb_lab(u))
    await cq.answer(f"+{earned}🧪")


@router.callback_query(F.data == "lab_upgrade")
async def cb_lab_up(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    check_auto_cure(u)
    if u["is_infected"]:
        await cq.answer("\u2623\ufe0f \u0422\u044b \u0437\u0430\u0440\u0430\u0436\u0451\u043d!", show_alert=True); return
    cost = u["lab_level"] * 80
    if u["pathogens"] < cost:
        await cq.answer(f"\u274c \u041d\u0443\u0436\u043d\u043e {cost}🧪 (\u0443 \u0432\u0430\u0441 {u['pathogens']})", show_alert=True); return
    u["pathogens"] -= cost; u["lab_level"] += 1; save_db(DB)
    await cq.message.edit_text(f"🔬 <b>\u041b\u0410\u0411\u0410 \u0423\u041b\u0423\u0427\u0428\u0415\u041d\u0410</b> \u2192 \u0443\u0440.<b>{u['lab_level']}</b>\n\n" + fmt_lab(u), reply_markup=kb_lab(u))
    await cq.answer(f"\u041b\u0430\u0431\u0430 \u0443\u0440.{u['lab_level']}!")


@router.callback_query(F.data == "lab_noop")
async def cb_lab_noop(cq: CallbackQuery) -> None:
    u   = get_or_create(DB, cq.from_user)
    cdr = max(0.0, LAB_CD - (time.time() - u["last_lab_work"]))
    await cq.answer(f"\u23f3 \u0421\u043c\u0435\u043d\u0430 \u0447\u0435\u0440\u0435\u0437 {cd_str(cdr)}" if cdr > 0 else "\u2705 \u041c\u043e\u0436\u043d\u043e \u0440\u0430\u0431\u043e\u0442\u0430\u0442\u044c!", show_alert=True)


# ── Магазин inline ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "shop")
async def cb_shop(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    check_auto_cure(u); apply_passive(u)
    if u["is_infected"]:
        await cq.answer("\u2623\ufe0f \u0417\u0430\u0440\u0430\u0436\u0451\u043d! \u041c\u0430\u0433\u0430\u0437\u0438\u043d \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d.", show_alert=True); return
    save_db(DB)
    await cq.message.edit_text(fmt_shop(u), reply_markup=kb_shop(u))
    await cq.answer()


def _buy(fu, key: str) -> Tuple[bool, str, Dict]:
    u = get_or_create(DB, fu)
    check_auto_cure(u)
    if u["is_infected"]:
        return False, "\u2623\ufe0f \u0422\u044b \u0437\u0430\u0440\u0430\u0436\u0451\u043d!", u
    cost = upgrade_cost(key, u[key])
    if u["pathogens"] < cost:
        return False, f"\u041d\u0443\u0436\u043d\u043e {cost}🧪 (\u0443 \u0432\u0430\u0441 {u['pathogens']})", u
    u["pathogens"] -= cost; u[key] += 1; save_db(DB)
    return True, f"\u0443\u0440.{u[key]}", u


@router.callback_query(F.data == "buy_infectivity")
async def cb_bi(cq: CallbackQuery) -> None:
    ok, t, u = _buy(cq.from_user, "infectivity")
    if ok: await cq.message.edit_text(fmt_shop(u), reply_markup=kb_shop(u)); await cq.answer(f"🦫 {t}!")
    else:  await cq.answer(f"\u274c {t}", show_alert=True)


@router.callback_query(F.data == "buy_lethality")
async def cb_bl(cq: CallbackQuery) -> None:
    ok, t, u = _buy(cq.from_user, "lethality")
    if ok: await cq.message.edit_text(fmt_shop(u), reply_markup=kb_shop(u)); await cq.answer(f"\u2620\ufe0f {t}!")
    else:  await cq.answer(f"\u274c {t}", show_alert=True)


@router.callback_query(F.data == "buy_resistance")
async def cb_br(cq: CallbackQuery) -> None:
    ok, t, u = _buy(cq.from_user, "resistance")
    if ok: await cq.message.edit_text(fmt_shop(u), reply_markup=kb_shop(u)); await cq.answer(f"🛡\ufe0f {t}!")
    else:  await cq.answer(f"\u274c {t}", show_alert=True)


@router.callback_query(F.data == "buy_vaccines")
async def cb_bv(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    check_auto_cure(u)
    if u["is_infected"]:
        await cq.answer("\u2623\ufe0f \u0422\u044b \u0437\u0430\u0440\u0430\u0436\u0451\u043d!", show_alert=True); return
    if u["vaccines"] >= MAX_VAC:
        await cq.answer("\u0423\u0436\u0435 \u043c\u0430\u043a\u0441\u0438\u043c\u0443\u043c \u0432\u0430\u043a\u0446\u0438\u043d!", show_alert=True); return
    cost = VAC_BASE * (u["vaccines"] + 1)
    if u["pathogens"] < cost:
        await cq.answer(f"\u274c \u041d\u0443\u0436\u043d\u043e {cost}🧪 (\u0443 \u0432\u0430\u0441 {u['pathogens']})", show_alert=True); return
    u["pathogens"] -= cost; u["vaccines"] += 1; save_db(DB)
    await cq.message.edit_text(fmt_shop(u), reply_markup=kb_shop(u))
    await cq.answer(f"💉 \u0412\u0430\u043a\u0446\u0438\u043d\u0430 {u['vaccines']}/{MAX_VAC}!")


@router.callback_query(F.data == "shop_noop")
async def cb_snoop(cq: CallbackQuery) -> None:
    await cq.answer("\u0423\u0436\u0435 \u043c\u0430\u043a\u0441\u0438\u043c\u0443\u043c!", show_alert=True)


# ── Топ inline ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "top_inf")
async def cb_ti(cq: CallbackQuery) -> None:
    await cq.message.edit_text(fmt_top_inf(), reply_markup=kb_back()); await cq.answer()


@router.callback_query(F.data == "top_pat")
async def cb_tp(cq: CallbackQuery) -> None:
    await cq.message.edit_text(fmt_top_pat(), reply_markup=kb_back()); await cq.answer()


_bot: Bot = None
_dp:  Dispatcher = None


async def _on_startup(app: web.Application) -> None:
    await _bot.delete_webhook(drop_pending_updates=True)
    await _bot.set_webhook(WEBHOOK_URL)
    log.info(f"Webhook set: {WEBHOOK_URL}")


async def _on_shutdown(app: web.Application) -> None:
    await _bot.delete_webhook()
    await _bot.session.close()
    log.info("Webhook removed.")


def main() -> None:
    global DB, _bot, _dp

    DB   = load_db()
    log.info(f"Loaded {len(DB)} users.")

    _bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    _dp  = Dispatcher()
    _dp.include_router(router)

    app = web.Application()

    async def ok(_: web.Request) -> web.Response:
        return web.Response(text="OK")
    app.router.add_get("/",       ok)
    app.router.add_get("/health", ok)

    SimpleRequestHandler(dispatcher=_dp, bot=_bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, _dp, bot=_bot)

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)

    log.info(f"Server on 0.0.0.0:{PORT}  |  Webhook: {WEBHOOK_URL}")
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
