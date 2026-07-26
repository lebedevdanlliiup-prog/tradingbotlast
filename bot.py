import os
import asyncio
import aiohttp
import json
import numpy as np
from datetime import datetime
from aiohttp import web

TELEGRAM_TOKEN ="8608954811:AAGJU_4pEJGZC6rNR5-Qq7JFrCik6PAPjVg"
PORT = int(os.environ.get("PORT", 8080))

PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"]
DB_FILE = "users.json"
CHECK_INTERVAL = 180

active_users = set()
offset = 0
first_run_done = False

def load_users():
    global active_users
    try:
        with open(DB_FILE, "r") as f:
            active_users = set(json.load(f))
    except:
        active_users = set()

def save_users():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(list(active_users), f)
    except:
        pass

async def send_tg(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        except:
            pass

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
    
    if price > np.max(high[-20:-5]) * 1.01:
        signals.append({
            "type": "Breakout",
            "side": "LONG",
            "entry": round(price, 2),
            "sl": round(price - atr * 1.5, 2),
            "tp": round(price + atr * 2, 2),
            "prob": 65
        })
    elif price < np.min(low[-20:-5]) * 0.99:
        signals.append({
            "type": "Breakout",
            "side": "SHORT",
            "entry": round(price, 2),
            "sl": round(price + atr * 1.5, 2),
            "tp": round(price - atr * 2, 2),
            "prob": 65
        })
    
    return signals

async def scan_signals():
    global first_run_done
    last_scan_time = 0

    while True:
        try:
            now = datetime.now().timestamp()
            
            # Ждем 3 минуты перед ПЕРВЫМ сканированием
            if not first_run_done:
                print("⏳ Жду 3 минуты перед первым сканированием...")
                await asyncio.sleep(CHECK_INTERVAL)
                first_run_done = True
                print("✅ Первое сканирование разрешено")
                continue

            # Проверяем, прошло ли 3 минуты с последнего сканирования
            if now - last_scan_time < CHECK_INTERVAL:
                await asyncio.sleep(10)
                continue

            last_scan_time = now
            
            if not active_users:
                print("👥 Нет активных пользователей")
                await asyncio.sleep(30)
                continue

            # Отправляем статус "сканирую"
            for user_id in list(active_users):
                await send_tg(user_id, "🔍 Сканирую рынок...")
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

            if found_any:
                for user_id in list(active_users):
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
                for user_id in list(active_users):
                    await send_tg(user_id, "😴 За 3 минуты сделок нет. Сканирую дальше...")
                    await asyncio.sleep(0.2)

        except Exception as e:
            print(f"Scan error: {e}")
            await asyncio.sleep(30)

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

async def polling_loop():
    global offset
    load_users()
    print(f"👥 Загружено пользователей: {len(active_users)}")

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
                        if "message" in update:
                            chat_id = update["message"]["chat"]["id"]
                            text = update["message"].get("text", "")

                            if text == "/start":
                                active_users.add(chat_id)
                                save_users()
                                await send_tg(chat_id, "✅ Бот запущен!\n\n📊 Сканирую: BTC, ETH, BNB, SOL, XRP, ADA\n🔍 Паттерны: Треугольник, Прорыв\n📌 Каждые 3 минуты пишу, есть сигналы или нет.")
                                print(f"👤 Новый пользователь: {chat_id}")

            await asyncio.sleep(2)
        except Exception as e:
            print(f"Polling error: {e}")
            await asyncio.sleep(10)

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
