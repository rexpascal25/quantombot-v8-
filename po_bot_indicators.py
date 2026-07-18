import os
import re
import asyncio
import logging
import time
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telegram import Bot

# ── Try new library first, fall back to old ───────────────────
try:
    from cmp_server_pocket_option_2026.client import PocketOptionClient
    USE_NEW_LIB = True
    logger_init = logging.getLogger(__name__)
    logger_init.info("✅ Using CMP library")
except ImportError:
    from pocketoptionapi_async import AsyncPocketOptionClient, OrderDirection
    USE_NEW_LIB = False
    logger_init = logging.getLogger(__name__)
    logger_init.info("✅ Using pocketoptionapi_async library")

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ── Environment Variables ──────────────────────────────────────
API_ID             = int(os.environ.get('API_ID', 0))
API_HASH           = os.environ.get('API_HASH', '')
SESSION_STRING     = os.environ.get('SESSION_STRING_2', '')
DEST_GROUP         = os.environ.get('DEST_GROUP', '')
PO_SSID            = os.environ.get('PO_SSID', '')
DEMO               = os.environ.get('DEMO', 'True') == 'True'
BASE_AMOUNT        = float(os.environ.get('BASE_AMOUNT', '1'))
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_USER_ID   = int(os.environ.get('TELEGRAM_USER_ID', 0))
PORT               = int(os.environ.get('PORT', 8080))

# ── Timezone ───────────────────────────────────────────────────
UTC_MINUS_4 = timezone(timedelta(hours=-4))

# ── Constants ──────────────────────────────────────────────────
DEFAULT_EXPIRY    = 2
MARTINGALE_LEVELS = [1, 2, 4]

# ── Global State ───────────────────────────────────────────────
is_trading         = False
direction_change   = None
po_client          = None
keep_alive_started = False
demo_mode          = DEMO

# ── Trade Stats ────────────────────────────────────────────────
trade_stats = {
    'total':  0,
    'wins':   0,
    'losses': 0,
    'profit': 0.0
}

# ── Telegram Bot ───────────────────────────────────────────────
tg_bot = Bot(token=TELEGRAM_BOT_TOKEN)

async def notify(message):
    try:
        await tg_bot.send_message(
            chat_id=TELEGRAM_USER_ID,
            text=message,
            parse_mode='HTML'
        )
        logger.info(f"📱 Sent: {message[:50]}")
    except Exception as e:
        logger.error(f"Notify error: {e}")

# ── Keep Alive Server ──────────────────────────────────────────
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"PO Bot is alive!")
    def log_message(self, format, *args):
        pass

def start_keep_alive():
    global keep_alive_started
    if keep_alive_started:
        return
    try:
        server = HTTPServer(('0.0.0.0', PORT), KeepAliveHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        keep_alive_started = True
        logger.info(f"✅ Keep alive server on port {PORT}")
    except Exception as e:
        logger.warning(f"Keep alive error: {e}")

# ── Ignore Keywords ────────────────────────────────────────────
IGNORE_KEYWORDS = [
    'isaac godwin', '@isaacgodwiin',
    'one on one training', 'one on one',
    'contact me', 'limited slots',
    'account management', 'earn daily revenue',
    'pocket partner', 'training',
    'meet me', 'met me',
    'good morning', 'good evening',
    'good afternoon', 'good night',
    'we will use', 'we will still use',
    'set your timeframe', 'set timeframe',
    'win at direct', 'win at m1',
    'win at m2', 'win at m3',
    'direct win', 'win in',
    'win ✅', '✅ win',
    'loss', 'lose',
    'result update',
    'win at direct in',
]

def should_ignore(text):
    if not text:
        return True
    text_lower = text.lower()
    for keyword in IGNORE_KEYWORDS:
        if keyword.lower() in text_lower:
            return True
    return False

# ── Asset Converter (Dynamic OTC) ─────────────────────────────
def convert_asset(text):
    text = text.upper()
    match = re.search(r'[A-Z]{3}/[A-Z]{3}', text)
    if not match:
        return None
    pair = match.group(0).replace('/', '')
    return f"{pair}_otc"

# ── Direction Change Detector ──────────────────────────────────
def detect_direction_change(text):
    if not text:
        return None
    text_upper = text.upper().strip()
    match = re.search(
        r'(SELL|BUY|PUT|CALL)\s+IN\s+(\d+)\s*MIN',
        text_upper
    )
    if match:
        direction = match.group(1)
        expiry    = int(match.group(2))
        if direction in ['SELL', 'PUT']:
            return ('put', expiry)
        elif direction in ['BUY', 'CALL']:
            return ('call', expiry)
    match = re.search(r'(SELL|BUY|PUT|CALL)\s+NOW', text_upper)
    if match:
        direction = match.group(1)
        if direction in ['SELL', 'PUT']:
            return ('put', DEFAULT_EXPIRY)
        elif direction in ['BUY', 'CALL']:
            return ('call', DEFAULT_EXPIRY)
    return None

# ── Signal Parser ──────────────────────────────────────────────
def parse_signal(text):
    try:
        if not text:
            return None
        text_upper = text.upper()
        if not re.search(r'[A-Z]{3}/[A-Z]{3}', text_upper):
            return None
        signal = {
            'asset':            None,
            'direction':        None,
            'expiry':           DEFAULT_EXPIRY,
            'entry_time':       None,
            'martingale_times': []
        }
        signal['asset'] = convert_asset(text)
        if not signal['asset']:
            return None
        if 'BUY' in text_upper or 'CALL' in text_upper or '🟩' in text:
            signal['direction'] = 'call'
        elif 'SELL' in text_upper or 'PUT' in text_upper or '🟥' in text:
            signal['direction'] = 'put'
        else:
            return None
        lines = text.split('\n')
        for line in lines:
            line_upper = line.upper()
            if 'ENTRY' in line_upper:
                time_match = re.search(r'(\d{1,2}:\d{2})', line)
                if time_match:
                    signal['entry_time'] = time_match.group(1)
            if ('1️⃣' in line or
                    re.search(r'\b1\b.*LEVEL|LEVEL.*\b1\b', line_upper)):
                time_match = re.search(r'(\d{1,2}:\d{2})', line)
                if time_match and len(signal['martingale_times']) == 0:
                    signal['martingale_times'].append(time_match.group(1))
            elif ('2️⃣' in line or
                    re.search(r'\b2\b.*LEVEL|LEVEL.*\b2\b', line_upper)):
                time_match = re.search(r'(\d{1,2}:\d{2})', line)
                if time_match and len(signal['martingale_times']) <= 1:
                    signal['martingale_times'].append(time_match.group(1))
        if signal['asset'] and signal['direction']:
            return signal
        return None
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return None

# ── Wait Until Time (UTC-4) ────────────────────────────────────
def wait_until(target_time_str):
    try:
        now = datetime.now(UTC_MINUS_4)
        target = datetime.strptime(
            f"{now.strftime('%Y-%m-%d')} {target_time_str}",
            "%Y-%m-%d %H:%M"
        ).replace(tzinfo=UTC_MINUS_4)
        wait_seconds = (target - now).total_seconds()
        if wait_seconds > 0:
            logger.info(
                f"⏰ Waiting {int(wait_seconds)}s "
                f"until {target_time_str} UTC-4"
            )
            time.sleep(wait_seconds)
        return True
    except Exception as e:
        logger.error(f"Time error: {e}")
        return False

# ── Record Trade Result ────────────────────────────────────────
async def record_result(outcome, profit_amount):
    global trade_stats
    trade_stats['total'] += 1
    if outcome == 'win':
        trade_stats['wins']   += 1
        trade_stats['profit'] += profit_amount
    else:
        trade_stats['losses'] += 1
        trade_stats['profit'] -= BASE_AMOUNT

    win_rate = (
        (trade_stats['wins'] / trade_stats['total']) * 100
        if trade_stats['total'] > 0 else 0
    )

    profit       = trade_stats['profit']
    profit_emoji = '💰' if profit >= 0 else '🔴'
    profit_sign  = '+' if profit >= 0 else ''

    await notify(
        f"📊 <b>TRADE RECORD</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔢 Total Trades: {trade_stats['total']}\n"
        f"✅ Wins: {trade_stats['wins']}\n"
        f"❌ Losses: {trade_stats['losses']}\n"
        f"🎯 Win Rate: {win_rate:.1f}%\n"
        f"{profit_emoji} Total Profit: {profit_sign}${profit:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

# ── Connect To Pocket Option ───────────────────────────────────
async def connect_pocket_option():
    global po_client, demo_mode
    try:
        if not PO_SSID:
            logger.error("❌ PO_SSID not set!")
            await notify("❌ <b>PO_SSID missing!</b>")
            return False

        logger.info("🔌 Connecting to Pocket Option...")

        if USE_NEW_LIB:
            po_client = PocketOptionClient(
                ssid=PO_SSID,
                demo=demo_mode
            )
        else:
            po_client = AsyncPocketOptionClient(
                PO_SSID, is_demo=demo_mode
            )

        for attempt in range(5):
            try:
                await po_client.connect()
                await asyncio.sleep(5)
                balance = await po_client.get_balance()
                logger.info(f"✅ Connected! Balance: {balance}")
                await notify(
                    f"✅ <b>Pocket Option Connected!</b>\n"
                    f"Mode: {'DEMO' if demo_mode else 'REAL'}\n"
                    f"Balance: {balance}\n"
                    f"Base Amount: ${BASE_AMOUNT}"
                )
                return True
            except Exception as e:
                logger.warning(f"Attempt {attempt+1}/5 failed: {e}")
                await asyncio.sleep(10)
                continue

        await notify("❌ <b>PO Connection Failed!</b>")
        return False

    except Exception as e:
        logger.error(f"PO error: {e}")
        await notify(f"❌ <b>PO error:</b>\n{e}")
        return False

# ── Reconnect Pocket Option ────────────────────────────────────
async def reconnect_pocket_option():
    global po_client, demo_mode
    await notify("⚠️ <b>PO Connection Lost!</b>\nReconnecting...")
    for attempt in range(10):
        try:
            if USE_NEW_LIB:
                po_client = PocketOptionClient(
                    ssid=PO_SSID, demo=demo_mode
                )
            else:
                po_client = AsyncPocketOptionClient(
                    PO_SSID, is_demo=demo_mode
                )
            await po_client.connect()
            await asyncio.sleep(5)
            balance = await po_client.get_balance()
            await notify(
                f"✅ <b>PO Reconnected!</b>\n"
                f"Balance: {balance}\n"
                f"Ready to trade! 👀"
            )
            return True
        except Exception as e:
            logger.warning(f"Reconnect {attempt+1} failed: {e}")
            await asyncio.sleep(15)
    await notify("❌ <b>Reconnection Failed!</b>\nRestarting in 30s...")
    return False

# ── Place Trade ────────────────────────────────────────────────
async def place_trade_async(asset, direction, amount, expiry_minutes):
    try:
        if USE_NEW_LIB:
            result = await po_client.place_trade(
                asset=asset,
                amount=amount,
                direction=direction,
                expiration=expiry_minutes * 60
            )
            if result:
                logger.info(
                    f"✅ Trade: {direction.upper()} "
                    f"{asset} ${amount} {expiry_minutes}min"
                )
                return result.get('id') or result.get('trade_id')
        else:
            from pocketoptionapi_async import OrderDirection
            order_direction = (
                OrderDirection.CALL
                if direction == 'call'
                else OrderDirection.PUT
            )
            order = await po_client.place_order(
                asset=asset,
                amount=amount,
                direction=order_direction,
                duration=expiry_minutes * 60
            )
            if order:
                logger.info(
                    f"✅ Trade: {direction.upper()} "
                    f"{asset} ${amount} {expiry_minutes}min"
                )
                return order.order_id
        logger.error("❌ Trade failed")
        return None
    except Exception as e:
        logger.error(f"Trade error: {e}")
        await reconnect_pocket_option()
        return None

# ── Check Result ───────────────────────────────────────────────
async def check_result_async(trade_id, expiry_minutes):
    try:
        wait_time = expiry_minutes * 60 + 5
        logger.info(f"⏳ Waiting {wait_time}s for result...")
        await asyncio.sleep(wait_time)

        if USE_NEW_LIB:
            result = await po_client.check_result(trade_id)
            if result:
                profit = result.get('profit', 0)
                if profit > 0:
                    logger.info(f"🎉 WIN! +${profit:.2f}")
                    return 'win', profit
        else:
            result = await po_client.get_order_result(trade_id)
            if result and result.profit > 0:
                logger.info(f"🎉 WIN! +${result.profit:.2f}")
                return 'win', result.profit

        logger.info("❌ LOSS!")
        return 'loss', 0
    except Exception as e:
        logger.error(f"Result error: {e}")
        return 'loss', 0

# ── Execute Signal ─────────────────────────────────────────────
async def execute_signal(signal):
    global is_trading, direction_change

    is_trading       = True
    direction_change = None

    asset      = signal['asset']
    direction  = signal['direction']
    expiry     = signal['expiry']
    entry_time = signal['entry_time']
    mg_times   = signal['martingale_times']

    now_utc4 = datetime.now(UTC_MINUS_4).strftime('%H:%M')

    await notify(
        f"🔔 <b>NEW SIGNAL!</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Asset: {asset}\n"
        f"Direction: {direction.upper()}\n"
        f"Entry: {entry_time} UTC-4\n"
        f"Expiry: {expiry} min\n"
        f"Amount: ${BASE_AMOUNT}\n"
        f"M1: {mg_times[0] if len(mg_times) > 0 else 'N/A'}\n"
        f"M2: {mg_times[1] if len(mg_times) > 1 else 'N/A'}\n"
        f"Current UTC-4: {now_utc4}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

    if entry_time:
        await notify(f"⏰ Waiting until {entry_time} UTC-4...")
        wait_until(entry_time)

    amount   = BASE_AMOUNT * MARTINGALE_LEVELS[0]
    trade_id = await place_trade_async(
        asset, direction, amount, expiry
    )

    if not trade_id:
        await notify("❌ Trade failed. Waiting for next signal.")
        is_trading = False
        return

    await notify(
        f"✅ <b>ENTRY PLACED!</b>\n"
        f"{direction.upper()} {asset}\n"
        f"Amount: ${amount:.2f} | Expiry: {expiry}min"
    )

    result, profit = await check_result_async(trade_id, expiry)

    if result == 'win':
        await notify(
            f"🎉 <b>WIN ON ENTRY!</b>\n"
            f"Profit: +${profit:.2f}\n"
            f"Waiting for next signal..."
        )
        await record_result('win', profit)
        is_trading = False
        return

    await notify(
        f"❌ <b>LOSS ON ENTRY</b>\n"
        f"Going to M1...\n"
        f"Amount: ${BASE_AMOUNT * MARTINGALE_LEVELS[1]:.2f}"
    )
    await record_result('loss', 0)

    if len(mg_times) >= 1:
        wait_until(mg_times[0])
        amount   = BASE_AMOUNT * MARTINGALE_LEVELS[1]
        trade_id = await place_trade_async(
            asset, direction, amount, expiry
        )
        if trade_id:
            await notify(
                f"📈 <b>M1 PLACED!</b>\n"
                f"{direction.upper()} {asset}\n"
                f"Amount: ${amount:.2f} | Expiry: {expiry}min"
            )
            result, profit = await check_result_async(
                trade_id, expiry
            )
            if result == 'win':
                await notify(
                    f"🎉 <b>WIN ON M1!</b>\n"
                    f"Profit: +${profit:.2f}\n"
                    f"Waiting for next signal..."
                )
                await record_result('win', profit)
                is_trading = False
                return
            await notify(
                f"❌ <b>LOSS ON M1</b>\n"
                f"Going to M2...\n"
                f"Amount: ${BASE_AMOUNT * MARTINGALE_LEVELS[2]:.2f}"
            )
            await record_result('loss', 0)

    if len(mg_times) >= 2:
        wait_until(mg_times[1])
        amount   = BASE_AMOUNT * MARTINGALE_LEVELS[2]
        trade_id = await place_trade_async(
            asset, direction, amount, expiry
        )
        if trade_id:
            await notify(
                f"📈 <b>M2 PLACED!</b>\n"
                f"{direction.upper()} {asset}\n"
                f"Amount: ${amount:.2f} | Expiry: {expiry}min"
            )
            result, profit = await check_result_async(
                trade_id, expiry
            )
            if result == 'win':
                await notify(
                    f"🎉 <b>WIN ON M2!</b>\n"
                    f"Profit: +${profit:.2f}"
                )
                await record_result('win', profit)
            else:
                await notify(
                    f"❌ <b>LOSS ON M2</b>\n"
                    f"Reset to ${BASE_AMOUNT}\n"
                    f"Waiting for next signal..."
                )
                await record_result('loss', 0)

    direction_change = None
    is_trading       = False

# ── Connection Health Monitor ──────────────────────────────────
async def connection_monitor():
    while True:
        await asyncio.sleep(60)
        try:
            await po_client.get_balance()
            logger.info("💓 PO Connection healthy")
        except Exception as e:
            logger.warning(f"⚠️ PO Connection lost: {e}")
            await reconnect_pocket_option()

# ── Main ───────────────────────────────────────────────────────
async def main():
    global is_trading

    logger.info("🚀 Starting PO Signal Bot...")
    logger.info(f"Mode: {'DEMO' if demo_mode else 'REAL'}")
    logger.info(f"Base amount: ${BASE_AMOUNT}")
    logger.info(f"Library: {'CMP' if USE_NEW_LIB else 'pocketoptionapi_async'}")

    start_keep_alive()

    po_connected = await connect_pocket_option()
    if not po_connected:
        return

    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH,
        device_model="Linux",
        system_version="Ubuntu 20.04",
        app_version="1.0"
    )

    await client.connect()

    if not await client.is_user_authorized():
        logger.error("❌ SESSION_STRING_2 invalid!")
        await notify("❌ SESSION_STRING_2 invalid!")
        return

    me = await client.get_me()
    logger.info(f"✅ Telegram: {me.first_name}")

    dest_entity = await client.get_entity(int(DEST_GROUP))
    logger.info(f"✅ Listening: {dest_entity.title}")

    await notify(
        f"🤖 <b>PO BOT IS LIVE!</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Mode: {'🔵 DEMO' if demo_mode else '🔴 REAL'}\n"
        f"Base: ${BASE_AMOUNT}\n"
        f"Martingale: ${BASE_AMOUNT}→"
        f"${BASE_AMOUNT*2}→${BASE_AMOUNT*4}\n"
        f"Timezone: UTC-4\n"
        f"Group: {dest_entity.title}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Waiting for signals... 👀"
    )

    asyncio.create_task(connection_monitor())

    @client.on(events.NewMessage(chats=dest_entity))
    async def handler(event):
        global direction_change, is_trading

        text = event.message.text or ''
        if not text:
            return

        logger.info(f"📨 {text[:50].replace(chr(10), ' ')}")

        dir_change = detect_direction_change(text)
        if dir_change and is_trading:
            direction_change = dir_change
            new_dir, new_expiry = dir_change
            await notify(
                f"⚡ <b>DIRECTION CHANGE!</b>\n"
                f"{new_dir.upper()} in {new_expiry}min"
            )
            return

        if should_ignore(text):
            logger.info("🚫 Ignored")
            return

        signal = parse_signal(text)

        if signal and not is_trading:
            logger.info(
                f"✅ Signal: {signal['asset']} "
                f"{signal['direction']}"
            )
            asyncio.create_task(execute_signal(signal))
        elif signal and is_trading:
            await notify(
                f"⚠️ New signal skipped\n"
                f"Bot currently trading\n"
                f"{signal['asset']} {signal['direction']}"
            )
        else:
            logger.info("⏭ Not a signal")

    async def keepalive():
        while True:
            await asyncio.sleep(240)
            try:
                await client.get_me()
                logger.info("💓 Keepalive OK")
            except Exception as e:
                logger.warning(f"⚠️ Keepalive error: {e}")

    asyncio.create_task(keepalive())
    await client.run_until_disconnected()

# ── Auto Restart ───────────────────────────────────────────────
async def run():
    while True:
        try:
            await main()
        except Exception as e:
            logger.error(f"💥 Crashed: {e}")
            try:
                await notify(
                    f"💥 Bot crashed: {e}\n"
                    f"🔄 Restarting in 30s..."
                )
            except:
                pass
        logger.warning("🔄 Restarting in 30s...")
        await asyncio.sleep(30)

if __name__ == '__main__':
    asyncio.run(run())
