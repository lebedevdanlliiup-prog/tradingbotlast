import os
import asyncio
import aiohttp
import numpy as np
import json
from datetime import datetime
from aiohttp import web

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_TOKEN="8608954811:AAGJU_4pEJGZC6rNR5-Qq7JFrCik6PAPjVg"
CHECK_INTERVAL = 180
CANDLES_COUNT_15M = 60
CANDLES_COUNT_1H = 30
VOLUME_MULTIPLIER = 1.4
DEFAULT_MIN_PROBABILITY = 70
STATUS_MESSAGE_INTERVAL = 180

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
offset = 0


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
        log(f"Data loaded: {len(active_users)} users", "INFO")
    except Exception as e:
        log(f"Load error: {e}", "ERROR")
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
        except Exception as e:
            log(f"Delete message error: {e}", "WARN")
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
        except Exception as e:
            log(f"Edit message error: {e}", "WARN")


def format_pair_list(pairs):
    return "\n".join([f"{i+1}. {p}" for i, p in enumerate(pairs)])


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


def get_pairs_for_user(chat_id):
    if chat_id in user_pairs and user_pairs[chat_id]:
        return user_pairs[chat_id]
    return get_pairs_for_mode(CURRENT_MODE)


def is_notifications_enabled(chat_id):
    return user_notifications.get(chat_id, True)


async def show_notification_status(chat_id, status):
    msg = "🔔 Bot is turning ON..." if status else "🔕 Bot is turning OFF..."
    await send_tg(msg, chat_id, delete_previous=True)
    await asyncio.sleep(2)
    await delete_previous_message(chat_id)


async def handle_callback(callback_query):
    global CURRENT_MODE
    data = callback_query.get("data")
    chat_id = callback_query["from"]["id"]
    message_id = callback_query["message"]["message_id"]

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        try:
            await session.post(url, json={"callback_query_id": callback_query["id"]})
        except Exception as e:
            log(f"AnswerCallbackQuery error: {e}", "WARN")

    if data == "back_main":
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_tg("Main menu:", chat_id, kb, delete_previous=True)

    elif data == "exit_pairs":
        if chat_id in temp_pairs:
            del temp_pairs[chat_id]
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_tg("Main menu:", chat_id, kb, delete_previous=True)

    elif data == "save_pairs":
        if chat_id in temp_pairs:
            user_pairs[chat_id] = temp_pairs[chat_id].copy()
            del temp_pairs[chat_id]
        else:
            user_pairs[chat_id] = get_pairs_for_mode(CURRENT_MODE).copy()
        save_data()

        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_tg("Main menu:", chat_id, kb, delete_previous=True)

    elif data == "toggle_notifications":
        new_status = not is_notifications_enabled(chat_id)
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
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
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
    sum_xx = np.sum(x * x)
    denom = n * sum_xx - sum_x ** 2
    if denom == 0:
        return 0.0, 0.0
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


async def fetch_candles(session, symbol, interval, limit):
    try:
        url = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
        async with session.get(url, params=params, timeout=10) as resp:
            data = await resp.json()
            if data.get("retCode") != 0 or not data.get("result", {}).get("list"):
                return None, None, None, None, None
            candles = data["result"]["list"]
            time_arr = np.array([int(c[0]) for c in candles])
            high_arr = np.array([float(c[2]) for c in candles])
            low_arr = np.array([float(c[3]) for c in candles])
            close_arr = np.array([float(c[4]) for c in candles])
            vol_arr = np.array([float(c[5]) for c in candles])
            return time_arr, high_arr, low_arr, close_arr, vol_arr
    except Exception as e:
        log(f"Fetch candles error {symbol}: {e}", "ERROR")
        return None, None, None, None, None


def detect_signal(high, low, close, vol, threshold_prob=70):
    if len(close) < 20:
        return False, 0.0, "Not enough data"

    atr = calculate_atr(high, low, close)
    slope, intercept = linear_regression_slope_intercept(
        np.arange(len(close)), close
    )

    price_change_pct = (close[-1] - close[0]) / close[0] * 100
    vol_change_pct = (vol[-1] - np.mean(vol[-10:])) / max(np.mean(vol[-10:]), 1e-6) * 100

    probability = 50.0
    if slope > 0:
        probability += 25
    if price_change_pct > 2.0:
        probability += 15
    if vol_change_pct > 50:
        probability += 10

    signal = probability >= threshold_prob and slope > 0
    return signal, probability, f"ATR={atr:.2f}, slope={slope:.4f}"


async def handle_text_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if chat_id in waiting_for_threshold:
        try:
            val = int(text)
            if 10 <= val <= 99:
                user_thresholds[chat_id] = val
                waiting_for_threshold.pop(chat_id, None)
                save_data()
                kb = get_main_menu_keyboard(
                    CURRENT_MODE, val, is_notifications_enabled(chat_id)
                )
                await send_tg(f"Threshold set to {val}%", chat_id, kb, delete_previous=True)
            else:
                await send_tg("Enter value between 10 and 99.", chat_id)
        except ValueError:
            await send_tg("Please enter a number.", chat_id)
        return

    if text == "/start":
        active_users.add(chat_id)
        if chat_id not in user_notifications:
            user_notifications[chat_id] = True
        last_status_time[chat_id] = datetime.now().timestamp()
        save_data()
        current_thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
        kb = get_main_menu_keyboard(CURRENT_MODE, current_thresh, is_notifications_enabled(chat_id))
        await send_tg("Welcome! Use the menu below:", chat_id, kb)


async def health_check(request):
    return web.Response(text="Bot is running!")


async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/ping', health_check)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log(f"Web server running on port {port}")


async def polling_loop():
    global offset, BOT_RUNNING
    load_data()
    
    web_task = asyncio.create_task(start_web_server())
    
    async with aiohttp.ClientSession() as session:
        while BOT_RUNNING:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
                payload = {"offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]}
                async with session.post(url, json=payload) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        log("TG getUpdates error", "ERROR")
                        await asyncio.sleep(5)
                        continue

                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        if "callback_query" in update:
                            await handle_callback(update["callback_query"])
                        elif "message" in update and "text" in update["message"]:
                            await handle_text_message(update["message"])

                # Проверка сигналов для каждого пользователя
                now = datetime.now().timestamp()
                for chat_id in list(active_users):
                    last = last_status_time.get(chat_id, 0)
                    if now - last >= STATUS_MESSAGE_INTERVAL:
                        last_status_time[chat_id] = now
                        
                        # --- СТАТУСНОЕ СООБЩЕНИЕ ---
                        if is_notifications_enabled(chat_id):
                            await send_tg("✅ Bot is running and scanning the market...", chat_id)
                        # --- КОНЕЦ СТАТУСНОГО СООБЩЕНИЯ ---
                        
                        pairs = get_pairs_for_user(chat_id)
                        thresh = user_thresholds.get(chat_id, DEFAULT_MIN_PROBABILITY)
                        notif_on = is_notifications_enabled(chat_id)

                        signals = []
                        for pair in pairs:
                            t15, h15, l15, c15, v15 = await fetch_candles(session, pair, "15", CANDLES_COUNT_15M)
                            if t15 is None:
                                continue
                            sig, prob, info = detect_signal(h15, l15, c15, v15, thresh)
                            if sig:
                                signals.append((pair, prob, info))

                        if signals and notif_on:
                            msg = "🚨 SIGNALS DETECTED:\n\n" + "\n".join(
                                [f"{p} — {prob:.1f}% ({info})" for p, prob, info in signals]
                            )
                            kb = get_back_menu_keyboard()
                            await send_tg(msg, chat_id, kb)

                await asyncio.sleep(CHECK_INTERVAL)

            except Exception as e:
                log(f"Polling loop error: {e}", "ERROR")
                await asyncio.sleep(10)


async def main():
    await polling_loop()


if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not set!")
        exit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        BOT_RUNNING = False
        log("Bot stopped by user")
