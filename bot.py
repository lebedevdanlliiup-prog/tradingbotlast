import os
import asyncio
import aiohttp
import json
import numpy as np
from datetime import datetime
from aiohttp import web

# ========== CONFIG =========
TELEGRAM_TOKEN = "8608954811:AAGJU_4pEJGZC6rNR5-Qq7JFrCik6PAPjVg"
PORT = int(os.environ.get("PORT", 8080))
CHECK_INTERVAL = 300
DB_FILE = "users.json"

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
PAIRS_ALL = PAIRS  # Теперь 30 пар

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

# ========== SEND ==========
async def send_tg(chat_id, text, reply_markup=None):
    if not is_notifications_enabled(chat_id):
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

async def delete_message(chat_id, message_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
        except:
            pass

async def send_and_delete(chat_id, text, delete_after=10):
    if not is_notifications_enabled(chat_id):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10) as resp:
                data = await resp.json()
                if data.get("ok") and data.get("result"):
                    msg_id = data["result"]["message_id"]
                    await asyncio.sleep(delete_after)
                    await delete_message(chat_id, msg_id)
        except:
            pass

async def send_forced(chat_id, text, reply_markup=None):
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
        status_text = "ON" if new_status else "OFF"
        await send_forced(chat_id, f"🔔 Notifications {status_text}")
        await asyncio.sleep(2)
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
        await send_forced(chat_id, f"Mode: {mode}", kb)

    elif data == "set_threshold":
        waiting_for_threshold[chat_id] = True
        kb = get_back_menu_keyboard()
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
                await send_forced(chat_id, "Enter value between 10 and 99.")
        except:
            await send_forced(chat_id, "Please enter a number.")
        return

    if text == "/start":
        active_users.add(chat_id)
        if chat_id not in user_notifications:
            user_notifications[chat_id] = True
        save_data()
        
        await send_forced(chat_id, "✅ Bot activated! Scanning market...\n⏳ Waiting for signals.")
        
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_forced(chat_id, "Welcome! Use the menu below:", kb)

# ========== SCANNER ==========
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
                if is_notifications_enabled(user_id):
                    await send_tg(user_id, "🔍 Scanning market...")
                await asyncio.sleep(0.2)
            
            found_any = False
            all_signals = []
            
            # Сканируем все 30 пар с паузой чтобы не перегружать Bybit
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
                await asyncio.sleep(0.3)  # Пауза между парами
            
            for user_id in list(active_users):
                if not is_notifications_enabled(user_id):
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
                        await send_tg(user_id, msg)
                        await asyncio.sleep(0.3)
                    last_no_signal_msg[user_id] = 0
                else:
                    last_time = last_no_signal_msg.get(user_id, 0)
                    if now - last_time >= 60:
                        await send_and_delete(user_id, "😴 No deals for 5 minutes. Scanning...", delete_after=180)
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
        keep_alive()
    )

if __name__ == "__main__":
    asyncio.run(main())
