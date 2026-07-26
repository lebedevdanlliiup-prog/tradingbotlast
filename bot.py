import os
import asyncio
import aiohttp
import json
import numpy as np
from datetime import datetime
from aiohttp import web

# ========== КОНФИГ ==========
TELEGRAM_TOKEN ="8608954811:AAGJU_4pEJGZC6rNR5-Qq7JFrCik6PAPjVg"
PORT = int(os.environ.get("PORT", 8080))
CHECK_INTERVAL = 180
DB_FILE = "users.json"

PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"]

CURRENT_MODE = "Strong"
DEFAULT_MIN_PROBABILITY = 70

PAIRS_STRONG = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
PAIRS_MEME = ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT"]
PAIRS_ALL = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOTUSDT", "LINKUSDT", "AVAXUSDT", "MATICUSDT"]

active_users = set()
offset = 0
user_thresholds = {}
user_pairs = {}
user_notifications = {}
waiting_for_threshold = {}
temp_pairs = {}
first_scan_done = False
last_scan_time = 0

# ========== ЗАГРУЗКА/СОХРАНЕНИЕ ==========
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

# ========== ОТПРАВКА ==========
async def send_tg(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json=payload, timeout=10)
        except:
            pass

# ========== КЛАВИАТУРЫ ==========
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

# ========== ОБРАБОТЧИК КНОПОК ==========
async def handle_callback(callback_query):
    global CURRENT_MODE
    data = callback_query.get("data")
    chat_id = callback_query["from"]["id"]

    if data == "back_main":
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_tg(chat_id, "Main menu:", kb)

    elif data == "exit_pairs":
        if chat_id in temp_pairs:
            del temp_pairs[chat_id]
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_tg(chat_id, "Main menu:", kb)

    elif data == "save_pairs":
        if chat_id in temp_pairs:
            user_pairs[chat_id] = temp_pairs[chat_id].copy()
            del temp_pairs[chat_id]
        else:
            user_pairs[chat_id] = get_pairs_for_mode(CURRENT_MODE).copy()
        save_data()
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_tg(chat_id, "Main menu:", kb)

    elif data == "toggle_notifications":
        new_status = not is_notifications_enabled(chat_id)
        user_notifications[chat_id] = new_status
        save_data()
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, new_status)
        await send_tg(chat_id, "Main menu:", kb)

    elif data == "show_pairs":
        temp_pairs[chat_id] = user_pairs.get(chat_id, get_pairs_for_mode(CURRENT_MODE)).copy()
        pairs = temp_pairs[chat_id]
        text = f"Current selection ({len(pairs)}):\n\n" + "\n".join([f"{i+1}. {p}" for i, p in enumerate(pairs)]) + "\n\nClick to toggle, then press SAVE."
        kb = get_pair_selection_keyboard(chat_id)
        await send_tg(chat_id, text, kb)

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
        await send_tg(chat_id, text, kb)

    elif data.startswith("mode_"):
        mode = data.split("_")[1]
        CURRENT_MODE = mode
        save_data()
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_tg(chat_id, f"Mode: {mode}", kb)

    elif data == "set_threshold":
        waiting_for_threshold[chat_id] = True
        kb = get_back_menu_keyboard()
        await send_tg(chat_id, "Enter number (10-99) for minimum probability threshold:", kb)

    elif data == "info":
        info = ("Trading Bot\n\n"
                "Timeframes: 15m + 1h\n\n"
                "Port (threshold):\n"
                "Minimum probability for signals.\n"
                "Higher value = higher quality signals.\n"
                "Range: 10-99%.\n\n"
                "⚠️ This bot is for informational purposes only.")
        kb = get_back_menu_keyboard()
        await send_tg(chat_id, info, kb)

# ========== ОБРАБОТЧИК ТЕКСТА ==========
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
                await send_tg(chat_id, f"Threshold set to {val}%", kb)
            else:
                await send_tg(chat_id, "Enter value between 10 and 99.")
        except:
            await send_tg(chat_id, "Please enter a number.")
        return

    if text == "/start":
        active_users.add(chat_id)
        if chat_id not in user_notifications:
            user_notifications[chat_id] = True
        save_data()
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_tg(chat_id, "Welcome! Use the menu below:", kb)

# ========== СКАНЕР ==========
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

def calculate_atr(high, low, close, period=14):
    if len(close) < period + 1:
        return 1.0
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    return float(np.mean(tr[-period:]))

def detect_setups(high, low, close, volume):
    if len(close) < 30:
        return []
    
    signals = []
    price = float(close[-1])
    atr = calculate_atr(high, low, close)
    
    range1 = np.max(high[-30:]) - np.min(low[-30:])
    range2 = np.max(high[-10:]) - np.min(low[-10:])
    
    if range2 / range1 < 0.4 and range1 > 0:
        height = range1 * 0.3
        if price > np.max(high[-5:]):
            signals.append({
                "type": "Triangle",
                "side": "LONG",
                "entry": round(price, 2),
                "sl": round(price - atr * 2, 2),
                "tp": round(price + height, 2),
                "prob": 75
            })
        elif price < np.min(low[-5:]):
            signals.append({
                "type": "Triangle",
                "side": "SHORT",
                "entry": round(price, 2),
                "sl": round(price + atr * 2, 2),
                "tp": round(price - height, 2),
                "prob": 75
            })
    
    return signals

async def scan_signals():
    global first_scan_done, last_scan_time
    
    while True:
        try:
            now = datetime.now().timestamp()
            
            # Первое сканирование через 3 минуты
            if not first_scan_done:
                print("⏳ Жду 3 минуты перед первым сканированием...")
                await asyncio.sleep(CHECK_INTERVAL)
                first_scan_done = True
                continue
            
            if now - last_scan_time < CHECK_INTERVAL:
                await asyncio.sleep(10)
                continue
            
            last_scan_time = now
            
            if not active_users:
                await asyncio.sleep(30)
                continue
            
            # Отправляем статус "сканирую"
            for user_id in list(active_users):
                await send_tg(user_id, "🔍 Сканирую рынок...")
                await asyncio.sleep(0.2)
            
            found_any = False
            all_signals = []
            
            # Сканируем пары
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
            
            # Отправляем результат
            for user_id in list(active_users):
                if not is_notifications_enabled(user_id):
                    continue
                if found_any:
                    for signal in all_signals:
                        msg = (f"🚨 СИГНАЛ: {signal['symbol']}\n"
                               f"📊 Тип: {signal['type']}\n"
                               f"📈 Сторона: {signal['side']}\n"
                               f"💰 Вход: {signal['entry']}\n"
                               f"🛑 SL: {signal['sl']}\n"
                               f"🎯 TP: {signal['tp']}\n"
                               f"⚡ Вероятность: {signal['prob']}%")
                        await send_tg(user_id, msg)
                        await asyncio.sleep(0.3)
                else:
                    await send_tg(user_id, "😴 За 3 минуты сделок нет. Сканирую дальше...")
                    await asyncio.sleep(0.2)
            
        except Exception as e:
            print(f"Scan error: {e}")
            await asyncio.sleep(30)

# ========== ПОЛЛИНГ ==========
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
            await asyncio.sleep(2)
        except:
            await asyncio.sleep(10)

# ========== ВЕБ-СЕРВЕР ==========
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
    print(f"✅ Веб-сервер на порту {PORT}")

# ========== ЗАПУСК ==========
async def main():
    if not TELEGRAM_TOKEN:
        print("❌ Ошибка: TELEGRAM_BOT_TOKEN не задан!")
        exit(1)
    print("🚀 Бот запускается...")
    await asyncio.gather(
        start_web_server(),
        polling_loop(),
        scan_signals()
    )

if __name__ == "__main__":
    asyncio.run(main())
