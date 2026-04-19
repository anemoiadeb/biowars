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
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import CommandStart, Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
TOKEN        = os.getenv("BOT_TOKEN", "YOUR_TOKEN_HERE")
PORT         = int(os.getenv("PORT", 8080))
DATA_FILE    = Path("bio_wars.json")


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bio_wars")

# Глушим мусорные TelegramConflictError при деплое Render
class _NoConflict(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "TelegramConflictError" not in record.getMessage()

for _name in ("aiogram.client.session.aiohttp", "aiogram", "root"):
    logging.getLogger(_name).addFilter(_NoConflict())

SEP = "\u2500" * 26

FARM_CD         = 4 * 3600    # 4 ч   ферма
INFECT_CD       = 1800        # 30 м  кд атаки
INFECT_DURATION = 5 * 3600    # 5 ч   автоснятие заражения

BASE_COST  = {"infectivity": 40, "lethality": 50, "resistance": 60}
VAC_BASE   = 30
MAX_VAC    = 5

FARM_ITEMS = [
    ("Чашка Петри",      0.3000),
    ("Пробирка",         0.2500),
    ("Образец ДНК",      0.1500),
    ("Мутаген",          0.1000),
    ("Вирусная капсула", 0.0700),
    ("Кристалл патогена",0.0500),
    ("Нейротоксин",      0.0200),
    ("Геном вируса",     0.0100),
    ("Антидот 15м",      0.00500),
    ("Антидот 30м",      0.00300),
    ("Антидот 1ч",       0.00200),
    ("Антидот 5ч",       0.00080),
    ("Антидот 12ч",      0.00050),
    ("Антидот 24ч",      0.00030),
    ("Антидот 48ч",      0.00010),
    ("Скидка 15м",       0.00400),
    ("Скидка 30м",       0.00250),
    ("Скидка 1ч",        0.00150),
    ("Скидка 5ч",        0.00060),
    ("Скидка 12ч",       0.00040),
    ("Скидка 24ч",       0.00020),
    ("Скидка 48ч",       0.00008),
]

BUFF_DUR = {
    "Антидот 15м": 900,    "Антидот 30м": 1800,   "Антидот 1ч": 3600,
    "Антидот 5ч":  18000,  "Антидот 12ч": 43200,  "Антидот 24ч": 86400,
    "Антидот 48ч": 172800,
    "Скидка 15м":  900,    "Скидка 30м":  1800,   "Скидка 1ч":  3600,
    "Скидка 5ч":   18000,  "Скидка 12ч":  43200,  "Скидка 24ч": 86400,
    "Скидка 48ч":  172800,
}

ITEM_EMOJI = {
    "Чашка Петри": "🧫", "Пробирка": "🧪", "Образец ДНК": "🧬",
    "Мутаген": "☢️",     "Вирусная капсула": "💊",
    "Кристалл патогена": "💎", "Нейротоксин": "☠️", "Геном вируса": "🦠",
}

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
        "infectivity": 0, "lethality": 0, "resistance": 0, "vaccines": 0,
        "last_farm": 0, "last_infect": 0,
        "rollback_chance": 0.005,
        "inventory": {},
        "active_antidote": 0,
        "active_discount": 0,
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



def is_antidote_buff(name: str) -> bool:
    return name.startswith("Антидот")

def is_discount_buff(name: str) -> bool:
    return name.startswith("Скидка")

def is_buff(name: str) -> bool:
    return is_antidote_buff(name) or is_discount_buff(name)

def has_active_antidote(u: Dict) -> bool:
    return time.time() < u.get("active_antidote", 0)

def has_active_discount(u: Dict) -> bool:
    return time.time() < u.get("active_discount", 0)

def maybe_rollback(u: Dict) -> Optional[str]:
    upgradeable = [k for k in ("infectivity", "lethality", "resistance") if u.get(k, 0) > 0]
    chance = u.get("rollback_chance", 0.005)
    if not upgradeable or random.random() > chance:
        return None
    key = random.choice(upgradeable)
    u[key] = 0
    u["rollback_chance"] = 0.005  # сброс после отката
    names = {"infectivity": "Заразность", "lethality": "Летальность", "resistance": "Резистентность"}
    return (
        f"⚠️ <b>На вашей лаборатории произошёл разгром!</b>\n"
        f"К сожалению ваш вирус тоже потерпел неудачу.\n"
        f"Откат: <b>{names[key]}</b>"
    )

def farm_drop(u: Dict) -> Optional[str]:
    inv = u.setdefault("inventory", {})
    for name, chance in FARM_ITEMS:
        if random.random() < chance:
            inv[name] = inv.get(name, 0) + 1
            return name
    return None

def fmt_inventory(u: Dict) -> str:
    inv  = u.get("inventory", {})
    now  = time.time()
    lines = [f"🎒 <b>ИНВЕНТАРЬ</b> · {u['name']}\n{SEP}"]
    ant  = u.get("active_antidote", 0)
    dis  = u.get("active_discount",  0)
    if ant > now:
        lines.append(f"🛡 Антидот активен: <b>{cd_str(ant - now)}</b>")
    if dis > now:
        lines.append(f"💰 Скидка активна: <b>{cd_str(dis - now)}</b>")
    if ant > now or dis > now:
        lines.append(SEP)
    items = {k: v for k, v in inv.items() if not is_buff(k)}
    buffs = {k: v for k, v in inv.items() if is_buff(k) and v > 0}
    if items:
        lines.append("📦 <b>Предметы:</b>")
        for name, cnt in items.items():
            em = ITEM_EMOJI.get(name, "📦")
            lines.append(f"  {em} {name} × {cnt}")
    if buffs:
        lines.append("✨ <b>Баффы в запасе:</b>")
        for name in sorted(buffs.keys()):
            em = "🛡" if is_antidote_buff(name) else "💰"
            lines.append(f"  {em} {name} × {buffs[name]}")
    if not items and not buffs and ant <= now and dis <= now:
        lines.append("Инвентарь пуст.")
    return "\n".join(lines)

def kb_inventory(u: Dict) -> InlineKeyboardMarkup:
    b    = InlineKeyboardBuilder()
    inv  = u.get("inventory", {})
    buffs = {k: v for k, v in inv.items() if is_buff(k) and v > 0}
    for name in sorted(buffs.keys()):
        em = "🛡" if is_antidote_buff(name) else "💰"
        short = name.replace("Антидот ", "🛡 ").replace("Скидка ", "💰 ")
        b.button(text=f"{em} Активировать {short}", callback_data=f"ubuff:{name}")
    b.button(text="◀️ Назад", callback_data="main_menu")
    b.adjust(1)
    return b.as_markup()


# ══════════════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════

def kb_main() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🧫 Профиль",        callback_data="profile")
    b.button(text="🔬 Лаборатория",    callback_data="lab")
    b.button(text="⛏️ Ферма",          callback_data="farm")
    b.button(text="🎒 Инвентарь",      callback_data="inventory")
    b.button(text="☣️ Топ заражённых", callback_data="top_inf")
    b.button(text="🏆 Топ патогенов",  callback_data="top_pat")
    b.adjust(2, 2, 2)
    return b.as_markup()


def kb_lab(u: Dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for emoji, label, key in [
        ("🦫", "Заразность",   "infectivity"),
        ("☠️",  "Летальность",  "lethality"),
        ("🛡️", "Резистентн.",  "resistance"),
    ]:
        lv   = u[key]
        cost = upgrade_cost(key, lv)
        if has_active_discount(u): cost = max(1, int(cost * 0.95))
        b.button(text=f"{emoji} {label} ур.{lv} [{cost}🧪]", callback_data=f"buy_{key}")
    vac  = u["vaccines"]
    vcst = VAC_BASE * (vac + 1)
    if has_active_discount(u): vcst = max(1, int(vcst * 0.95))
    if vac < MAX_VAC:
        b.button(text=f"💉 Вакцина {vac}/{MAX_VAC} [{vcst}🧪]", callback_data="buy_vaccines")
    else:
        b.button(text="💉 Вакцина MAX", callback_data="lab_noop")
    b.button(text="◀️ Назад", callback_data="main_menu")
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

        f"🦫 \u0417\u0430\u0440\u0430\u0437\u043d\u043e\u0441\u0442\u044c  \u0443\u0440.<b>{u['infectivity']}</b>\n"
        f"\u2620\ufe0f  \u041b\u0435\u0442\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u044c \u0443\u0440.<b>{u['lethality']}</b>\n"
        f"🛡  \u0420\u0435\u0437\u0438\u0441\u0442\u0435\u043d\u0442\u043d. \u0443\u0440.<b>{u['resistance']}</b>\n"
        f"💉 \u0412\u0430\u043a\u0446\u0438\u043d\u044b: <b>{u['vaccines']}/{MAX_VAC}</b>\n{SEP}\n"
        f"\u2623\ufe0f \u0417\u0430\u0440\u0430\u0437\u0438\u043b: <b>{u['infected_count']}</b>  \u00b7  "
        f"💊 \u0412\u044b\u043b\u0435\u0447\u0438\u043b\u0441\u044f: <b>{u['cured_count']}</b>\n"
        f"📈 \u0421\u0442\u0430\u0442\u0443\u0441: <b>{status}</b>{src}\n"
    )


def fmt_lab(u: Dict) -> str:
    chance_pct = u.get("rollback_chance", 0.005) * 100
    dis = "  · 🏷️ Скидка -5% активна" if has_active_discount(u) else ""
    return (
        f"🔬 <b>ЛАБОРАТОРИЯ ВИРУСА</b>\n{SEP}\n"
        f"🦫 Заразность    — +4% к шансу/ур.\n"
        f"☠️  Летальность   — крадёт патогены\n"
        f"🛡️ Резистентн. — -5% к входящему/ур.\n"
        f"💉 Вакцина       — -12% к заражению\n"
        f"{SEP}\n"
        f"💰 Баланс: <b>{u['pathogens']}🧪</b>{dis}\n"
    )


def fmt_farm_page(u: Dict) -> str:
    cdr = max(0.0, FARM_CD - (time.time() - u.get("last_farm", 0)))
    return (
        f"\u26cf\ufe0f <b>\u0424\u0415\u0420\u041c\u0410 \u041f\u0410\u0422\u041e\u0413\u0415\u041d\u041e\u0412</b>\n{SEP}\n"
        f"\u23f1  \u0421\u043b\u0435\u0434. \u0441\u0431\u043e\u0440: <b>{''+cd_str(cdr) if cdr>0 else chr(9989)+' \u0413\u043e\u0442\u043e\u0432\u043e'}</b>\n"
        f"💰 Доход: ~<b>30–40🧪</b>\n"
        f"\u26a0\ufe0f  \u041a\u0443\u043b\u0434\u0430\u0443\u043d: 4 \u0447\u0430\u0441\u0430\n"
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
async def error_handler(event) -> None:
    log.error(f"Handler error: {type(event.exception).__name__}: {event.exception}", exc_info=event.exception)

HELP = (
    f"☣️ <b>БИО-ВОЙНЫ v2</b>\n{SEP}\n"
    "• <b>Био-война</b>         — главное меню\n"
    "• <b>Профиль</b>           — ваш БК\n"
    "• <b>Заразить @ник</b>     — атака (только в группах)\n"
    "• <b>Вылечиться</b>        — антидот (30🧪)\n"
    "• <b>Ферма</b>             — сбор патогенов (КД 4ч)\n"
    "• <b>Лаба</b>              — прокачка вируса\n"
    "• <b>Инвентарь</b>         — предметы и баффы\n"
    "• <b>Ник Название</b>      — имя вируса\n"
    "• <b>Топ заражённых</b>    — рейтинг\n"
    "• <b>Топ патогенов</b>     — богачи\n\n"
    f"{SEP}\n"
    "☣️ Заражение блокирует все действия на 5 часов."
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
    save_db(DB)
    txt = "☣️ <b>БИО-ВОЙНЫ</b>\nМир патогенов ждёт:"
    if cured:  txt += "\n\n💊 <i>Вирус самоустранился. Ты снова здоров.</i>"
    await msg.answer(txt, reply_markup=kb_main())


# ── Профиль ───────────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"профиль", "мой профиль", "бк", "стат"}))
async def cmd_profile(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    check_auto_cure(u)
    rb = maybe_rollback(u)
    save_db(DB)
    if rb: await msg.answer(rb)
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
    earned = 30 + random.randint(0, 10)
    u["pathogens"] += earned; u["total_earned"] += earned; u["last_farm"] = time.time()
    lvl = give_xp(u, 10)
    drop = farm_drop(u)
    save_db(DB)
    r = f"\u26cf\ufe0f <b>\u0421\u0411\u041e\u0420 \u0417\u0410\u0412\u0415\u0420\u0428\u0401\u041d</b>\n\u0421\u043e\u0431\u0440\u0430\u043d\u043e: <b>+{earned}🧪</b>\n\u0421\u043b\u0435\u0434. \u0441\u0431\u043e\u0440 \u0447\u0435\u0440\u0435\u0437 <b>4 \u0447\u0430\u0441\u0430</b>."
    if drop: r += f"\n\n🎁 <b>Находка:</b> {ITEM_EMOJI.get(drop, '✨') if not is_buff(drop) else ('🛡' if is_antidote_buff(drop) else '💰')} {drop}"
    if lvl: r += "\n" + "\n".join(lvl)
    await msg.answer(r)


# ── Лаба (текст) ──────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"лаба", "лаборатория", "лаб", "моя лаба"}))
async def cmd_lab(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    check_auto_cure(u)
    if u["is_infected"]:
        await msg.answer(infected_msg(u)); return
    rb = maybe_rollback(u)
    save_db(DB)
    if rb: await msg.answer(rb)
    await msg.answer(fmt_lab(u), reply_markup=kb_lab(u))



# ── Заразить ──────────────────────────────────────────────────────────────────

@router.message(SW("заразить"))
async def cmd_infect(msg: Message) -> None:
    if msg.chat.type == ChatType.PRIVATE:
        await msg.answer("❌ Заражать можно только в группах!")
        return
    att = get_or_create(DB, msg.from_user)
    check_auto_cure(att)
    if att["is_infected"]:
        await msg.answer(infected_msg(att)); return

    tgt: Optional[Dict] = None
    tgt_uid: Optional[int] = None

    if msg.reply_to_message and msg.reply_to_message.from_user:
        fu = msg.reply_to_message.from_user
        if not fu.is_bot:
            tgt = get_or_create(DB, fu)
            tgt_uid = fu.id
    else:
        m = re.search(r"@(\w+)", msg.text or "", re.I)
        if m:
            sn = m.group(1).lower()
            found = next((ud for ud in DB.values() if ud.get("username", "").lower() == sn), None)
            if found is None:
                await msg.answer("\u274c \u042d\u0442\u043e\u0442 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u0435\u0449\u0451 \u043d\u0435 \u0438\u0433\u0440\u0430\u043b \u0432 \u0431\u043e\u0442\u0430!")
                return
            tgt = found
            tgt_uid = found["id"]

    if tgt is None:
        await msg.answer("\u274c \u0423\u043a\u0430\u0436\u0438 \u0446\u0435\u043b\u044c:\n\u2022 <b>\u0417\u0430\u0440\u0430\u0437\u0438\u0442\u044c @\u043d\u0438\u043a</b>\n\u2022 \u0418\u043b\u0438 \u0440\u0435\u043f\u043b\u0430\u0435\u043c \u043d\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435"); return

    # Проверка что цель в этой группе
    if tgt_uid is not None:
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.types import ChatMemberLeft, ChatMemberBanned
        try:
            member = await msg.bot.get_chat_member(msg.chat.id, tgt_uid)
            if isinstance(member, (ChatMemberLeft, ChatMemberBanned)):
                await msg.answer("\u274c \u042d\u0442\u043e\u0433\u043e \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f \u0442\u0443\u0442 \u043d\u0435\u0442!")
                return
        except TelegramBadRequest:
            await msg.answer("\u274c \u042d\u0442\u043e\u0433\u043e \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f \u0442\u0443\u0442 \u043d\u0435\u0442!")
            return

    if tgt["id"] == msg.from_user.id:
        await msg.answer("🤡 \u0421\u0430\u043c\u043e\u0437\u0430\u0440\u0430\u0436\u0435\u043d\u0438\u0435 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e."); return
    if tgt["is_infected"]:
        left = infection_left(tgt)
        await msg.answer(f"\u2623\ufe0f <b>{tgt['name']}</b> \u0443\u0436\u0435 \u0437\u0430\u0440\u0430\u0436\u0451\u043d. \u041e\u043f\u0440\u0430\u0432\u0438\u0442\u0441\u044f \u0447\u0435\u0440\u0435\u0437 <b>{cd_str(left)}</b>."); return
    if has_active_antidote(tgt):
        await msg.answer(f"🛡 <b>{tgt['name']}</b> защищён антидотом!"); return

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
    check_auto_cure(u)
    rb = maybe_rollback(u)
    save_db(DB)
    txt = fmt_profile(u)
    if rb: txt = rb + "\n\n" + txt
    await cq.message.edit_text(txt, reply_markup=kb_back())
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
    rb = maybe_rollback(u)
    earned = 30 + random.randint(0, 10)
    u["pathogens"] += earned; u["total_earned"] += earned; u["last_farm"] = time.time()
    lvl = give_xp(u, 10)
    drop = farm_drop(u)
    save_db(DB)
    txt = f"\u26cf\ufe0f <b>\u0421\u0411\u041e\u0420 \u0417\u0410\u0412\u0415\u0420\u0428\u0401\u041d</b>\n+<b>{earned}🧪</b>\n\u0421\u043b\u0435\u0434. \u0441\u0431\u043e\u0440 \u0447\u0435\u0440\u0435\u0437 <b>4 \u0447\u0430\u0441\u0430</b>."
    if drop: txt += f"\n\n🎁 <b>Находка:</b> {ITEM_EMOJI.get(drop, '✨') if not is_buff(drop) else ('🛡' if is_antidote_buff(drop) else '💰')} {drop}"
    if lvl: txt += "\n" + "\n".join(lvl)
    if rb:   txt += "\n\n" + rb
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
    check_auto_cure(u)
    if u["is_infected"]:
        await cq.answer("☣️ Заражён! Лаборатория заблокирована.", show_alert=True); return
    rb = maybe_rollback(u)
    save_db(DB)
    txt = fmt_lab(u)
    if rb: txt = rb + "\n\n" + txt
    await cq.message.edit_text(txt, reply_markup=kb_lab(u))
    await cq.answer()



def _buy(fu, key: str) -> tuple:
    u = get_or_create(DB, fu)
    check_auto_cure(u)
    if u["is_infected"]:
        return False, "☣️ Ты заражён!", u, None
    rb = maybe_rollback(u)
    cost = upgrade_cost(key, u[key])
    if has_active_discount(u): cost = max(1, int(cost * 0.95))
    if u["pathogens"] < cost:
        return False, f"Нужно {cost}🧪 (у вас {u['pathogens']})", u, None
    u["pathogens"] -= cost; u[key] += 1
    u["rollback_chance"] = u.get("rollback_chance", 0.005) + 0.01
    save_db(DB)
    return True, f"ур.{u[key]}", u, rb


@router.callback_query(F.data == "buy_infectivity")
async def cb_bi(cq: CallbackQuery) -> None:
    ok, t, u, rb = _buy(cq.from_user, "infectivity")
    if ok:
        txt = fmt_lab(u)
        if rb: txt = rb + "\n\n" + txt
        await cq.message.edit_text(txt, reply_markup=kb_lab(u)); await cq.answer(f"🦫 {t}!")
    else:  await cq.answer(f"\u274c {t}", show_alert=True)


@router.callback_query(F.data == "buy_lethality")
async def cb_bl(cq: CallbackQuery) -> None:
    ok, t, u, rb = _buy(cq.from_user, "lethality")
    if ok:
        txt = fmt_lab(u)
        if rb: txt = rb + "\n\n" + txt
        await cq.message.edit_text(txt, reply_markup=kb_lab(u)); await cq.answer(f"\u2620\ufe0f {t}!")
    else:  await cq.answer(f"\u274c {t}", show_alert=True)


@router.callback_query(F.data == "buy_resistance")
async def cb_br(cq: CallbackQuery) -> None:
    ok, t, u, rb = _buy(cq.from_user, "resistance")
    if ok:
        txt = fmt_lab(u)
        if rb: txt = rb + "\n\n" + txt
        await cq.message.edit_text(txt, reply_markup=kb_lab(u)); await cq.answer(f"🛡\ufe0f {t}!")
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
    if has_active_discount(u): cost = max(1, int(cost * 0.95))
    if u["pathogens"] < cost:
        await cq.answer(f"\u274c \u041d\u0443\u0436\u043d\u043e {cost}🧪 (\u0443 \u0432\u0430\u0441 {u['pathogens']})", show_alert=True); return
    u["pathogens"] -= cost; u["vaccines"] += 1
    u["rollback_chance"] = u.get("rollback_chance", 0.005) + 0.01
    save_db(DB)
    await cq.message.edit_text(fmt_lab(u), reply_markup=kb_lab(u))
    await cq.answer(f"💉 Вакцина {u['vaccines']}/{MAX_VAC}!")


@router.callback_query(F.data == "lab_noop")
async def cb_lab_noop(cq: CallbackQuery) -> None:
    await cq.answer("Уже максимум!", show_alert=True)


# ── Топ inline ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "top_inf")
async def cb_ti(cq: CallbackQuery) -> None:
    await cq.message.edit_text(fmt_top_inf(), reply_markup=kb_back()); await cq.answer()


@router.callback_query(F.data == "top_pat")
async def cb_tp(cq: CallbackQuery) -> None:
    await cq.message.edit_text(fmt_top_pat(), reply_markup=kb_back()); await cq.answer()




# ── Инвентарь (текст) ─────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"инвентарь", "инв", "inventory"}))
async def cmd_inventory(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    check_auto_cure(u)
    rb = maybe_rollback(u)
    save_db(DB)
    if rb: await msg.answer(rb)
    await msg.answer(fmt_inventory(u), reply_markup=kb_inventory(u))


# ── Инвентарь inline ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "inventory")
async def cb_inventory(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    check_auto_cure(u)
    rb = maybe_rollback(u)
    save_db(DB)
    txt = fmt_inventory(u)
    if rb: txt = rb + "\n\n" + txt
    await cq.message.edit_text(txt, reply_markup=kb_inventory(u))
    await cq.answer()


@router.callback_query(F.data.startswith("ubuff:"))
async def cb_use_buff(cq: CallbackQuery) -> None:
    name = cq.data[6:]
    u    = get_or_create(DB, cq.from_user)
    inv  = u.setdefault("inventory", {})
    if inv.get(name, 0) <= 0:
        await cq.answer("❌ Такого баффа нет в инвентаре.", show_alert=True); return
    dur = BUFF_DUR.get(name, 0)
    if dur == 0:
        await cq.answer("❌ Неизвестный бафф.", show_alert=True); return
    now = time.time()
    if is_antidote_buff(name):
        current = max(u.get("active_antidote", 0), now)
        u["active_antidote"] = current + dur
        label = "🛡 Антидот"
    else:
        current = max(u.get("active_discount", 0), now)
        u["active_discount"] = current + dur
        label = "💰 Скидка"
    inv[name] -= 1
    save_db(DB)
    await cq.message.edit_text(fmt_inventory(u), reply_markup=kb_inventory(u))
    await cq.answer(f"{label} активирован на {name.split()[-1]}!")

async def health_server() -> None:
    async def ok(_: web.Request) -> web.Response:
        return web.Response(text="OK")
    app = web.Application()
    app.router.add_get("/",       ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info(f"Health server on 0.0.0.0:{PORT}")


async def main() -> None:
    import signal
    global DB
    DB = load_db()
    log.info(f"Loaded {len(DB)} users.")

    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp  = Dispatcher()
    dp.include_router(router)

    # Graceful shutdown при SIGTERM (Render убивает старый инстанс)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_sigterm():
        log.info("SIGTERM received — stopping polling.")
        stop_event.set()

    loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
    loop.add_signal_handler(signal.SIGINT,  _handle_sigterm)

    await bot.delete_webhook(drop_pending_updates=True)
    await health_server()

    # Ждём чтобы старый инстанс успел умереть
    log.info("Waiting 8s before polling to let old instance die...")
    await asyncio.sleep(8)

    log.info("Polling started.")
    polling_task = asyncio.create_task(dp.start_polling(bot))
    stop_task    = asyncio.create_task(stop_event.wait())

    done, pending = await asyncio.wait(
        [polling_task, stop_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()

    await dp.stop_polling()
    await bot.session.close()
    log.info("Bot stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
