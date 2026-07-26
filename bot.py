import os
import asyncio
import aiohttp
import json
import numpy as np
from datetime import datetime, time
from aiohttp import web

# ========== CONFIG ==========
TELEGRAM_TOKEN = "8608954811:AAGJU_4pEJGZC6rNR5-Qq7JFrCik6PAPjVg"
PORT = int(os.environ.get("PORT", 8080))
CHECK_INTERVAL = 300  # 5 минут
DB_FILE = "users.json"

# === СПЯЩИЙ РЕЖИМ (23:00 — 09:00) ===
SLEEP_START = 23  # 23:00
SLEEP_END = 9     # 09:00

PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "TRXUSDT", "AVAXUSDT", "SHIBUSDT",
    "DOTUSDT", "LINKUSDT", "MATICUSDT", "LTCUSDT", "BCHUSDT",
    "NEARUSDT", "ATOMUSDT", "UNIUSDT", "ETCUSDT", "XLMUSDT",
    "ARBUSDT", "APTUSDT", "OPUSDT", "INJUSDT", "SUIUSDT",
    "SEIUSDT", "RNDRUSDT", "ICPUSDT", "FILUSDT", "VETUSDT",
]

CURRENT_MODE = "Strong"
DEFAULT_MIN_PROBABILITY = 70

PAIRS_STRONG = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
PAIRS_MEME = ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT"]
PAIRS_ALL = PAIRS

active_users = set()
offset = 0
user_thresholds = {}
user_pairs = {}
user_notifications = {}
waiting_for_threshold = {}
temp_pairs = {}
first_scan_done = False
last_scan_time = 0
last_no_signal_msg = {}
daily_report_sent = {}  # chat_id -> last_report_date

# ========== LOAD/SAVE ==========
def load_data():
    global active_users, user_thresholds, user_pairs, user_notifications
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            active_users = set(data.get("users", []))
            user_thresholds = {int(k): v for k, v in data.get("thresholds", {}).items()}
            user_pairs = {int(k): v for k, v in data.get("user_pairs", {}).items()}
            user_notifications = {int(k): v for k, v in data.get("user_notifications", {}).items()}
    except:
        active_users = set()
        user_thresholds = {}
        user_pairs = {}
        user_notifications = {}

def save_data():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "users": list(active_users),
                "thresholds": {str(k): v for k, v in user_thresholds.items()},
                "user_pairs": {str(k): v for k, v in user_pairs.items()},
                "user_notifications": {str(k): v for k, v in user_notifications.items()}
            }, f)
    except:
        pass

def get_pairs_for_mode(mode):
    if mode == "Strong":
        return PAIRS_STRONG
    elif mode == "Meme":
        return PAIRS_MEME
    else:
        return PAIRS_ALL

def get_pairs_for_user(chat_id):
    if chat_id in user_pairs and user_pairs[chat_id]:
        return user_pairs[chat_id]
    return get_pairs_for_mode(CURRENT_MODE)

def is_notifications_enabled(chat_id):
    return user_notifications.get(chat_id, True)

def is_sleep_time():
    now = datetime.now().hour
    if SLEEP_START < SLEEP_END:
        return SLEEP_START <= now < SLEEP_END
    else:
        return now >= SLEEP_START or now < SLEEP_END

# ========== SEND ==========
async def send_tg(chat_id, text, reply_markup=None):
    if not is_notifications_enabled(chat_id):
        return
    if is_sleep_time():
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json=payload, timeout=10)
        except:
            pass

async def send_signal(chat_id, text):
    if not is_notifications_enabled(chat_id):
        return
    if is_sleep_time():
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json=payload, timeout=10)
        except:
            pass

async def send_forced(chat_id, text, reply_markup=None):
    """Отправка без проверки ON/OFF (для меню)"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json=payload, timeout=10)
        except:
            pass

# ========== KEYBOARDS ==========
def get_main_menu_keyboard(mode, threshold, notifications_on=True):
    if notifications_on:
        status_text = "OFF"
        status_emoji = "🔕"
    else:
        status_text = "ON"
        status_emoji = "🔔"

    buttons = [
        [
            {"text": "Strong", "callback_data": "mode_Strong"},
            {"text": "Meme", "callback_data": "mode_Meme"},
            {"text": "All", "callback_data": "mode_All"}
        ],
        [
            {"text": f"Port: {threshold}%", "callback_data": "set_threshold"},
            {"text": "Info", "callback_data": "info"},
            {"text": f"{status_emoji} {status_text}", "callback_data": "toggle_notifications"}
        ],
        [{"text": "Select pairs", "callback_data": "show_pairs"}]
    ]

    for row in buttons:
        for btn in row:
            btn_text = btn["text"]
            if btn_text == "Strong" and mode == "Strong":
                btn["text"] = "🔸 Strong"
            elif btn_text == "Meme" and mode == "Meme":
                btn["text"] = "🔸 Meme"
            elif btn_text == "All" and mode == "All":
                btn["text"] = "🔸 All"

    return {"inline_keyboard": buttons}

def get_back_menu_keyboard():
    return {"inline_keyboard": [[{"text": "Back to menu", "callback_data": "back_main"}]]}

def get_pair_selection_keyboard(chat_id):
    current_pairs = temp_pairs.get(chat_id, user_pairs.get(chat_id, []))
    buttons = []

    all_pairs = get_pairs_for_mode(CURRENT_MODE)
    for pair in all_pairs:
        is_selected = pair in current_pairs
        text = f"🔸 {pair}" if is_selected else pair
        buttons.append([{"text": text, "callback_data": f"toggle_{pair}"}])

    buttons.append([{"text": "SAVE SELECTION", "callback_data": "save_pairs"}])
    buttons.append([{"text": "EXIT", "callback_data": "exit_pairs"}])
    return {"inline_keyboard": buttons}

def get_threshold_input_keyboard():
    return {"inline_keyboard": [[{"text": "Back to menu", "callback_data": "back_main"}]]}

# ========== CALLBACKS ==========
async def handle_callback(callback_query):
    global CURRENT_MODE
    data = callback_query.get("data")
    chat_id = callback_query["from"]["id"]

    if data == "back_main":
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_forced(chat_id, "Main menu:", kb)

    elif data == "exit_pairs":
        if chat_id in temp_pairs:
            del temp_pairs[chat_id]
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_forced(chat_id, "Main menu:", kb)

    elif data == "save_pairs":
        if chat_id in temp_pairs:
            user_pairs[chat_id] = temp_pairs[chat_id].copy()
            del temp_pairs[chat_id]
        else:
            user_pairs[chat_id] = get_pairs_for_mode(CURRENT_MODE).copy()
        save_data()
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_forced(chat_id, "Main menu:", kb)

    elif data == "toggle_notifications":
        new_status = not is_notifications_enabled(chat_id)
        user_notifications[chat_id] = new_status
        save_data()
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, new_status)
        await send_forced(chat_id, "Main menu:", kb)

    elif data == "show_pairs":
        temp_pairs[chat_id] = user_pairs.get(chat_id, get_pairs_for_mode(CURRENT_MODE)).copy()
        pairs = temp_pairs[chat_id]
        text = f"Current selection ({len(pairs)}):\n\n" + "\n".join([f"{i+1}. {p}" for i, p in enumerate(pairs)]) + "\n\nClick to toggle, then press SAVE."
        kb = get_pair_selection_keyboard(chat_id)
        await send_forced(chat_id, text, kb)

    elif data.startswith("toggle_"):
        pair = data.replace("toggle_", "")
        if chat_id not in temp_pairs:
            temp_pairs[chat_id] = user_pairs.get(chat_id, get_pairs_for_mode(CURRENT_MODE)).copy()
        if pair in temp_pairs[chat_id]:
            temp_pairs[chat_id].remove(pair)
        else:
            temp_pairs[chat_id].append(pair)
        pairs = temp_pairs[chat_id]
        text = f"Current selection ({len(pairs)}):\n\n" + "\n".join([f"{i+1}. {p}" for i, p in enumerate(pairs)]) + "\n\nClick to toggle, then press SAVE."
        kb = get_pair_selection_keyboard(chat_id)
        await send_forced(chat_id, text, kb)

    elif data.startswith("mode_"):
        mode = data.split("_")[1]
        CURRENT_MODE = mode
        save_data()
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_forced(chat_id, "Main menu:", kb)

    elif data == "set_threshold":
        waiting_for_threshold[chat_id] = True
        kb = get_threshold_input_keyboard()
        await send_forced(chat_id, "Enter number (10-99) for minimum probability threshold:", kb)

    elif data == "info":
        info = ("Trading Bot\n\n"
                "Timeframes: 15m + 1h\n\n"
                "Port (threshold):\n"
                "Minimum probability for signals.\n"
                "Higher value = higher quality signals.\n"
                "Range: 10-99%.\n\n"
                "⚠️ This bot is for informational purposes only.\n"
                "Not financial advice.")
        kb = get_back_menu_keyboard()
        await send_forced(chat_id, info, kb)

# ========== TEXT HANDLER ==========
async def handle_text(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if chat_id in waiting_for_threshold:
        try:
            val = int(text)
            if 10 <= val <= 99:
                user_thresholds[chat_id] = val
                waiting_for_threshold.pop(chat_id, None)
                save_data()
                kb = get_main_menu_keyboard(CURRENT_MODE, val, is_notifications_enabled(chat_id))
                await send_forced(chat_id, f"Threshold set to {val}%", kb)
            else:
                await send_forced(chat_id, "Enter value between 10 and 99.", get_threshold_input_keyboard())
        except:
            await send_forced(chat_id, "Please enter a number.", get_threshold_input_keyboard())
        return

    if text == "/start":
        active_users.add(chat_id)
        if chat_id not in user_notifications:
            user_notifications[chat_id] = True
        save_data()
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_forced(chat_id, "Welcome! Use the menu below:", kb)

# ========== INDIKATORS ==========
def calculate_atr(high, low, close, period=14):
    if len(close) < period + 1:
        return 1.0
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    return float(np.mean(tr[-period:]))

def calculate_rsi(close, period=14):
    if len(close) < period + 1:
        return 50
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = np.mean(gain[:period])
    avg_loss = np.mean(loss[:period])
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return float(rsi)

def get_trend(close):
    if len(close) < 50:
        return "FLAT"
    ma50 = np.mean(close[-50:])
    ma20 = np.mean(close[-20:])
    if ma20 > ma50 * 1.01:
        return "UP"
    elif ma20 < ma50 * 0.99:
        return "DOWN"
    return "FLAT"

def check_volume_confirmation(volume, threshold=1.3):
    if len(volume) < 20:
        return False
    avg_vol = np.mean(volume[-20:])
    return volume[-1] > avg_vol * threshold

def calculate_adx(high, low, close, period=14):
    """ADX для определения силы тренда"""
    if len(close) < period + 1:
        return 25
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    plus_dm = np.maximum(high[1:] - high[:-1], 0)
    minus_dm = np.maximum(low[:-1] - low[1:], 0)
    plus_dm = np.where(plus_dm > minus_dm, plus_dm, 0)
    minus_dm = np.where(minus_dm > plus_dm, minus_dm, 0)
    atr = np.mean(tr[-period:])
    if atr == 0:
        return 25
    plus_di = 100 * np.mean(plus_dm[-period:]) / atr
    minus_di = 100 * np.mean(minus_dm[-period:]) / atr
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    return float(np.mean(dx))

def calculate_macd(close):
    """Быстрая проверка MACD"""
    if len(close) < 26:
        return "NEUTRAL"
    ema12 = np.mean(close[-12:])
    ema26 = np.mean(close[-26:])
    macd = ema12 - ema26
    signal = np.mean(close[-9:])
    if macd > signal:
        return "BULLISH"
    elif macd < signal:
        return "BEARISH"
    return "NEUTRAL"

# ========== DAILY REPORT ==========
async def send_daily_report():
    """Отправка ежедневной сводки в 11:00"""
    while True:
        now = datetime.now()
        target_time = datetime(now.year, now.month, now.day, 11, 0, 0)
        if now >= target_time:
            target_time = target_time + timedelta(days=1)
        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        
        if not active_users:
            continue
        
        # Собираем данные
        btc_data = await fetch_candles("BTCUSDT")
        if not btc_data:
            continue
        high, low, close, volume = btc_data
        price = float(close[-1])
        change_24h = (close[-1] - close[-24]) / close[-24] * 100 if len(close) >= 24 else 0
        rsi = calculate_rsi(close)
        trend = get_trend(close)
        adx = calculate_adx(high, low, close)
        macd = calculate_macd(close)
        
        # Общий объём по топ-5
        total_vol = 0
        for symbol in ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]:
            data = await fetch_candles(symbol)
            if data:
                total_vol += data[3][-1]
        
        # Формируем сообщение
        msg = (
            f"📊 <b>Daily Market Report</b>\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y')}\n\n"
            f"🔹 <b>BTC:</b> ${price:,.2f} ({change_24h:+.2f}%)\n"
            f"🔹 <b>RSI:</b> {rsi:.1f} "
            f"{'🔴 Overbought' if rsi > 70 else '🟢 Oversold' if rsi < 30 else '🟡 Neutral'}\n"
            f"🔹 <b>Trend:</b> {trend}\n"
            f"🔹 <b>ADX (Trend Strength):</b> {adx:.1f} "
            f"{'🔥 Strong' if adx > 40 else '🟡 Moderate' if adx > 25 else '💤 Weak'}\n"
            f"🔹 <b>MACD:</b> {macd}\n"
            f"🔹 <b>Total Volume (Top-5):</b> ${total_vol/1000:.1f}K\n\n"
            f"📌 <i>Higher ADX = stronger trend\n"
            f"RSI > 70 = overbought\n"
            f"RSI < 30 = oversold</i>"
        )
        
        for user_id in list(active_users):
            await send_signal(user_id, msg)
        await asyncio.sleep(60)

# ========== FETCH CANDLES ==========
async def fetch_candles(symbol):
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": symbol, "interval": "15", "limit": 60}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=10) as resp:
                data = await resp.json()
                if data.get("retCode") != 0:
                    return None
                rows = data["result"]["list"]
                high = np.array([float(r[2]) for r in rows])
                low = np.array([float(r[3]) for r in rows])
                close = np.array([float(r[4]) for r in rows])
                volume = np.array([float(r[5]) for r in rows])
                return high, low, close, volume
        except:
            return None

# ========== PATTERN DETECTION ==========
def detect_setups(high, low, close, volume):
    if len(close) < 30:
        return []
    
    signals = []
    price = float(close[-1])
    atr = calculate_atr(high, low, close)
    rsi = calculate_rsi(close)
    trend = get_trend(close)
    vol_confirm = check_volume_confirmation(volume)
    adx = calculate_adx(high, low, close)
    
    # === 1. TRIANGLE ===
    range1 = np.max(high[-30:]) - np.min(low[-30:])
    range2 = np.max(high[-10:]) - np.min(low[-10:])
    
    if range2 / range1 < 0.4 and range1 > 0:
        height = range1 * 0.3
        if price > np.max(high[-5:]) and trend != "DOWN" and rsi < 70 and vol_confirm:
            signals.append({
                "type": "Triangle",
                "side": "LONG",
                "entry": round(price, 2),
                "sl": round(price - atr * 2, 2),
                "tp": round(price + height, 2),
                "prob": min(95, 75 + (5 if rsi < 40 else 0) + (5 if vol_confirm else 0) + (5 if adx > 30 else 0))
            })
        elif price < np.min(low[-5:]) and trend != "UP" and rsi > 30 and vol_confirm:
            signals.append({
                "type": "Triangle",
                "side": "SHORT",
                "entry": round(price, 2),
                "sl": round(price + atr * 2, 2),
                "tp": round(price - height, 2),
                "prob": min(95, 75 + (5 if rsi > 60 else 0) + (5 if vol_confirm else 0) + (5 if adx > 30 else 0))
            })
    
    # === 2. HEAD & SHOULDERS ===
    peaks = np.where((high[1:-1] > high[:-2]) & (high[1:-1] > high[2:]))[0] + 1
    if len(peaks) >= 3 and trend != "UP":
        p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
        if high[p2] > high[p1] and high[p2] > high[p3] and abs(high[p1] - high[p3]) < high[p2] * 0.03:
            neck = min(np.min(low[p1:p2]), np.min(low[p2:p3]))
            if price < neck * 0.995 and rsi > 30 and vol_confirm:
                height = high[p2] - neck
                signals.append({
                    "type": "Head & Shoulders",
                    "side": "SHORT",
                    "entry": round(price, 2),
                    "sl": round(price + atr * 2, 2),
                    "tp": round(price - height, 2),
                    "prob": min(95, 80 + (5 if rsi > 60 else 0) + (5 if vol_confirm else 0) + (5 if adx > 30 else 0))
                })
    
    # === 3. INVERSE H&S ===
    troughs = np.where((low[1:-1] < low[:-2]) & (low[1:-1] < low[2:]))[0] + 1
    if len(troughs) >= 3 and trend != "DOWN":
        t1, t2, t3 = troughs[-3], troughs[-2], troughs[-1]
        if low[t2] < low[t1] and low[t2] < low[t3] and abs(low[t1] - low[t3]) < low[t2] * 0.03:
            neck = max(np.max(high[t1:t2]), np.max(high[t2:t3]))
            if price > neck * 1.005 and rsi < 70 and vol_confirm:
                height = neck - low[t2]
                signals.append({
                    "type": "Inverse H&S",
                    "side": "LONG",
                    "entry": round(price, 2),
                    "sl": round(price - atr * 2, 2),
                    "tp": round(price + height, 2),
                    "prob": min(95, 80 + (5 if rsi < 40 else 0) + (5 if vol_confirm else 0) + (5 if adx > 30 else 0))
                })
    
    # === 4. DOUBLE TOP ===
    if len(peaks) >= 2 and trend != "UP":
        p1, p2 = peaks[-2], peaks[-1]
        if abs(high[p1] - high[p2]) < high[p1] * 0.015:
            neck = min(low[p1:p2])
            if price < neck * 0.995 and rsi > 30 and vol_confirm:
                height = high[p1] - neck
                signals.append({
                    "type": "Double Top",
                    "side": "SHORT",
                    "entry": round(price, 2),
                    "sl": round(price + atr * 2, 2),
                    "tp": round(price - height, 2),
                    "prob": min(95, 78 + (5 if rsi > 60 else 0) + (5 if vol_confirm else 0) + (5 if adx > 30 else 0))
                })
    
    # === 5. DOUBLE BOTTOM ===
    if len(troughs) >= 2 and trend != "DOWN":
        t1, t2 = troughs[-2], troughs[-1]
        if abs(low[t1] - low[t2]) < low[t1] * 0.015:
            neck = max(high[t1:t2])
            if price > neck * 1.005 and rsi < 70 and vol_confirm:
                height = neck - low[t1]
                signals.append({
                    "type": "Double Bottom",
                    "side": "LONG",
                    "entry": round(price, 2),
                    "sl": round(price - atr * 2, 2),
                    "tp": round(price + height, 2),
                    "prob": min(95, 78 + (5 if rsi < 40 else 0) + (5 if vol_confirm else 0) + (5 if adx > 30 else 0))
                })
    
    # === 6. FLAG ===
    if len(close) >= 20:
        impulse_up = (close[-10] - close[-20]) / close[-20] > 0.03
        impulse_dn = (close[-10] - close[-20]) / close[-20] < -0.03
        if (impulse_up or impulse_dn) and vol_confirm:
            corr_range = np.max(high[-10:]) - np.min(low[-10:])
            full_range = np.max(high[-20:]) - np.min(low[-20:])
            if full_range > 0 and corr_range / full_range < 0.4:
                if impulse_up and price > np.max(high[-10:]) * 1.002 and trend != "DOWN" and rsi < 70:
                    signals.append({
                        "type": "Flag",
                        "side": "LONG",
                        "entry": round(price, 2),
                        "sl": round(price - atr * 2, 2),
                        "tp": round(price + full_range * 0.6, 2),
                        "prob": min(95, 70 + (5 if rsi < 40 else 0) + (5 if vol_confirm else 0) + (5 if adx > 30 else 0))
                    })
                elif impulse_dn and price < np.min(low[-10:]) * 0.998 and trend != "UP" and rsi > 30:
                    signals.append({
                        "type": "Flag",
                        "side": "SHORT",
                        "entry": round(price, 2),
                        "sl": round(price + atr * 2, 2),
                        "tp": round(price - full_range * 0.6, 2),
                        "prob": min(95, 70 + (5 if rsi > 60 else 0) + (5 if vol_confirm else 0) + (5 if adx > 30 else 0))
                    })
    
    return signals

# ========== SCAN ==========
async def scan_signals():
    global first_scan_done, last_scan_time
    
    while True:
        try:
            now = datetime.now().timestamp()
            
            if not first_scan_done:
                print("⏳ Waiting 5 minutes before first scan...")
                await asyncio.sleep(CHECK_INTERVAL)
                first_scan_done = True
                continue
            
            if now - last_scan_time < CHECK_INTERVAL:
                await asyncio.sleep(5)
                continue
            
            last_scan_time = now
            
            if not active_users:
                await asyncio.sleep(30)
                continue
            
            for user_id in list(active_users):
                if is_notifications_enabled(user_id) and not is_sleep_time():
                    await send_signal(user_id, "🔍 Scanning market...")
                await asyncio.sleep(0.2)
            
            found_any = False
            all_signals = []
            
            for symbol in PAIRS:
                data = await fetch_candles(symbol)
                if not data:
                    continue
                high, low, close, volume = data
                signals = detect_setups(high, low, close, volume)
                if signals:
                    found_any = True
                    for signal in signals:
                        signal["symbol"] = symbol
                        all_signals.append(signal)
                await asyncio.sleep(0.3)
            
            for user_id in list(active_users):
                if not is_notifications_enabled(user_id):
                    continue
                if is_sleep_time():
                    continue
                if found_any:
                    for signal in all_signals:
                        msg = (f"🚨 SIGNAL: {signal['symbol']}\n"
                               f"📊 Type: {signal['type']}\n"
                               f"📈 Side: {signal['side']}\n"
                               f"💰 Entry: {signal['entry']}\n"
                               f"🛑 SL: {signal['sl']}\n"
                               f"🎯 TP: {signal['tp']}\n"
                               f"⚡ Probability: {signal['prob']}%")
                        await send_signal(user_id, msg)
                        await asyncio.sleep(0.3)
                    last_no_signal_msg[user_id] = 0
                else:
                    last_time = last_no_signal_msg.get(user_id, 0)
                    if now - last_time >= 60:
                        await send_signal(user_id, "😴 No deals for 5 minutes. Scanning...")
                        last_no_signal_msg[user_id] = now
            
        except Exception as e:
            print(f"Scan error: {e}")
            await asyncio.sleep(30)

# ========== POLLING ==========
async def polling_loop():
    global offset
    load_data()
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"offset": offset, "timeout": 30}) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        await asyncio.sleep(5)
                        continue
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        if "callback_query" in update:
                            await handle_callback(update["callback_query"])
                        elif "message" in update:
                            await handle_text(update["message"])
            await asyncio.sleep(1)
        except:
            await asyncio.sleep(5)

# ========== WEB SERVER ==========
async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/ping', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"✅ Web server on port {PORT}")

# ========== KEEP ALIVE ==========
async def keep_alive():
    url = "https://tradingbotlast.onrender.com"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(f"{url}/ping", timeout=5)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 Ping")
        except:
            pass
        await asyncio.sleep(120)

# ========== MAIN ==========
async def main():
    if not TELEGRAM_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not set!")
        exit(1)
    print("🚀 Bot starting...")
    await asyncio.gather(
        start_web_server(),
        polling_loop(),
        scan_signals(),
        keep_alive(),
        send_daily_report()
    )

if __name__ == "__main__":
    asyncio.run(main())
