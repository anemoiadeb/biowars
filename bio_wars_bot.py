#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════╗
║  ☣️   БИО-ВОЙНЫ  |  Bio-Wars Bot  v1.0   ☣️   ║
║  aiogram 3.x  |  JSON storage              ║
║  Pydroid 3 → polling / Render.com → hook   ║
╚══════════════════════════════════════════════╝

Команды (текстом, без слэша):
  Био-война           — главное меню
  Профиль             — ваш БК
  Заразить @ник       — атака (или реплаем)
  Вылечиться          — антидот
  Лаба                — лаборатория
  Магазин             — прокачка
  Топ заражённых      — рейтинг
  Топ патогенов       — богачи
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

# ─── aiogram 3.x ─────────────────────────────────────────────────────────────
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Filter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
TOKEN       = os.getenv("BOT_TOKEN", "YOUR_TOKEN_HERE")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")   # e.g. https://bio-wars.onrender.com
PORT        = int(os.getenv("PORT", 8080))
DATA_FILE   = Path("bio_wars.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("bio_wars")

# Visual separator used across all formatted messages
SEP = "─" * 26

# Game timers
LAB_CD    = 3600   # 1 hour between lab shifts
INFECT_CD = 1800   # 30 min between infection attempts

# ══════════════════════════════════════════════════════════════════════════════
#  DATA LAYER  (JSON file — Pydroid 3 compatible)
# ══════════════════════════════════════════════════════════════════════════════

def load_db() -> Dict:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"DB load failed: {e}")
    return {}


def save_db(data: Dict) -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"DB save failed: {e}")


def _blank_user(uid: int, uname: str, name: str) -> Dict:
    return {
        "id":              uid,
        "username":        uname,
        "name":            name,
        # — Economy —
        "pathogens":       15,
        "total_earned":    15,
        # — Bio-Container level —
        "level":           1,
        "xp":              0,
        # — Infection state —
        "is_infected":     False,
        "infected_by_id":  None,
        "infected_by_name": None,
        "infected_count":  0,     # victims this user infected
        "cured_count":     0,     # times they self-cured
        # — Lab —
        "lab_level":       1,
        # — Upgrades (each 0-5) —
        "infectivity":     0,
        "lethality":       0,
        "resistance":      0,
        "vaccines":        0,     # 0-3 doses
        # — Cooldown timestamps —
        "last_lab_work":   0,
        "last_infect":     0,
        "last_passive":    time.time(),
    }


def get_or_create(data: Dict, from_user) -> Dict:
    """Accepts Message.from_user or CallbackQuery.from_user."""
    uid = str(from_user.id)
    if uid not in data:
        data[uid] = _blank_user(
            from_user.id,
            from_user.username or "",
            from_user.full_name,
        )
    else:
        if from_user.username:
            data[uid]["username"] = from_user.username
        data[uid]["name"] = from_user.full_name
    return data[uid]


# ══════════════════════════════════════════════════════════════════════════════
#  GAME LOGIC
# ══════════════════════════════════════════════════════════════════════════════

XP_PER_LEVEL = 100   # xp needed = level * XP_PER_LEVEL


def _xp_needed(level: int) -> int:
    return level * XP_PER_LEVEL


def give_xp(u: Dict, amount: int) -> List[str]:
    """Add XP, handle level-ups. Returns list of level-up notification strings."""
    msgs: List[str] = []
    u["xp"] += amount
    while u["xp"] >= _xp_needed(u["level"]):
        u["xp"] -= _xp_needed(u["level"])
        u["level"] += 1
        msgs.append(f"⬆️ <b>Уровень БК: {u['level']}</b>")
    return msgs


def apply_passive(u: Dict) -> int:
    """
    Tick passive pathogen income accumulated since last login.
    Rate: (2 + lab_level * 0.5) per hour, capped at 60 per cycle.
    Returns amount actually added.
    """
    now = time.time()
    elapsed_h = (now - u.get("last_passive", now)) / 3600.0
    if elapsed_h < 0.5:
        return 0
    earned = int(elapsed_h * (2 + u["lab_level"] * 0.5))
    earned = min(earned, 60)
    u["pathogens"]    += earned
    u["total_earned"] += earned
    u["last_passive"]  = now
    return earned


def infection_chance(attacker: Dict, defender: Dict) -> float:
    """
    Base 40% + infectivity bonus − resistance penalty − vaccine penalty.
    Clamped to [5%, 95%].
    """
    chance = 0.40
    chance += attacker["infectivity"] * 0.05
    chance -= defender["resistance"]  * 0.07
    chance -= defender["vaccines"]    * 0.15
    return max(0.05, min(0.95, chance))


def cd_str(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}м {s % 60}с"


def _purchase(u: Dict, key: str, cost_per: int, max_lv: int = 5) -> Tuple[bool, str]:
    lv = u[key]
    if lv >= max_lv:
        return False, "Максимальный уровень"
    cost = (lv + 1) * cost_per
    if u["pathogens"] < cost:
        return False, f"Нужно {cost}🧪 (у вас {u['pathogens']}🧪)"
    u["pathogens"] -= cost
    u[key] += 1
    return True, f"Уровень → {u[key]}"


# ══════════════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════

def kb_main() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🧫 Профиль",         callback_data="profile")
    b.button(text="🔬 Лаборатория",     callback_data="lab")
    b.button(text="💊 Магазин",         callback_data="shop")
    b.button(text="☣️ Топ заражённых",  callback_data="top_inf")
    b.button(text="🏆 Топ патогенов",   callback_data="top_pat")
    b.adjust(2, 1, 2)
    return b.as_markup()


def kb_lab(u: Dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    cd_left = max(0.0, LAB_CD - (time.time() - u["last_lab_work"]))
    if cd_left > 0:
        b.button(text=f"⏳ Работа ({cd_str(cd_left)})", callback_data="lab_noop")
    else:
        b.button(text="⚗️ Работать в лабе",              callback_data="lab_work")

    if u["lab_level"] < 10:
        cost = u["lab_level"] * 80
        b.button(text=f"📈 Улучшить лабу [{cost}🧪]",   callback_data="lab_upgrade")
    else:
        b.button(text="🏆 Лаба MAX",                     callback_data="lab_noop")

    b.button(text="◀️ Назад", callback_data="main_menu")
    b.adjust(1)
    return b.as_markup()


def kb_shop(u: Dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()

    def _btn(emoji: str, label: str, key: str, cost_per: int, max_lv: int = 5):
        lv   = u[key]
        cost = (lv + 1) * cost_per
        if lv < max_lv:
            b.button(
                text=f"{emoji} {label} {lv}/{max_lv} [{cost}🧪]",
                callback_data=f"buy_{key}",
            )
        else:
            b.button(
                text=f"{emoji} {label} MAX ✔",
                callback_data="maxed",
            )

    _btn("🦠", "Заразность",      "infectivity", 40)
    _btn("☠️", "Летальность",     "lethality",   50)
    _btn("🛡️", "Резистентность",  "resistance",  60)

    vac = u["vaccines"]
    if vac < 3:
        b.button(text=f"💉 Вакцина {vac}/3 [30🧪]", callback_data="buy_vaccines")
    else:
        b.button(text="💉 Вакцина MAX ✔",            callback_data="maxed")

    b.button(text="◀️ Назад", callback_data="main_menu")
    b.adjust(1)
    return b.as_markup()


# ══════════════════════════════════════════════════════════════════════════════
#  FORMATTERS
# ══════════════════════════════════════════════════════════════════════════════

def _bar(val: int, max_val: int = 5) -> str:
    return "█" * val + "░" * (max_val - val)


def fmt_profile(u: Dict) -> str:
    status = "☣️ ЗАРАЖЁН" if u["is_infected"] else "✅ Здоров"
    infby  = (
        f"\n   ☠️ Источник: <b>{u['infected_by_name']}</b>"
        if u["is_infected"] and u["infected_by_name"] else ""
    )
    return (
        f"🧫 <b>БИО-КОНТЕЙНЕР</b> · {u['name']}\n{SEP}\n"
        f"📊 Уровень БК: <b>{u['level']}</b>  "
        f"({u['xp']}/{_xp_needed(u['level'])} XP)\n"
        f"🧪 Патогены:   <b>{u['pathogens']}</b>\n"
        f"⚗️  Лаба:       <b>ур.{u['lab_level']}</b>\n"
        f"{SEP}\n"
        f"🦠 Заразность:    [{_bar(u['infectivity'])}] {u['infectivity']}/5\n"
        f"☠️  Летальность:  [{_bar(u['lethality'])}] {u['lethality']}/5\n"
        f"🛡  Резистентн.:  [{_bar(u['resistance'])}] {u['resistance']}/5\n"
        f"💉 Вакцины:    <b>{u['vaccines']}/3</b>\n"
        f"{SEP}\n"
        f"☣️  Заразил:  <b>{u['infected_count']}</b>  ·  "
        f"💊 Вылечился: <b>{u['cured_count']}</b>\n"
        f"📈 Статус: <b>{status}</b>{infby}\n"
    )


def fmt_lab(u: Dict) -> str:
    cd_left = max(0.0, LAB_CD - (time.time() - u["last_lab_work"]))
    cd_txt  = cd_str(cd_left) if cd_left > 0 else "✅ Готово"
    income  = 10 + u["lab_level"] * 5
    passive = 2 + u["lab_level"] * 0.5
    cost_up = u["lab_level"] * 80
    return (
        f"🔬 <b>ЛАБОРАТОРИЯ</b> · ур.{u['lab_level']}\n{SEP}\n"
        f"⏱  Следующая смена:  <b>{cd_txt}</b>\n"
        f"💰 Доход со смены:   ~<b>{income}🧪</b>\n"
        f"📡 Пассивно в час:   ~<b>{passive:.1f}🧪</b>\n"
        f"📈 Улучш. лабы:      <b>{cost_up}🧪</b> → ур.{u['lab_level'] + 1}\n"
        f"{SEP}\n"
        f"💼 Всего заработано: <b>{u['total_earned']}🧪</b>\n"
    )


def fmt_shop_header(u: Dict) -> str:
    return (
        f"💊 <b>МАГАЗИН МУТАЦИЙ</b>\n{SEP}\n"
        f"🦠 <b>Заразность</b>     — +5% к шансу заражения за ур.\n"
        f"☠️ <b>Летальность</b>    — крадёт патогены при атаке\n"
        f"🛡️ <b>Резистентность</b> — -7% к входящему заражению\n"
        f"💉 <b>Вакцина</b>        — -15% к шансу (макс 3 ед.)\n"
        f"{SEP}\n"
        f"💰 Ваш баланс: <b>{u['pathogens']}🧪</b>\n"
    )


def fmt_top_infected() -> str:
    medals = ["🥇", "🥈", "🥉"] + ["🔬"] * 7
    top    = sorted(DB.values(), key=lambda x: x["infected_count"], reverse=True)[:10]
    lines  = [f"☣️ <b>ТОП РАСПРОСТРАНИТЕЛЕЙ ВИРУСА</b>\n{SEP}"]
    for i, ud in enumerate(top):
        status = "☣️" if ud["is_infected"] else "✅"
        lines.append(
            f"{medals[i]} <b>{ud['name']}</b> {status}\n"
            f"   └ {ud['infected_count']} заражений · БК:{ud['level']}"
        )
    return "\n".join(lines) if len(top) > 0 else "Пока нет данных."


def fmt_top_pathogens() -> str:
    medals = ["🥇", "🥈", "🥉"] + ["💰"] * 7
    top    = sorted(DB.values(), key=lambda x: x["pathogens"], reverse=True)[:10]
    lines  = [f"🧪 <b>ТОП ПО ПАТОГЕНАМ</b>\n{SEP}"]
    for i, ud in enumerate(top):
        lines.append(
            f"{medals[i]} <b>{ud['name']}</b>\n"
            f"   └ {ud['pathogens']}🧪 · БК:{ud['level']} · "
            f"заразил:{ud['infected_count']}"
        )
    return "\n".join(lines) if len(top) > 0 else "Пока нет данных."


# ══════════════════════════════════════════════════════════════════════════════
#  CUSTOM FILTER  (prefix match, case-insensitive)
# ══════════════════════════════════════════════════════════════════════════════

class TextStartsWith(Filter):
    """Filter that passes when message text starts with given prefix (lower-case)."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix.lower()

    async def __call__(self, message: Message) -> bool:
        return (
            message.text is not None
            and message.text.lower().startswith(self.prefix)
        )


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTER & GLOBAL DB
# ══════════════════════════════════════════════════════════════════════════════

router = Router()
DB: Dict = {}   # Loaded from JSON at startup; every write syncs back.


# ─── /start + help ────────────────────────────────────────────────────────────

HELP_TEXT = (
    f"☣️ <b>БИО-ВОЙНЫ</b> — добро пожаловать!\n{SEP}\n"
    "Напиши любую команду текстом:\n\n"
    "• <b>Био-война</b> — главное меню\n"
    "• <b>Профиль</b> — ваш БК-профиль\n"
    "• <b>Заразить @ник</b> — атаковать игрока\n"
    "  (или ответом на сообщение жертвы)\n"
    "• <b>Вылечиться</b> — антидот (30🧪)\n"
    "• <b>Лаба</b> — лаборатория\n"
    "• <b>Магазин</b> — прокачка мутаций\n"
    "• <b>Топ заражённых</b> — рейтинг атакующих\n"
    "• <b>Топ патогенов</b> — рейтинг богатых\n\n"
    f"{SEP}\n"
    "Каждый новый игрок получает <b>15🧪</b>.\n"
    "Патогены накапливаются пассивно, пока вы не в сети."
)


@router.message(CommandStart())
@router.message(F.text.lower().in_({"помощь", "команды", "help", "хелп"}))
async def cmd_help(msg: Message) -> None:
    get_or_create(DB, msg.from_user)
    save_db(DB)
    await msg.answer(HELP_TEXT)


# ─── Main menu ────────────────────────────────────────────────────────────────

@router.message(
    F.text.lower().in_({"био-война", "биовойна", "биовойны", "bio war", "bio wars"})
)
async def cmd_main_menu(msg: Message) -> None:
    u      = get_or_create(DB, msg.from_user)
    earned = apply_passive(u)
    save_db(DB)
    text   = "☣️ <b>БИО-ВОЙНЫ</b>\nМир патогенов ждёт. Выбери действие:"
    if earned:
        text += f"\n\n<i>💡 Пассивный доход: +{earned}🧪</i>"
    await msg.answer(text, reply_markup=kb_main())


# ─── Profile ──────────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"профиль", "мой профиль", "биокон", "бк", "стат"}))
async def cmd_profile(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    apply_passive(u)
    save_db(DB)
    await msg.answer(fmt_profile(u))


# ─── Lab ──────────────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"лаба", "моя лаба", "лаборатория", "лаб"}))
async def cmd_lab(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    apply_passive(u)
    save_db(DB)
    await msg.answer(fmt_lab(u), reply_markup=kb_lab(u))


# ─── Shop ─────────────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"магазин", "шоп", "улучшения", "апгрейд", "апгрейды"}))
async def cmd_shop(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    apply_passive(u)
    save_db(DB)
    await msg.answer(fmt_shop_header(u), reply_markup=kb_shop(u))


# ─── Infect ───────────────────────────────────────────────────────────────────

@router.message(TextStartsWith("заразить"))
async def cmd_infect(msg: Message) -> None:
    attacker = get_or_create(DB, msg.from_user)
    apply_passive(attacker)

    # ── Locate target ─────────────────────────────────────────────────────────
    target: Optional[Dict] = None

    if msg.reply_to_message and msg.reply_to_message.from_user:
        tfu = msg.reply_to_message.from_user
        if not tfu.is_bot:
            target = get_or_create(DB, tfu)
    else:
        m = re.search(r"@(\w+)", msg.text or "", re.IGNORECASE)
        if m:
            search_name = m.group(1).lower()
            for ud in DB.values():
                if ud.get("username", "").lower() == search_name:
                    target = ud
                    break

    if target is None:
        await msg.answer(
            "❌ Укажи цель:\n"
            "• <b>Заразить @ник</b>\n"
            "• Или ответь реплаем на сообщение жертвы"
        )
        return

    if target["id"] == msg.from_user.id:
        await msg.answer("🤡 Самозаражение невозможно. Даже для тебя.")
        return

    if target["is_infected"]:
        await msg.answer(
            f"☣️ <b>{target['name']}</b> уже несёт вирус.\n"
            "Трать штамм на здоровых."
        )
        return

    # ── Cooldown ──────────────────────────────────────────────────────────────
    elapsed = time.time() - attacker["last_infect"]
    if elapsed < INFECT_CD:
        left = INFECT_CD - elapsed
        await msg.answer(
            f"⏳ Штамм созревает ещё <b>{cd_str(left)}</b>."
        )
        return

    attacker["last_infect"] = time.time()

    # ── Roll ──────────────────────────────────────────────────────────────────
    chance = infection_chance(attacker, target)
    roll   = random.random()
    lvl_msgs: List[str] = []

    if roll <= chance:
        # SUCCESS
        target["is_infected"]       = True
        target["infected_by_id"]    = attacker["id"]
        target["infected_by_name"]  = attacker["name"]
        attacker["infected_count"] += 1

        # Lethality steals pathogens
        stolen = 0
        if attacker["lethality"] > 0:
            steal_pct = attacker["lethality"] * 0.06
            stolen    = int(target["pathogens"] * steal_pct)
            stolen    = min(stolen, target["pathogens"])
            target["pathogens"]        -= stolen
            attacker["pathogens"]      += stolen
            attacker["total_earned"]   += stolen

        lvl_msgs = give_xp(attacker, 20)
        save_db(DB)

        reply = (
            f"☣️ <b>ЗАРАЖЕНИЕ УСПЕШНО!</b>\n"
            f"Жертва: <b>{target['name']}</b>\n"
            f"Шанс:   <b>{int(chance * 100)}%</b>"
        )
        if stolen:
            reply += f"\n💀 Похищено: <b>{stolen}🧪</b>"
        if lvl_msgs:
            reply += "\n" + "\n".join(lvl_msgs)

    else:
        # FAILURE
        save_db(DB)
        reply = (
            f"💨 <b>ШТАММ НЕ ПРИЖИЛСЯ</b>\n"
            f"<b>{target['name']}</b> устоял.\n"
            f"Шанс был: {int(chance * 100)}%"
        )

    await msg.answer(reply)


# ─── Cure self ────────────────────────────────────────────────────────────────

@router.message(F.text.lower().in_({"вылечиться", "антидот", "вылечить себя", "лечение"}))
async def cmd_cure(msg: Message) -> None:
    u = get_or_create(DB, msg.from_user)
    apply_passive(u)

    if not u["is_infected"]:
        await msg.answer("✅ Вы здоровы. Вирус не обнаружен.")
        return

    cost = 30
    if u["pathogens"] < cost:
        await msg.answer(
            f"❌ Для антидота нужно <b>{cost}🧪</b>.\n"
            f"У вас: <b>{u['pathogens']}🧪</b>.\n"
            "Работайте в лабе!"
        )
        return

    u["pathogens"]        -= cost
    u["is_infected"]       = False
    u["infected_by_id"]    = None
    u["infected_by_name"]  = None
    u["cured_count"]      += 1
    lvl_msgs = give_xp(u, 10)
    save_db(DB)

    reply = (
        f"💊 <b>АНТИДОТ ВВЕДЁН</b>\n"
        f"Потрачено: <b>{cost}🧪</b>\n"
        "Вы здоровы. Берегитесь рецидива..."
    )
    if lvl_msgs:
        reply += "\n" + "\n".join(lvl_msgs)
    await msg.answer(reply)


# ─── Top lists (text triggers) ────────────────────────────────────────────────

@router.message(F.text.lower().in_({"топ заражённых", "топ зараженных", "топ", "рейтинг"}))
async def cmd_top_infected_txt(msg: Message) -> None:
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад в меню", callback_data="main_menu")
    await msg.answer(fmt_top_infected(), reply_markup=b.as_markup())


@router.message(F.text.lower().in_({"топ патогенов", "богачи", "патогены"}))
async def cmd_top_pathogens_txt(msg: Message) -> None:
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад в меню", callback_data="main_menu")
    await msg.answer(fmt_top_pathogens(), reply_markup=b.as_markup())


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK QUERY HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cq: CallbackQuery) -> None:
    await cq.message.edit_text(
        "☣️ <b>БИО-ВОЙНЫ</b>\nМир патогенов ждёт. Выбери действие:",
        reply_markup=kb_main(),
    )
    await cq.answer()


@router.callback_query(F.data == "profile")
async def cb_profile(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    apply_passive(u)
    save_db(DB)
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="main_menu")
    await cq.message.edit_text(fmt_profile(u), reply_markup=b.as_markup())
    await cq.answer()


# ── Lab callbacks ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "lab")
async def cb_lab(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    apply_passive(u)
    save_db(DB)
    await cq.message.edit_text(fmt_lab(u), reply_markup=kb_lab(u))
    await cq.answer()


@router.callback_query(F.data == "lab_work")
async def cb_lab_work(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    apply_passive(u)

    cd_left = LAB_CD - (time.time() - u["last_lab_work"])
    if cd_left > 0:
        await cq.answer(f"⏳ Ещё {cd_str(cd_left)}", show_alert=True)
        return

    # Calculate shift reward: base + lab bonus + small random
    earned  = 10 + u["lab_level"] * 5 + random.randint(0, u["lab_level"] * 3)
    u["pathogens"]     += earned
    u["total_earned"]  += earned
    u["last_lab_work"]  = time.time()
    lvl_msgs = give_xp(u, 15)
    save_db(DB)

    text = (
        f"⚗️ <b>СМЕНА ЗАВЕРШЕНА</b>\n"
        f"Синтезировано: <b>+{earned}🧪</b>\n"
        "Следующая смена — через <b>1 час</b>."
    )
    if lvl_msgs:
        text += "\n" + "\n".join(lvl_msgs)

    await cq.message.edit_text(text, reply_markup=kb_lab(u))
    await cq.answer(f"+{earned}🧪")


@router.callback_query(F.data == "lab_upgrade")
async def cb_lab_upgrade(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    apply_passive(u)

    if u["lab_level"] >= 10:
        await cq.answer("🏆 Лаба уже на максимуме!", show_alert=True)
        return

    cost = u["lab_level"] * 80
    if u["pathogens"] < cost:
        await cq.answer(
            f"❌ Нужно {cost}🧪 (у вас {u['pathogens']}🧪)", show_alert=True
        )
        return

    u["pathogens"]  -= cost
    u["lab_level"]  += 1
    save_db(DB)

    await cq.message.edit_text(
        f"🔬 <b>ЛАБА УЛУЧШЕНА</b> → ур.<b>{u['lab_level']}</b>\n\n"
        + fmt_lab(u),
        reply_markup=kb_lab(u),
    )
    await cq.answer(f"Лаба ур.{u['lab_level']}!")


@router.callback_query(F.data == "lab_noop")
async def cb_lab_noop(cq: CallbackQuery) -> None:
    u      = get_or_create(DB, cq.from_user)
    cd_left = max(0.0, LAB_CD - (time.time() - u["last_lab_work"]))
    if cd_left > 0:
        await cq.answer(f"⏳ Следующая смена через {cd_str(cd_left)}", show_alert=True)
    else:
        await cq.answer("✅ Можно работать!", show_alert=True)


# ── Shop callbacks ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "shop")
async def cb_shop(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    apply_passive(u)
    save_db(DB)
    await cq.message.edit_text(fmt_shop_header(u), reply_markup=kb_shop(u))
    await cq.answer()


@router.callback_query(F.data == "buy_infectivity")
async def cb_buy_infectivity(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    ok, msg_txt = _purchase(u, "infectivity", 40)
    if ok:
        save_db(DB)
        await cq.message.edit_text(fmt_shop_header(u), reply_markup=kb_shop(u))
        await cq.answer(f"🦠 Заразность ур.{u['infectivity']}!")
    else:
        await cq.answer(f"❌ {msg_txt}", show_alert=True)


@router.callback_query(F.data == "buy_lethality")
async def cb_buy_lethality(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    ok, msg_txt = _purchase(u, "lethality", 50)
    if ok:
        save_db(DB)
        await cq.message.edit_text(fmt_shop_header(u), reply_markup=kb_shop(u))
        await cq.answer(f"☠️ Летальность ур.{u['lethality']}!")
    else:
        await cq.answer(f"❌ {msg_txt}", show_alert=True)


@router.callback_query(F.data == "buy_resistance")
async def cb_buy_resistance(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    ok, msg_txt = _purchase(u, "resistance", 60)
    if ok:
        save_db(DB)
        await cq.message.edit_text(fmt_shop_header(u), reply_markup=kb_shop(u))
        await cq.answer(f"🛡️ Резистентность ур.{u['resistance']}!")
    else:
        await cq.answer(f"❌ {msg_txt}", show_alert=True)


@router.callback_query(F.data == "buy_vaccines")
async def cb_buy_vaccines(cq: CallbackQuery) -> None:
    u = get_or_create(DB, cq.from_user)
    if u["vaccines"] >= 3:
        await cq.answer("Уже максимум вакцин!", show_alert=True)
        return
    if u["pathogens"] < 30:
        await cq.answer(
            f"❌ Нужно 30🧪 (у вас {u['pathogens']}🧪)", show_alert=True
        )
        return
    u["pathogens"]  -= 30
    u["vaccines"]   += 1
    save_db(DB)
    await cq.message.edit_text(fmt_shop_header(u), reply_markup=kb_shop(u))
    await cq.answer(f"💉 Вакцина куплена ({u['vaccines']}/3)!")


@router.callback_query(F.data == "maxed")
async def cb_maxed(cq: CallbackQuery) -> None:
    await cq.answer("🏆 Уже максимальный уровень!", show_alert=True)


# ── Top callbacks ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "top_inf")
async def cb_top_inf(cq: CallbackQuery) -> None:
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="main_menu")
    await cq.message.edit_text(fmt_top_infected(), reply_markup=b.as_markup())
    await cq.answer()


@router.callback_query(F.data == "top_pat")
async def cb_top_pat(cq: CallbackQuery) -> None:
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="main_menu")
    await cq.message.edit_text(fmt_top_pathogens(), reply_markup=b.as_markup())
    await cq.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    global DB
    DB = load_db()
    log.info(f"Loaded {len(DB)} user record(s).")

    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    if WEBHOOK_URL:
        # ── Render.com / production  (webhook mode) ───────────────────────────
        from aiogram.webhook.aiohttp_server import (
            SimpleRequestHandler,
            setup_application,
        )
        from aiohttp import web

        webhook_path = f"/webhook/{TOKEN}"
        full_url     = f"{WEBHOOK_URL.rstrip('/')}{webhook_path}"

        await bot.set_webhook(full_url)
        log.info(f"Webhook registered: {full_url}")

        app = web.Application()
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=webhook_path)
        setup_application(app, dp, bot=bot)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        log.info(f"HTTP server on 0.0.0.0:{PORT}")

        try:
            await asyncio.Event().wait()   # keep running forever
        finally:
            await runner.cleanup()
            await bot.session.close()

    else:
        # ── Pydroid 3 / local  (long-polling mode) ───────────────────────────
        log.info("Polling mode — Pydroid 3 / local")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
