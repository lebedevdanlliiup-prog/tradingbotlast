import os
import asyncio
import aiohttp
import json
import numpy as np
from datetime import datetime
from aiohttp import web

# ========== КОНФИГ ==========
TELEGRAM_TOKEN = "8608954811:AAGJU_4pEJGZC6rNR5-Qq7JFrCik6PAPjVg"
PORT = int(os.environ.get("PORT", 8080))
CHECK_INTERVAL = 180

PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
DB_FILE = "users.json"

active_users = set()
offset = 0

# ========== ЗАГРУЗКА/СОХРАНЕНИЕ ==========
def load_users():
    global active_users
    try:
        with open(DB_FILE, "r") as f:
            active_users = set(json.load(f))
        print(f"✅ Загружено {len(active_users)} пользователей")
    except:
        active_users = set()
        print("📁 Новый файл пользователей")

def save_users():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(list(active_users), f)
    except:
        pass

# ========== ОТПРАВКА ==========
async def send_tg(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        except Exception as e:
            print(f"❌ Send error: {e}")

# ========== СВЕЧИ ==========
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
        except Exception as e:
            print(f"⚠️ Ошибка {symbol}: {e}")
            return None

# ========== ATR ==========
def calculate_atr(high, low, close, period=14):
    if len(close) < period + 1:
        return 1.0
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    return float(np.mean(tr[-period:]))

# ========== ТРЕУГОЛЬНИК ==========
def detect_triangle(high, low, close):
    if len(close) < 30:
        return None
    
    range1 = np.max(high[-30:]) - np.min(low[-30:])
    range2 = np.max(high[-10:]) - np.min(low[-10:])
    
    if range2 / range1 < 0.4 and range1 > 0:
        price = float(close[-1])
        atr = calculate_atr(high, low, close)
        height = range1 * 0.3
        
        if price > np.max(high[-5:]):
            sl = price - atr * 2
            tp = price + height
            return {"type": "Triangle", "side": "LONG", "entry": round(price, 2), "sl": round(sl, 2), "tp": round(tp, 2), "prob": 75}
        
        if price < np.min(low[-5:]):
            sl = price + atr * 2
            tp = price - height
            return {"type": "Triangle", "side": "SHORT", "entry": round(price, 2), "sl": round(sl, 2), "tp": round(tp, 2), "prob": 75}
    
    return None

# ========== СКАНЕР ==========
async def scan_signals():
    global active_users
    
    while True:
        try:
            now = datetime.now().strftime('%H:%M:%S')
            print(f"[{now}] 🔍 Сканирую {len(active_users)} пользователей...")
            
            found = False
            signals = []
            
            # Сканируем пары
            for symbol in PAIRS:
                data = await fetch_candles(symbol)
                if not data:
                    continue
                high, low, close, volume = data
                
                signal = detect_triangle(high, low, close)
                if signal:
                    signal["symbol"] = symbol
                    signals.append(signal)
                    found = True
            
            # Если есть сигналы — отправляем
            if found and active_users:
                for user_id in list(active_users):
                    for signal in signals:
                        msg = (f"🚨 СИГНАЛ: {signal['symbol']}\n"
                               f"📈 Тип: {signal['type']}\n"
                               f"📍 Сторона: {signal['side']}\n"
                               f"💰 Вход: {signal['entry']}\n"
                               f"🛑 SL: {signal['sl']}\n"
                               f"🎯 TP: {signal['tp']}\n"
                               f"⚡ Вероятность: {signal['prob']}%")
                        await send_tg(user_id, msg)
                        await asyncio.sleep(0.3)
            
            # Если сигналов нет — пишем об этом
            if not found and active_users:
                for user_id in list(active_users):
                    await send_tg(user_id, "😴 За 3 минуты сделок нет. Сканирую дальше...")
            
            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"❌ Ошибка сканера: {e}")
            await asyncio.sleep(30)

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

# ========== TELEGRAM ==========
async def polling_loop():
    global offset
    load_users()
    
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
                                await send_tg(chat_id, "✅ Бот запущен! Каждые 3 минуты сканирую рынок.\n\n📊 Пары: BTC, ETH, BNB, SOL, XRP\n🔍 Паттерн: Triangle\n📈 Если сделок нет — пишу об этом.")
                                print(f"👤 Новый пользователь: {chat_id}")
            
            await asyncio.sleep(2)
        except Exception as e:
            print(f"❌ Polling error: {e}")
            await asyncio.sleep(10)

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
