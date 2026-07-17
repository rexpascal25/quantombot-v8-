# ============================================================
# QuantomBot V8 — Dual Engine Edition
# Uses pocket-option PyPI library (Python 3.13+)
# ============================================================

import os, re, json, asyncio, logging, threading, time
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession
import telebot
import requests as _req
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ── Delete webhook to prevent 409 ─────────────────────────────
try:
    _TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    _req.get(f"https://api.telegram.org/bot{_TOKEN}/deleteWebhook?drop_pending_updates=true", timeout=10)
    _req.get(f"https://api.telegram.org/bot{_TOKEN}/close", timeout=5)
except: pass
time.sleep(3)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── Env Variables ──────────────────────────────────────────────
API_ID             = int(os.environ.get('API_ID', 0))
API_HASH           = os.environ.get('API_HASH', '')
SESSION_STRING     = os.environ.get('SESSION_STRING_2', '')
DEST_GROUP         = os.environ.get('DEST_GROUP', '')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_USER_ID   = int(os.environ.get('TELEGRAM_USER_ID', 0))
PORT               = int(os.environ.get('PORT', 8080))
RAILWAY_SSID       = os.environ.get('PO_SSID', '')
PO_UID             = int(os.environ.get('PO_UID', 0))
PO_EMAIL           = os.environ.get('PO_EMAIL', '')
PO_PASSWORD        = os.environ.get('PO_PASSWORD', '')
CAPTCHA_KEY        = os.environ.get('CAPTCHA_KEY', '')

UTC_MINUS_4        = timezone(timedelta(hours=-4))
DEFAULT_EXPIRY     = 2
USERS_FILE         = 'users.json'

# ── Bot Instance ───────────────────────────────────────────────
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False, num_threads=1)

# ── User Storage ───────────────────────────────────────────────
def load_users():
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r') as f:
                return json.load(f)
    except: pass
    return {}

def save_users(users):
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f, indent=2)
    except: pass

users        = load_users()
user_clients = {}
user_trading = {}
user_state   = {}
auto_loop    = None
auto_client  = None
auto_trading = False
auto_connected = False

def get_user(uid):
    uid = str(uid)
    if uid not in users:
        users[uid] = {
            'ssid': '', 'uid': 0, 'is_demo': True, 'amount': 1.0,
            'stats': {'total':0,'wins':0,'losses':0,'profit':0.0},
            'auto_stats': {'total':0,'wins':0,'losses':0,'profit':0.0}
        }
        save_users(users)
    return users[uid]

# ── Keep Alive ─────────────────────────────────────────────────
keep_alive_started = False
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"QuantomBot V8 alive!")
    def log_message(self, *args): pass

def start_keep_alive():
    global keep_alive_started
    if keep_alive_started: return
    try:
        s = HTTPServer(('0.0.0.0', PORT), KeepAliveHandler)
        t = threading.Thread(target=s.serve_forever)
        t.daemon = True
        t.start()
        keep_alive_started = True
        logger.info(f"✅ Keep alive on port {PORT}")
    except Exception as e:
        logger.warning(f"Keep alive error: {e}")

# ── Main Menu ──────────────────────────────────────────────────
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("📊 Dashboard"),
        KeyboardButton("🔑 My Login"),
        KeyboardButton("⚙️ Settings"),
        KeyboardButton("📈 My Stats"),
        KeyboardButton("🤖 Auto Signal ON"),
        KeyboardButton("🛑 Auto Signal OFF"),
        KeyboardButton("👤 Manual Trade"),
        KeyboardButton("💰 Balance"),
        KeyboardButton("❓ Help")
    )
    return markup

# ── Auto Login ─────────────────────────────────────────────────
def get_ssid_via_login():
    try:
        from po_login import auto_login_and_get_ssid
        return auto_login_and_get_ssid()
    except Exception as e:
        logger.error(f"Auto login error: {e}")
        return None

# ── Connect Auto Engine ────────────────────────────────────────
def connect_auto_engine():
    global auto_client, auto_connected, auto_loop, RAILWAY_SSID, PO_UID

    if not RAILWAY_SSID and PO_EMAIL and PO_PASSWORD:
        logger.info("No SSID — trying auto login...")
        bot.send_message(TELEGRAM_USER_ID, "🔐 Auto logging in to Pocket Option...")
        result = get_ssid_via_login()
        if result:
            if isinstance(result, dict):
                RAILWAY_SSID = result.get('session', '')
                PO_UID       = result.get('uid', 0)
            else:
                RAILWAY_SSID = result
        if not RAILWAY_SSID:
            bot.send_message(TELEGRAM_USER_ID, "❌ Auto login failed! Add PO_SSID to Railway.")
            return False

    if not RAILWAY_SSID:
        bot.send_message(TELEGRAM_USER_ID, "❌ No PO_SSID in Railway variables!")
        return False

    async def do_connect():
        global auto_client, auto_connected
        try:
            from pocket_option import PocketOptionClient
            from pocket_option.constants import Regions
            from pocket_option.models import AuthorizationData
            from pocket_option.contrib.deals import MemoryDealsStorage

            client = PocketOptionClient()

            connected_event = asyncio.Event()
            auth_done_event = asyncio.Event()
            balance_val     = [0.0]

            @client.on.connect
            async def on_connect(data):
                logger.info("✅ WS Connected! Sending auth...")
                await client.emit.auth(
                    AuthorizationData.model_validate({
                        "session":       RAILWAY_SSID,
                        "isDemo":        1,
                        "uid":           PO_UID,
                        "platform":      2,
                        "isFastHistory": True,
                        "isOptimized":   True,
                    })
                )
                connected_event.set()

            @client.on.success_auth
            async def on_auth(data):
                logger.info(f"✅ Authenticated! ID: {data.id}")
                auth_done_event.set()

            @client.on.balance
            async def on_balance(data):
                try:
                    balance_val[0] = float(data.balance)
                except: pass

            await client.connect(Regions.DEMO)

            # Wait for connection and auth
            await asyncio.wait_for(connected_event.wait(), timeout=30)
            await asyncio.wait_for(auth_done_event.wait(), timeout=30)

            auto_client    = client
            auto_connected = True

            await asyncio.sleep(3)
            bal = balance_val[0]
            logger.info(f"✅ Auto Engine ready! Balance: ${bal:.2f}")
            bot.send_message(
                TELEGRAM_USER_ID,
                f"🤖 <b>Auto Engine Ready!</b>\n"
                f"Balance: ${bal:.2f} (DEMO)\n"
                f"Click 🤖 <b>Auto Signal ON</b> to start!",
                parse_mode='HTML'
            )

            # Keep running
            await asyncio.sleep(86400)

        except asyncio.TimeoutError:
            logger.error("Connection timeout!")
            bot.send_message(TELEGRAM_USER_ID, "❌ Connection timeout! Check PO_SSID and PO_UID.")
        except Exception as e:
            logger.error(f"Auto engine error: {e}")
            bot.send_message(TELEGRAM_USER_ID, f"❌ Engine error: {str(e)[:100]}")

    loop = asyncio.new_event_loop()
    auto_loop = loop
    loop.run_until_complete(do_connect())
    loop.close()

# ── Place Trade (Auto Engine) ──────────────────────────────────
async def auto_place_trade(asset, direction, amount, expiry):
    if not auto_client or not auto_connected:
        return None, None
    try:
        from pocket_option.contrib.deals import MemoryDealsStorage
        from pocket_option.models import DealAction, Asset as PAsset
        from pocket_option.contrib.deals import MemoryDealsStorage

        deals  = MemoryDealsStorage(auto_client)
        action = DealAction.CALL if direction == 'call' else DealAction.PUT

        deal = await deals.open_deal(
            asset       = asset,
            amount      = amount,
            action      = action,
            is_demo     = 1,
            option_type = 100,
            time        = expiry * 60,
        )
        result = await deals.check_deal_result(wait_time=expiry * 60 + 10, deal=deal)
        if result and result.profit > 0:
            return 'win', result.profit
        return 'loss', 0
    except Exception as e:
        logger.error(f"Auto trade error: {e}")
        return None, 0

# ── Connect Personal ───────────────────────────────────────────
def connect_personal(uid):
    uid  = str(uid)
    user = get_user(uid)
    ssid = user.get('ssid', '')
    p_uid = user.get('uid', 0)
    if not ssid:
        bot.send_message(int(uid), "❌ No SSID! Use 🔑 My Login first.")
        return

    async def do_connect():
        try:
            from pocket_option import PocketOptionClient
            from pocket_option.constants import Regions
            from pocket_option.models import AuthorizationData

            client          = PocketOptionClient()
            auth_done       = asyncio.Event()
            balance_val     = [0.0]

            @client.on.connect
            async def on_connect(data):
                await client.emit.auth(
                    AuthorizationData.model_validate({
                        "session":       ssid,
                        "isDemo":        1 if user.get('is_demo', True) else 0,
                        "uid":           p_uid,
                        "platform":      2,
                        "isFastHistory": True,
                        "isOptimized":   True,
                    })
                )

            @client.on.success_auth
            async def on_auth(data):
                auth_done.set()

            @client.on.balance
            async def on_balance(data):
                try: balance_val[0] = float(data.balance)
                except: pass

            await client.connect(Regions.DEMO if user.get('is_demo', True) else Regions.REAL)
            await asyncio.wait_for(auth_done.wait(), timeout=30)

            user_clients[uid] = client
            await asyncio.sleep(3)
            bal  = balance_val[0]
            mode = "🔵 DEMO" if user.get('is_demo', True) else "🔴 REAL"
            bot.send_message(
                int(uid),
                f"✅ <b>Personal Account Connected!</b>\n"
                f"Mode: {mode}\n"
                f"Balance: ${bal:.2f}\n"
                f"Use 👤 Manual Trade!",
                parse_mode='HTML'
            )
            await asyncio.sleep(86400)
        except Exception as e:
            bot.send_message(int(uid), f"❌ Error: {str(e)[:100]}")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(do_connect())
    loop.close()

# ── /start ─────────────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid  = str(message.from_user.id)
    name = message.from_user.first_name or "Trader"
    get_user(uid)
    user_state.pop(uid, None)
    bot.send_message(
        message.chat.id,
        f"👋 Welcome <b>{name}</b> to QuantomBot V8!\n\n"
        f"🤖 <b>DUAL ENGINE BOT</b>\n\n"
        f"<b>Engine 1 — Auto Signal:</b>\n"
        f"▸ Reads Rex Signal Alerts\n"
        f"▸ Click 🤖 Auto Signal ON\n\n"
        f"<b>Engine 2 — Manual:</b>\n"
        f"▸ Login with 🔑 My Login\n"
        f"▸ Trade with 👤 Manual Trade\n\n"
        f"Both engines are independent! 🚀",
        parse_mode='HTML',
        reply_markup=main_menu()
    )

@bot.message_handler(func=lambda m: m.text == "🤖 Auto Signal ON")
def cmd_auto_on(message):
    global auto_trading
    if not auto_connected:
        bot.send_message(message.chat.id, "⏳ Connecting auto engine...")
        threading.Thread(target=connect_auto_engine, daemon=True).start()
        return
    auto_trading = True
    bot.send_message(message.chat.id, "🤖 <b>Auto Signal ON!</b>\n✅ Watching Rex Signal Alerts", parse_mode='HTML')

@bot.message_handler(func=lambda m: m.text == "🛑 Auto Signal OFF")
def cmd_auto_off(message):
    global auto_trading
    auto_trading = False
    bot.send_message(message.chat.id, "🛑 <b>Auto Signal OFF!</b>", parse_mode='HTML')

@bot.message_handler(func=lambda m: m.text == "🔑 My Login")
def cmd_login(message):
    uid = str(message.from_user.id)
    user_state[uid] = 'wait_ssid'
    bot.send_message(
        message.chat.id,
        "🔑 <b>Personal Login</b>\n\n"
        "Paste your PO SSID:\n"
        "Format: session:uid\n"
        "Example: abc123:27658142\n\n"
        "Or just paste the session ID alone.",
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda m: m.text == "👤 Manual Trade")
def cmd_manual(message):
    uid = str(message.from_user.id)
    if uid not in user_clients:
        bot.send_message(message.chat.id, "❌ Use 🔑 My Login first!")
        return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("EUR/USD OTC",  callback_data="asset_EURUSD_otc"),
        InlineKeyboardButton("GBP/USD OTC",  callback_data="asset_GBPUSD_otc"),
        InlineKeyboardButton("USD/JPY OTC",  callback_data="asset_USDJPY_otc"),
        InlineKeyboardButton("AUD/USD OTC",  callback_data="asset_AUDUSD_otc"),
        InlineKeyboardButton("NGN/USD OTC",  callback_data="asset_NGNUSD_otc"),
        InlineKeyboardButton("EUR/GBP OTC",  callback_data="asset_EURGBP_otc"),
    )
    bot.send_message(message.chat.id, "👤 Choose Asset:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("asset_"))
def cb_asset(call):
    uid = str(call.from_user.id)
    bot.answer_callback_query(call.id)
    get_user(uid)['_temp_asset'] = call.data.replace("asset_", "")
    save_users(users)
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🟢 BUY", callback_data="dir_call"),
        InlineKeyboardButton("🔴 SELL", callback_data="dir_put"),
    )
    bot.send_message(call.message.chat.id, "Choose Direction:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("dir_"))
def cb_dir(call):
    uid = str(call.from_user.id)
    bot.answer_callback_query(call.id)
    get_user(uid)['_temp_dir'] = call.data.replace("dir_", "")
    save_users(users)
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("1 min", callback_data="exp_1"),
        InlineKeyboardButton("2 min", callback_data="exp_2"),
        InlineKeyboardButton("5 min", callback_data="exp_5"),
    )
    bot.send_message(call.message.chat.id, "Choose Expiry:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("exp_"))
def cb_exp(call):
    uid    = str(call.from_user.id)
    expiry = int(call.data.replace("exp_", ""))
    bot.answer_callback_query(call.id)
    user      = get_user(uid)
    asset     = user.get('_temp_asset', 'EURUSD_otc')
    direction = user.get('_temp_dir', 'call')
    amount    = user.get('amount', 1.0)
    client    = user_clients.get(uid)
    if not client:
        bot.send_message(call.message.chat.id, "❌ Not connected!")
        return
    bot.send_message(call.message.chat.id, f"⏳ Placing trade...")
    threading.Thread(
        target=manual_trade_thread,
        args=(uid, asset, direction, amount, expiry),
        daemon=True
    ).start()

def manual_trade_thread(uid, asset, direction, amount, expiry):
    loop = asyncio.new_event_loop()
    async def run():
        try:
            from pocket_option.contrib.deals import MemoryDealsStorage
            from pocket_option.models import DealAction
            client = user_clients.get(str(uid))
            if not client: return
            deals  = MemoryDealsStorage(client)
            action = DealAction.CALL if direction == 'call' else DealAction.PUT
            deal   = await deals.open_deal(
                asset=asset, amount=amount, action=action,
                is_demo=1, option_type=100, time=expiry*60
            )
            bot.send_message(int(uid), f"✅ Trade placed! Waiting {expiry}min...")
            result = await deals.check_deal_result(wait_time=expiry*60+10, deal=deal)
            if result and result.profit > 0:
                bot.send_message(int(uid), f"🎉 <b>WIN!</b> +${result.profit:.2f}", parse_mode='HTML')
            else:
                bot.send_message(int(uid), f"❌ <b>LOSS</b> -${amount:.2f}", parse_mode='HTML')
        except Exception as e:
            bot.send_message(int(uid), f"❌ Trade error: {e}")
    loop.run_until_complete(run())
    loop.close()

@bot.message_handler(func=lambda m: m.text == "📊 Dashboard")
def cmd_dashboard(message):
    uid        = str(message.from_user.id)
    user       = get_user(uid)
    auto_stats = user.get('auto_stats', {'total':0,'wins':0,'losses':0,'profit':0.0})
    my_stats   = user.get('stats', {'total':0,'wins':0,'losses':0,'profit':0.0})
    auto_wr    = (auto_stats['wins']/auto_stats['total']*100) if auto_stats['total'] > 0 else 0
    my_wr      = (my_stats['wins']/my_stats['total']*100) if my_stats['total'] > 0 else 0
    bot.send_message(
        message.chat.id,
        f"📊 <b>DASHBOARD</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Auto: {'🟢 ON' if auto_trading else '🔴 OFF'} | {'✅' if auto_connected else '❌'}\n"
        f"Trades: {auto_stats['total']} ✅{auto_stats['wins']} ❌{auto_stats['losses']} | {auto_wr:.1f}%\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 Personal: {'✅' if uid in user_clients else '❌'}\n"
        f"Amount: ${user.get('amount',1.0)}\n"
        f"Trades: {my_stats['total']} ✅{my_stats['wins']} ❌{my_stats['losses']} | {my_wr:.1f}%\n"
        f"━━━━━━━━━━━━━━━━━━",
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda m: m.text == "💰 Balance")
def cmd_balance(message):
    bot.send_message(message.chat.id,
        "💰 Balance checking...\n"
        "Use 📊 Dashboard to see stats."
    )

@bot.message_handler(func=lambda m: m.text == "⚙️ Settings")
def cmd_settings(message):
    uid  = str(message.from_user.id)
    user = get_user(uid)
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔵 DEMO", callback_data="set_demo"),
        InlineKeyboardButton("🔴 REAL", callback_data="set_real"),
        InlineKeyboardButton("$1",      callback_data="set_amt_1.0"),
        InlineKeyboardButton("$2",      callback_data="set_amt_2.0"),
        InlineKeyboardButton("$5",      callback_data="set_amt_5.0"),
        InlineKeyboardButton("💵 Custom", callback_data="set_amt_custom")
    )
    bot.send_message(message.chat.id,
        f"⚙️ Mode: {'🔵 DEMO' if user.get('is_demo',True) else '🔴 REAL'}\n"
        f"Amount: ${user.get('amount',1.0)}",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith('set_'))
def cb_settings(call):
    uid  = str(call.from_user.id)
    user = get_user(uid)
    bot.answer_callback_query(call.id)
    if call.data == "set_demo":
        user['is_demo'] = True; save_users(users)
        bot.send_message(call.message.chat.id, "✅ DEMO mode!")
    elif call.data == "set_real":
        user['is_demo'] = False; save_users(users)
        bot.send_message(call.message.chat.id, "✅ REAL mode!")
    elif call.data == "set_amt_custom":
        user_state[uid] = 'wait_amount'
        bot.send_message(call.message.chat.id, "Enter amount:")
    elif call.data.startswith("set_amt_"):
        amt = float(call.data.replace("set_amt_",""))
        user['amount'] = amt; save_users(users)
        bot.send_message(call.message.chat.id, f"✅ ${amt:.2f}")

@bot.message_handler(func=lambda m: m.text == "📈 My Stats")
def cmd_stats(message):
    uid        = str(message.from_user.id)
    user       = get_user(uid)
    auto_stats = user.get('auto_stats', {'total':0,'wins':0,'losses':0,'profit':0.0})
    my_stats   = user.get('stats', {'total':0,'wins':0,'losses':0,'profit':0.0})
    bot.send_message(
        message.chat.id,
        f"📈 <b>STATS</b>\n"
        f"🤖 Auto: {auto_stats['total']} | ${auto_stats['profit']:.2f}\n"
        f"👤 Manual: {my_stats['total']} | ${my_stats['profit']:.2f}",
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda m: m.text == "❓ Help")
def cmd_help(message):
    bot.send_message(message.chat.id,
        "❓ <b>HOW TO USE</b>\n"
        "🤖 Auto Signal ON/OFF\n"
        "🔑 My Login — personal account\n"
        "👤 Manual Trade — place trades\n"
        "📊 Dashboard — see stats\n"
        "⚙️ Settings — configure",
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    uid   = str(message.from_user.id)
    text  = message.text.strip()
    state = user_state.get(uid)

    if state == 'wait_ssid':
        user_state.pop(uid, None)
        user = get_user(uid)
        # Support format "session:uid"
        if ':' in text and text.split(':')[-1].isdigit():
            parts        = text.rsplit(':', 1)
            user['ssid'] = parts[0]
            user['uid']  = int(parts[1])
        else:
            user['ssid'] = text
            user['uid']  = 0
        save_users(users)
        bot.send_message(message.chat.id, "✅ SSID saved! Connecting...")
        threading.Thread(target=connect_personal, args=(uid,), daemon=True).start()

    elif state == 'wait_amount':
        user_state.pop(uid, None)
        try:
            amt = float(text)
            if amt < 1:
                bot.send_message(message.chat.id, "❌ Min $1")
                return
            get_user(uid)['amount'] = amt
            save_users(users)
            bot.send_message(message.chat.id, f"✅ ${amt:.2f}")
        except:
            bot.send_message(message.chat.id, "❌ Invalid")

# ── Auto Stats ─────────────────────────────────────────────────
def update_auto_stats(outcome, profit, amount):
    user  = get_user(str(TELEGRAM_USER_ID))
    stats = user.get('auto_stats', {'total':0,'wins':0,'losses':0,'profit':0.0})
    stats['total'] += 1
    if outcome == 'win':
        stats['wins']   += 1
        stats['profit'] += profit
    else:
        stats['losses'] += 1
        stats['profit'] -= amount
    user['auto_stats'] = stats
    save_users(users)
    wr = (stats['wins']/stats['total']*100) if stats['total'] > 0 else 0
    bot.send_message(
        TELEGRAM_USER_ID,
        f"📊 {stats['total']} trades | {wr:.1f}% | ${stats['profit']:.2f}"
    )

# ── Signal Parse ───────────────────────────────────────────────
IGNORE_KEYWORDS = [
    'isaac godwin','one on one','contact me','limited slots',
    'account management','earn daily','training','good morning',
    'good evening','good afternoon','good night','we will use',
    'set your timeframe','result update','win ✅','✅ win',
    'loss','lose','direct win',
]

def should_ignore(text):
    if not text: return True
    return any(k in text.lower() for k in IGNORE_KEYWORDS)

def convert_asset(text):
    m = re.search(r'[A-Z]{3}/[A-Z]{3}', text.upper())
    return m.group(0).replace('/', '') + '_otc' if m else None

def parse_signal(text):
    try:
        if not text or not re.search(r'[A-Z]{3}/[A-Z]{3}', text.upper()):
            return None
        sig = {'asset':None,'direction':None,'expiry':DEFAULT_EXPIRY,
               'entry_time':None,'martingale_times':[]}
        sig['asset'] = convert_asset(text)
        if not sig['asset']: return None
        tu = text.upper()
        if 'BUY' in tu or 'CALL' in tu or '🟩' in text:
            sig['direction'] = 'call'
        elif 'SELL' in tu or 'PUT' in tu or '🟥' in text:
            sig['direction'] = 'put'
        else: return None
        for line in text.split('\n'):
            lu = line.upper()
            if 'ENTRY' in lu:
                m = re.search(r'(\d{1,2}:\d{2})', line)
                if m: sig['entry_time'] = m.group(1)
            if '1️⃣' in line:
                m = re.search(r'(\d{1,2}:\d{2})', line)
                if m and len(sig['martingale_times'])==0:
                    sig['martingale_times'].append(m.group(1))
            elif '2️⃣' in line:
                m = re.search(r'(\d{1,2}:\d{2})', line)
                if m and len(sig['martingale_times'])<=1:
                    sig['martingale_times'].append(m.group(1))
        return sig if sig['asset'] and sig['direction'] else None
    except: return None

# ── Execute Auto Signal ────────────────────────────────────────
def execute_auto_signal(signal):
    if not auto_client or not auto_connected: return
    asset      = signal['asset']
    direction  = signal['direction']
    expiry     = signal['expiry']
    entry_time = signal['entry_time']
    mg_times   = signal['martingale_times']
    amount     = 1.0

    bot.send_message(
        TELEGRAM_USER_ID,
        f"🤖 <b>AUTO SIGNAL!</b>\n"
        f"{'🟢 BUY' if direction=='call' else '🔴 SELL'} {asset}\n"
        f"Entry: {entry_time or 'NOW'} | {expiry}min",
        parse_mode='HTML'
    )

    def wait_until(t):
        now    = datetime.now(UTC_MINUS_4)
        target = datetime.strptime(
            f"{now.strftime('%Y-%m-%d')} {t}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=UTC_MINUS_4)
        wait_s = (target - now).total_seconds()
        if wait_s > 0: time.sleep(wait_s)

    if entry_time: wait_until(entry_time)

    loop = asyncio.new_event_loop()

    async def trade(amt):
        return await auto_place_trade(asset, direction, amt, expiry)

    # Entry
    outcome, profit = loop.run_until_complete(trade(amount))
    if outcome == 'win':
        bot.send_message(TELEGRAM_USER_ID, f"🎉 <b>AUTO WIN!</b> +${profit:.2f}", parse_mode='HTML')
        update_auto_stats('win', profit, amount)
        loop.close()
        return
    elif outcome == 'loss':
        bot.send_message(TELEGRAM_USER_ID, f"❌ Loss → M1 ${amount*2:.2f}")
        update_auto_stats('loss', 0, amount)
    else:
        loop.close()
        return

    # M1
    if len(mg_times) >= 1:
        wait_until(mg_times[0])
        outcome, profit = loop.run_until_complete(trade(amount * 2))
        if outcome == 'win':
            bot.send_message(TELEGRAM_USER_ID, f"🎉 <b>WIN M1!</b> +${profit:.2f}", parse_mode='HTML')
            update_auto_stats('win', profit, amount)
            loop.close()
            return
        bot.send_message(TELEGRAM_USER_ID, f"❌ Loss M1 → M2 ${amount*4:.2f}")
        update_auto_stats('loss', 0, amount)

    # M2
    if len(mg_times) >= 2:
        wait_until(mg_times[1])
        outcome, profit = loop.run_until_complete(trade(amount * 4))
        if outcome == 'win':
            bot.send_message(TELEGRAM_USER_ID, f"🎉 <b>WIN M2!</b> +${profit:.2f}", parse_mode='HTML')
            update_auto_stats('win', profit, amount)
        else:
            bot.send_message(TELEGRAM_USER_ID, f"❌ Loss M2. Reset.")
            update_auto_stats('loss', 0, amount)

    loop.close()

# ── Signal Watcher ─────────────────────────────────────────────
def run_signal_watcher():
    async def watcher():
        client = TelegramClient(
            StringSession(SESSION_STRING), API_ID, API_HASH,
            device_model="Linux", system_version="Ubuntu 20.04"
        )
        await client.connect()
        if not await client.is_user_authorized():
            logger.error("❌ SESSION_STRING_2 invalid!")
            return
        me   = await client.get_me()
        dest = await client.get_entity(int(DEST_GROUP))
        logger.info(f"✅ Watching: {dest.title}")
        bot.send_message(TELEGRAM_USER_ID, f"👀 Watching: {dest.title}")

        @client.on(events.NewMessage(chats=dest))
        async def handler(event):
            if not auto_trading: return
            text = event.message.text or ''
            if not text or should_ignore(text): return
            signal = parse_signal(text)
            if not signal: return
            logger.info(f"✅ Signal: {signal['asset']} {signal['direction']}")
            threading.Thread(
                target=execute_auto_signal,
                args=(signal,),
                daemon=True
            ).start()

        await client.run_until_disconnected()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(watcher())

# ── Main ───────────────────────────────────────────────────────
def main():
    start_keep_alive()
    logger.info("🚀 Starting QuantomBot V8...")

    # Connect auto engine
    threading.Thread(target=connect_auto_engine, daemon=True).start()

    try:
        bot.send_message(
            TELEGRAM_USER_ID,
            "🤖 <b>QuantomBot V8 LIVE!</b>\n"
            "🔌 Auto engine connecting...\n"
            "Type /start to see panel!",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Startup: {e}")

    threading.Thread(target=run_signal_watcher, daemon=True).start()
    logger.info("✅ Polling started!")
    bot.infinity_polling(
        timeout=60,
        long_polling_timeout=60,
        skip_pending=True,
        allowed_updates=["message", "callback_query"]
    )

if __name__ == '__main__':
    while True:
        try:
            main()
        except Exception as e:
            logger.error(f"💥 Crash: {e}")
        logger.warning("🔄 Restarting in 30s...")
        time.sleep(30)
