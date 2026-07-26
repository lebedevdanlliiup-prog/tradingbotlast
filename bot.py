import os
import asyncio
import aiohttp
import numpy as np
import json
from datetime import datetime
from aiohttp import web

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_TOKEN ="8608954811:AAGJU_4pEJGZC6rNR5-Qq7JFrCik6PAPjVg"
CHECK_INTERVAL = 180  # 3 минуты
CANDLES_COUNT_15M = 60
CANDLES_COUNT_1H = 30
VOLUME_MULTIPLIER = 1.4
DEFAULT_MIN_PROBABILITY = 70
STATUS_MESSAGE_INTERVAL = 180  # 3 минуты

PAIRS_STRONG = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
PAIRS_MEME = ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT"]
PAIRS_ALL = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOTUSDT", "LINKUSDT", "AVAXUSDT", "MATICUSDT"]

CURRENT_MODE = "Strong"
BOT_RUNNING = True
DB_FILE = "active_users.json"

user_pairs = {}
user_notifications = {}
user_thresholds = {}
waiting_for_threshold = {}
active_users = set()
last_message_id = {}
temp_pairs = {}
last_status_time = {}

def get_pairs_for_mode(mode):
    if mode == "Strong":
        return PAIRS_STRONG
    elif mode == "Meme":
        return PAIRS_MEME
    else:
        return PAIRS_ALL

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
    except Exception as e:
        log(f"Save error: {e}", "ERROR")

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {level}: {msg}")

async def delete_previous_message(chat_id):
    if chat_id in last_message_id and last_message_id[chat_id]:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
            payload = {"chat_id": chat_id, "message_id": last_message_id[chat_id]}
            async with aiohttp.ClientSession() as session:
                await session.post(url, json=payload, timeout=5)
        except:
            pass
        last_message_id[chat_id] = None

async def send_tg(text, chat_id, reply_markup=None, delete_previous=False):
    if not TELEGRAM_TOKEN:
        log("No TELEGRAM_TOKEN", "ERROR")
        return None
        
    if delete_previous:
        await delete_previous_message(chat_id)
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if data.get("ok") and data.get("result"):
                    last_message_id[chat_id] = data["result"]["message_id"]
                return data
        except Exception as e:
            log(f"TG send error: {e}", "ERROR")
            return None

async def edit_tg(chat_id, message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json=payload, timeout=10)
        except:
            pass

def format_pair_list(pairs):
    return "\n".join([f"{i+1}. {p}" for i, p in enumerate(pairs)])

def get_main_menu_keyboard(mode, threshold, notifications_on=True):
    # Если бот ВКЛЮЧЕН → показываем OFF (выключить)
    # Если бот ВЫКЛЮЧЕН → показываем ON (включить)
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
    buttons = [
        [{"text": "Back to menu", "callback_data": "back_main"}]
    ]
    return {"inline_keyboard": buttons}

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

def get_pairs_for_user(chat_id):
    if chat_id in user_pairs and user_pairs[chat_id]:
        return user_pairs[chat_id]
    else:
        return get_pairs_for_mode(CURRENT_MODE)

def is_notifications_enabled(chat_id):
    return user_notifications.get(chat_id, True)

async def show_notification_status(chat_id, status):
    msg = "🔔 Bot is turning ON..." if status else "🔕 Bot is turning OFF..."
    await send_tg(msg, chat_id, delete_previous=True)
    await asyncio.sleep(2)
    await delete_previous_message(chat_id)

async def send_status_message():
    """Отправляет сообщение о работе бота каждые 3 минуты"""
    while BOT_RUNNING:
        current_time = datetime.now().timestamp()
        
        for user_id in list(active_users):
            if not is_notifications_enabled(user_id):
                continue
                
            last_time = last_status_time.get(user_id, 0)
            if current_time - last_time >= STATUS_MESSAGE_INTERVAL:
                msg = "✅ Bot is running and scanning the market..."
                await send_tg(msg, user_id)
                last_status_time[user_id] = current_time
        
        await asyncio.sleep(60)  # Проверяем каждую минуту

async def handle_callback(callback_query):
    global CURRENT_MODE
    data = callback_query.get("data")
    chat_id = callback_query["from"]["id"]
    message_id = callback_query["message"]["message_id"]

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        try:
            await session.post(url, json={"callback_query_id": callback_query["id"]})
        except:
            pass

    if data == "back_main":
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        notif_status = is_notifications_enabled(chat_id)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, notif_status)
        await send_tg("Main menu:", chat_id, kb, delete_previous=True)

    elif data == "exit_pairs":
        if chat_id in temp_pairs:
            del temp_pairs[chat_id]
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        notif_status = is_notifications_enabled(chat_id)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, notif_status)
        await send_tg("Main menu:", chat_id, kb, delete_previous=True)

    elif data == "save_pairs":
        if chat_id in temp_pairs:
            user_pairs[chat_id] = temp_pairs[chat_id].copy()
            del temp_pairs[chat_id]
        else:
            user_pairs[chat_id] = get_pairs_for_mode(CURRENT_MODE).copy()
        save_data()
        
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        notif_status = is_notifications_enabled(chat_id)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, notif_status)
        await send_tg("Main menu:", chat_id, kb, delete_previous=True)

    elif data == "toggle_notifications":
        current_status = is_notifications_enabled(chat_id)
        new_status = not current_status
        user_notifications[chat_id] = new_status
        save_data()
        
        await show_notification_status(chat_id, new_status)
        
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, new_status)
        await send_tg("Main menu:", chat_id, kb, delete_previous=True)

    elif data == "show_pairs":
        temp_pairs[chat_id] = user_pairs.get(chat_id, get_pairs_for_mode(CURRENT_MODE)).copy()
        pairs = temp_pairs[chat_id]
        text = f"Current selection ({len(pairs)}):\n\n{format_pair_list(pairs)}\n\nClick to toggle, then press SAVE."
        kb = get_pair_selection_keyboard(chat_id)
        await send_tg(text, chat_id, kb, delete_previous=True)

    elif data.startswith("toggle_"):
        pair = data.replace("toggle_", "")
        if chat_id not in temp_pairs:
            temp_pairs[chat_id] = user_pairs.get(chat_id, get_pairs_for_mode(CURRENT_MODE)).copy()
        
        if pair in temp_pairs[chat_id]:
            temp_pairs[chat_id].remove(pair)
        else:
            temp_pairs[chat_id].append(pair)
        
        pairs = temp_pairs[chat_id]
        text = f"Current selection ({len(pairs)}):\n\n{format_pair_list(pairs)}\n\nClick to toggle, then press SAVE."
        kb = get_pair_selection_keyboard(chat_id)
        await edit_tg(chat_id, message_id, text, kb)

    elif data.startswith("mode_"):
        mode = data.split("_")[1]
        CURRENT_MODE = mode
        save_data()
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        notif_status = is_notifications_enabled(chat_id)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, notif_status)
        await send_tg(f"Mode: {mode}", chat_id, kb, delete_previous=True)

    elif data == "set_threshold":
        waiting_for_threshold[chat_id] = True
        kb = get_back_menu_keyboard()
        await send_tg("Enter number (10-99) for minimum probability threshold:", chat_id, kb, delete_previous=True)

    elif data == "info":
        info = ("Trading Bot\n\n"
                "Timeframes: 15m + 1h\n\n"
                "Port (threshold):\n"
                "Minimum probability for signals.\n"
                "Higher value = higher quality signals.\n"
                "Range: 10-99%.\n\n"
                "⚠️ This bot is for informational purposes only.\n"
                "It does NOT constitute financial advice or trading recommendations.")
        kb = get_back_menu_keyboard()
        await send_tg(info, chat_id, kb, delete_previous=True)

    elif data == "stop":
        active_users.discard(chat_id)
        save_data()
        await send_tg("You have been unsubscribed from signals.\nSend /start to resume.", chat_id, delete_previous=True)

# --- ОСТАЛЬНЫЕ ФУНКЦИИ ---
def calculate_atr(high, low, close, period=14):
    if len(close) < period + 1:
        return 1.0
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    val = float(np.mean(tr[-period:]))
    return val if val > 0 else 1.0

def linear_regression_slope_intercept(x, y):
    n = len(x)
    if n < 2:
        return 0.0, 0.0
    sum_x = np.sum(x)
    sum_y = np.sum(y)
    sum_xy = np.sum(x * y)
    sum_xx = np.sum(x**2)
    denom = n * sum_xx - sum_x**2
    if abs(denom) < 1e-9:
        return 0.0, 0.0
    m = (n * sum_xy - sum_x * sum_y) / denom
    c = (sum_y - m * sum_x) / n
    return float(m), float(c)

def get_trend_direction_mtf(close_15m, close_1h):
    avg_short_15 = np.mean(close_15m[-20:])
    avg_long_15 = np.mean(close_15m[-50:])
    trend_15 = "UP" if avg_short_15 > avg_long_15 * 1.01 else ("DOWN" if avg_short_15 < avg_long_15 * 0.99 else "FLAT")

    avg_short_1h = np.mean(close_1h[-10:])
    avg_long_1h = np.mean(close_1h[-20:])
    trend_1h = "UP" if avg_short_1h > avg_long_1h * 1.01 else ("DOWN" if avg_short_1h < avg_long_1h * 0.99 else "FLAT")
    return trend_15, trend_1h

def adaptive_thresholds(atr_15, atr_1h, price):
    sl_factor = 2.0 if atr_15 > atr_1h else 1.5
    tp_factor = 1.8 if atr_15 < atr_1h else 2.2
    noise_threshold = atr_15 * 0.3
    return sl_factor, tp_factor, noise_threshold

def detect_triangle(high, low, close, volume, atr):
    peaks = np.where((high[1:-1] > high[:-2]) & (high[1:-1] > high[2:]))[0] + 1
    troughs = np.where((low[1:-1] < low[:-2]) & (low[1:-1] < low[2:]))[0] + 1
    if len(peaks) < 3 or len(troughs) < 3:
        return None
    m_up, c_up = linear_regression_slope_intercept(peaks[-3:], high[peaks[-3:]])
    m_down, c_down = linear_regression_slope_intercept(troughs[-3:], low[troughs[-3:]])
    price = float(close[-1])
    last_idx = len(close) - 1
    line_up_last = m_up * last_idx + c_up
    line_down_last = m_down * last_idx + c_down
    height = abs(line_up_last - line_down_last)
    if height < price * 0.002:
        return None
    if m_up < 0 and m_down > 0:
        if price > line_up_last * 1.005:
            return {"side": "LONG", "entry": round(price, 2), "type": "Triangle", "height": height}
        elif price < line_down_last * 0.995:
            return {"side": "SHORT", "entry": round(price, 2), "type": "Triangle", "height": height}
    return None

def detect_head_and_shoulders(high, low, close, volume):
    peaks = np.where((high[1:-1] > high[:-2]) & (high[1:-1] > high[2:]))[0] + 1
    if len(peaks) < 3:
        return None
    p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
    if high[p2] > high[p1] and high[p2] > high[p3] and abs(high[p1]-high[p3]) < high[p2]*0.03:
        neck_level = min(np.min(low[p1:p2]), np.min(low[p2:p3]))
        price = float(close[-1])
        if price < neck_level * 0.995:
            height = high[p2] - neck_level
            return {"side": "SHORT", "entry": round(price, 2), "type": "HeadAndShoulders", "height": height}
    return None

def detect_inverse_head_and_shoulders(high, low, close, volume):
    troughs = np.where((low[1:-1] < low[:-2]) & (low[1:-1] < low[2:]))[0] + 1
    if len(troughs) < 3:
        return None
    t1, t2, t3 = troughs[-3], troughs[-2], troughs[-1]
    if low[t2] < low[t1] and low[t2] < low[t3] and abs(low[t1]-low[t3]) < low[t2]*0.03:
        neck_level = max(np.max(high[t1:t2]), np.max(high[t2:t3]))
        price = float(close[-1])
        if price > neck_level * 1.005:
            height = neck_level - low[t2]
            return {"side": "LONG", "entry": round(price, 2), "type": "InvHeadAndShoulders", "height": height}
    return None

def detect_double_top(high, low, close, volume):
    peaks = np.where((high[1:-1] > high[:-2]) & (high[1:-1] > high[2:]))[0] + 1
    if len(peaks) < 2:
        return None
    p1, p2 = peaks[-2], peaks[-1]
    if abs(high[p1] - high[p2]) < high[p1] * 0.015:
        neck = min(low[p1:p2])
        price = float(close[-1])
        if price < neck * 0.995:
            height = high[p1] - neck
            return {"side": "SHORT", "entry": round(price, 2), "type": "DoubleTop", "height": height}
    return None

def detect_double_bottom(high, low, close, volume):
    troughs = np.where((low[1:-1] < low[:-2]) & (low[1:-1] < low[2:]))[0] + 1
    if len(troughs) < 2:
        return None
    t1, t2 = troughs[-2], troughs[-1]
    if abs(low[t1] - low[t2]) < low[t1] * 0.015:
        neck = max(high[t1:t2])
        price = float(close[-1])
        if price > neck * 1.005:
            height = neck - low[t1]
            return {"side": "LONG", "entry": round(price, 2), "type": "DoubleBottom", "height": height}
    return None

def detect_flag(high, low, close, volume, atr):
    impulse_up = (close[-10] - close[-20]) / close[-20] > 0.03
    impulse_dn = (close[-10] - close[-20]) / close[-20] < -0.03
    if not (impulse_up or impulse_dn):
        return None

    corr_range = np.max(high[-10:]) - np.min(low[-10:])
    full_range = np.max(high[-20:]) - np.min(low[-20:])
    
    if full_range == 0:
        return None
    if corr_range / full_range < 0.4:
        price = float(close[-1])
        if impulse_up:
            if price > np.max(high[-10:]) * 1.002:
                return {"side": "LONG", "entry": round(price, 2), "type": "Flag", "height": full_range}
        elif impulse_dn:
            if price < np.min(low[-10:]) * 0.998:
                return {"side": "SHORT", "entry": round(price, 2), "type": "Flag", "height": full_range}
    return None

def calculate_probability(signal, high, low, close, volume, atr_15, atr_1h, trend_15, trend_1h):
    score = 50.0

    if (signal["side"] == "LONG" and trend_15 == "UP" and trend_1h != "DOWN") or \
       (signal["side"] == "SHORT" and trend_15 == "DOWN" and trend_1h != "UP"):
        score += 20

    avg_vol = np.mean(volume[-20:])
    last_vol = volume[-1]
    if last_vol > avg_vol * VOLUME_MULTIPLIER:
        score += 15
    elif last_vol < avg_vol * 0.8:
        score -= 15

    atr_ratio = atr_15 / (atr_1h if atr_1h > 0 else 1.0)
    if 0.7 <= atr_ratio <= 1.3:
        score += 10
    else:
        score -= 5

    if signal["type"] == "Flag":
        score -= 20
    elif signal["type"] in ["Triangle", "HeadAndShoulders", "InvHeadAndShoulders"]:
        score += 10

    if signal["type"] == "Triangle":
        height = signal["height"]
        if height > atr_15 * 1.5:
            score += 5
    return int(max(10, min(95, score)))

async def fetch_candles(session, symbol, interval, count, max_retries=3):
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": count}

    for attempt in range(max_retries):
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json()
                if data.get("retCode") == 0 and data.get("result", {}).get("list"):
                    rows = data["result"]["list"]
                    high = np.array([float(r[2]) for r in rows], dtype=np.float64)
                    low = np.array([float(r[3]) for r in rows], dtype=np.float64)
                    close = np.array([float(r[4]) for r in rows], dtype=np.float64)
                    volume = np.array([float(r[5]) for r in rows], dtype=np.float64)
                    return high, low, close, volume
                elif resp.status == 429:
                    wait = (2 ** attempt) + (np.random.rand() * 0.5)
                    log(f"{symbol} rate limit, retry in {wait:.1f}s", "WARN")
                    await asyncio.sleep(wait)
                else:
                    log(f"Bybit error {resp.status} for {symbol}", "ERROR")
                    break
        except Exception as e:
            log(f"Candle fetch error {symbol}: {e}", "ERROR")
            wait = (2 ** attempt) + (np.random.rand() * 0.5)
            await asyncio.sleep(wait)
    return None

async def scan_signals():
    global BOT_RUNNING
    while BOT_RUNNING:
        all_symbols = set()
        for uid in active_users:
            if not is_notifications_enabled(uid):
                continue
            pairs = get_pairs_for_user(uid)
            all_symbols.update(pairs)
        
        if not all_symbols:
            all_symbols = set(get_pairs_for_mode(CURRENT_MODE))
        
        symbols = list(all_symbols)
        if symbols:
            log(f"Scanning {len(symbols)} symbols...")

        async with aiohttp.ClientSession() as session:
            batch_size = 3
            for i in range(0, len(symbols), batch_size):
                if not BOT_RUNNING:
                    break
                    
                batch = symbols[i:i+batch_size]
                tasks = [fetch_candles(session, sym, "15", CANDLES_COUNT_15M + 20) for sym in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for sym, res in zip(batch, results):
                    if isinstance(res, Exception) or res is None:
                        continue

                    high_15, low_15, close_15, volume_15 = res
                    res_1h = await fetch_candles(session, sym, "60", CANDLES_COUNT_1H + 10)
                    if not res_1h:
                        continue
                    high_1h, low_1h, close_1h, volume_1h = res_1h

                    atr_15 = calculate_atr(high_15, low_15, close_15)
                    atr_1h = calculate_atr(high_1h, low_1h, close_1h)
                    trend_15, trend_1h = get_trend_direction_mtf(close_15, close_1h)

                    patterns = [
                        detect_triangle(high_15, low_15, close_15, volume_15, atr_15),
                        detect_head_and_shoulders(high_15, low_15, close_15, volume_15),
                        detect_inverse_head_and_shoulders(high_15, low_15, close_15, volume_15),
                        detect_double_top(high_15, low_15, close_15, volume_15),
                        detect_double_bottom(high_15, low_15, close_15, volume_15),
                        detect_flag(high_15, low_15, close_15, volume_15, atr_15),
                    ]

                    for signal in patterns:
                        if not signal or atr_15 < 0.1 or close_15[-1] < 1:
                            continue

                        prob = calculate_probability(signal, high_15, low_15, close_15, volume_15, atr_15, atr_1h, trend_15, trend_1h)

                        for user_id in list(active_users):
                            if not is_notifications_enabled(user_id):
                                continue
                                
                            user_pairs_list = get_pairs_for_user(user_id)
                            if sym not in user_pairs_list:
                                continue
                                
                            user_thresh = user_thresholds.get(user_id, DEFAULT_MIN_PROBABILITY)
                            if prob >= user_thresh:
                                sl_factor, tp_factor, _ = adaptive_thresholds(atr_15, atr_1h, signal["entry"])
                                
                                if signal["side"] == "LONG":
                                    sl = signal["entry"] - (atr_15 * sl_factor)
                                    tp = signal["entry"] + (signal["height"] * tp_factor)
                                else:
                                    sl = signal["entry"] + (atr_15 * sl_factor)
                                    tp = signal["entry"] - (signal["height"] * tp_factor)

                                msg = (f"Signal: {signal['type']}\n"
                                       f"Pair: {sym}\n"
                                       f"Side: {signal['side']}\n"
                                       f"Entry: {signal['entry']}\n"
                                       f"SL: {round(sl, 2)}\n"
                                       f"TP: {round(tp, 2)}\n"
                                       f"Probability: {prob}%\n"
                                       f"Trends: 15m={trend_15}, 1h={trend_1h}")
                                await send_tg(msg, user_id)

                await asyncio.sleep(1)

        await asyncio.sleep(CHECK_INTERVAL)

async def listen_updates():
    offset = 0
    error_count = 0
    base_delay = 5
    max_delay = 60

    while BOT_RUNNING:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        params = {"offset": offset, "timeout": 20}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                async with session.post(url, json=params) as resp:
                    result = await resp.json()
                    if not result.get("ok"):
                        log("Telegram API error", "ERROR")
                        error_count += 1
                        delay = min(base_delay * (2 ** error_count), max_delay)
                        await asyncio.sleep(delay)
                        continue
                    error_count = 0
                    updates = result.get("result", [])
                    for u in updates:
                        offset = u["update_id"] + 1
                        if "message" in u:
                            chat_id = u["message"]["chat"]["id"]
                            text = u["message"].get("text", "")
                            if chat_id in waiting_for_threshold:
                                try:
                                    val = int(text)
                                    if 10 <= val <= 99:
                                        user_thresholds[chat_id] = val
                                        save_data()
                                        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
                                        notif_status = is_notifications_enabled(chat_id)
                                        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, notif_status)
                                        await send_tg(f"Threshold set: {val}%", chat_id, kb, delete_previous=True)
                                    else:
                                        kb = get_back_menu_keyboard()
                                        await send_tg("Number must be 10-99.", chat_id, kb, delete_previous=True)
                                except ValueError:
                                    kb = get_back_menu_keyboard()
                                    await send_tg("Enter a valid number.", chat_id, kb, delete_previous=True)
                                waiting_for_threshold.pop(chat_id, None)
                            else:
                                if text.lower() == "/start":
                                    active_users.add(chat_id)
                                    if chat_id not in user_notifications:
                                        user_notifications[chat_id] = True
                                    save_data()
                                    current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
                                    notif_status = is_notifications_enabled(chat_id)
                                    kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, notif_status)
                                    await send_tg(
                                        "Trading Bot\n\nSelect a mode and set your threshold.",
                                        chat_id,
                                        kb
                                    )
                        elif "callback_query" in u:
                            await handle_callback(u["callback_query"])
        except Exception as e:
            log(f"Polling error: {e}", "ERROR")
            error_count += 1
            delay = min(base_delay * (2 ** error_count), max_delay)
            await asyncio.sleep(delay)

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/ping', health_check)
    app.router.add_get('/', health_check)
    
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log(f"Web server running on port {port}")

async def main():
    load_data()
    log("Bot started.")
    
    web_task = asyncio.create_task(start_web_server())
    scan_task = asyncio.create_task(scan_signals())
    listen_task = asyncio.create_task(listen_updates())
    status_task = asyncio.create_task(send_status_message())
    
    try:
        await asyncio.gather(web_task, scan_task, listen_task, status_task)
    except asyncio.CancelledError:
        log("Bot stopping.")
    except Exception as e:
        log(f"Critical error: {e}", "ERROR")
    finally:
        save_data()
        log("Bot stopped. Data saved.")

if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not set!")
        exit(1)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        BOT_RUNNING = False
        log("Stopped by user.")
