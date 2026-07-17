# ============================================================
# QuantomBot V8 — Dual Engine Edition
# Uses A11ksa/API-Pocket-Option library
# Auto login with email/password + 2captcha
# ============================================================

import os, re, json, asyncio, logging, threading, time
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession
import telebot
import requests
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ── Fix for 409 conflict ───────────────────────────────────────
try:
    requests.get(
        f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_BOT_TOKEN', '')}/deleteWebhook?drop_pending_updates=true",
        timeout=10
    )
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
PO_EMAIL           = os.environ.get('PO_EMAIL', '')
PO_PASSWORD        = os.environ.get('PO_PASSWORD', '')
CAPTCHA_KEY        = os.environ.get('CAPTCHA_KEY', '')

UTC_MINUS_4   = timezone(timedelta(hours=-4))
DEFAULT_EXPIRY = 2
USERS_FILE    = 'users.json'

# ── Bot ────────────────────────────────────────────────────────
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)

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
auto_client  = None
auto_trading = False
auto_connected = False

def get_user(uid):
    uid = str(uid)
    if uid not in users:
        users[uid] = {
            'ssid': '', 'is_demo': True, 'amount': 1.0,
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

# ── Connect Auto Engine ────────────────────────────────────────
def connect_auto_engine():
    global auto_client, auto_connected
    try:
        from api_pocket import AsyncPocketOptionClient, get_ssid
        logger.info("🔐 Auto login with email/password...")
        bot.send_message(TELEGRAM_USER_ID, "🔐 Auto logging into Pocket Option...")

        # Get SSID using built-in auto login
        os.environ['POCKET_EMAIL']    = PO_EMAIL
        os.environ['POCKET_PASSWORD'] = PO_PASSWORD
        if CAPTCHA_KEY:
            os.environ['TWOCAPTCHA_API_KEY'] = CAPTCHA_KEY

        ssid_data = get_ssid(email=PO_EMAIL, password=PO_PASSWORD)
        logger.info(f"✅ Got SSID data: {type(ssid_data)}")

        # Get demo SSID
        if isinstance(ssid_data, dict):
            ssid = ssid_data.get('demo') or ssid_data.get('live') or list(ssid_data.values())[0]
        else:
            ssid = str(ssid_data)

        if not ssid:
            bot.send_message(TELEGRAM_USER_ID, "❌ Auto login failed! Check PO_EMAIL and PO_PASSWORD.")
            return False

        logger.info(f"✅ SSID obtained: {ssid[:15]}...")

        async def do_connect():
            global auto_client, auto_connected
            client = AsyncPocketOptionClient(ssid=ssid, is_demo=True)
            await client.connect()
            await asyncio.sleep(3)
            bal = await client.get_balance()
            bal_val = float(bal.balance) if hasattr(bal, 'balance') else float(bal)
            auto_client    = client
            auto_connected = True
            logger.info(f"✅ Auto Engine connected! Balance: ${bal_val:.2f}")
            bot.send_message(
                TELEGRAM_USER_ID,
                f"🤖 <b>Auto Engine Ready!</b>\n"
                f"Balance: ${bal_val:.2f} (DEMO)\n"
                f"Click 🤖 Auto Signal ON to start!",
                parse_mode='HTML'
            )

        loop = asyncio.new_event_loop()
        loop.run_until_complete(do_connect())
        loop.close()
        return True

    except Exception as e:
        logger.error(f"Auto engine error: {e}")
        bot.send_message(TELEGRAM_USER_ID, f"❌ Auto engine error:\n{e}")
        return False

# ── Connect Personal ───────────────────────────────────────────
def connect_personal(uid):
    uid  = str(uid)
    user = get_user(uid)
    ssid = user.get('ssid', '')
    if not ssid:
        bot.send_message(int(uid), "❌ No SSID! Use 🔑 My Login first.")
        return

    def do_connect():
        try:
            from api_pocket import AsyncPocketOptionClient
            async def run():
                client = AsyncPocketOptionClient(ssid=ssid, is_demo=user.get('is_demo', True))
                await client.connect()
                await asyncio.sleep(3)
                bal     = await client.get_balance()
                bal_val = float(bal.balance) if hasattr(bal, 'balance') else float(bal)
                user_clients[uid] = client
                mode = "🔵 DEMO" if user.get('is_demo', True) else "🔴 REAL"
                bot.send_message(
                    int(uid),
                    f"✅ <b>Personal Account Connected!</b>\n"
                    f"Mode: {mode}\nBalance: ${bal_val:.2f}",
                    parse_mode='HTML'
                )
            loop = asyncio.new_event_loop()
            loop.run_until_complete(run())
            loop.close()
        except Exception as e:
            bot.send_message(int(uid), f"❌ Connection error: {e}")

    threading.Thread(target=do_connect, daemon=True).start()

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
        f"▸ Auto login with email/password\n"
        f"▸ Click 🤖 Auto Signal ON\n\n"
        f"<b>Engine 2 — Manual Control:</b>\n"
        f"▸ Login with 🔑 My Login\n"
        f"▸ Trade with 👤 Manual Trade\n\n"
        f"Both engines work independently! 🚀",
        parse_mode='HTML',
        reply_markup=main_menu()
    )

# ── Auto Signal ON/OFF ─────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "🤖 Auto Signal ON")
def cmd_auto_on(message):
    global auto_trading
    if not auto_connected:
        bot.send_message(message.chat.id, "⏳ Connecting auto engine...\nPlease wait 30-60 seconds!")
        threading.Thread(target=connect_auto_engine, daemon=True).start()
        return
    auto_trading = True
    bot.send_message(
        message.chat.id,
        "🤖 <b>Auto Signal ON!</b>\n"
        "✅ Watching Rex Signal Alerts\n"
        "✅ Auto executing signals\n"
        "✅ Martingale $1→$2→$4",
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda m: m.text == "🛑 Auto Signal OFF")
def cmd_auto_off(message):
    global auto_trading
    auto_trading = False
    bot.send_message(message.chat.id, "🛑 <b>Auto Signal OFF!</b>", parse_mode='HTML')

# ── My Login ───────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "🔑 My Login")
def cmd_login(message):
    uid = str(message.from_user.id)
    user_state[uid] = 'wait_ssid'
    bot.send_message(
        message.chat.id,
        "🔑 Paste your <b>Pocket Option SSID:</b>\n\n"
        "Get from: pocketoption.com → F12\n"
        "→ Application → Cookies → ssid",
        parse_mode='HTML'
    )

# ── Manual Trade ───────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "👤 Manual Trade")
def cmd_manual(message):
    uid = str(message.from_user.id)
    if uid not in user_clients:
        bot.send_message(message.chat.id, "❌ Use 🔑 My Login first!")
        return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("EUR/USD OTC", callback_data="asset_EURUSD_otc"),
        InlineKeyboardButton("GBP/USD OTC", callback_data="asset_GBPUSD_otc"),
        InlineKeyboardButton("USD/JPY OTC", callback_data="asset_USDJPY_otc"),
        InlineKeyboardButton("AUD/USD OTC", callback_data="asset_AUDUSD_otc"),
        InlineKeyboardButton("NGN/USD OTC", callback_data="asset_NGNUSD_otc"),
        InlineKeyboardButton("EUR/GBP OTC", callback_data="asset_EURGBP_otc"),
    )
    bot.send_message(message.chat.id, "👤 <b>MANUAL TRADE</b>\nChoose Asset:", parse_mode='HTML', reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("asset_"))
def cb_asset(call):
    uid = str(call.from_user.id)
    bot.answer_callback_query(call.id)
    get_user(uid)['_temp_asset'] = call.data.replace("asset_", "")
    save_users(users)
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🟢 BUY/CALL", callback_data="dir_call"),
        InlineKeyboardButton("🔴 SELL/PUT", callback_data="dir_put"),
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
    bot.send_message(
        call.message.chat.id,
        f"⏳ Placing {'🟢 BUY' if direction=='call' else '🔴 SELL'} "
        f"{asset} ${amount} {expiry}min..."
    )
    threading.Thread(
        target=manual_trade_thread,
        args=(uid, asset, direction, amount, expiry, client),
        daemon=True
    ).start()

def manual_trade_thread(uid, asset, direction, amount, expiry, client):
    from api_pocket import OrderDirection as OD
    loop = asyncio.new_event_loop()
    async def run():
        try:
            od    = OD.CALL if direction == 'call' else OD.PUT
            order = await client.place_order(asset=asset, amount=amount, direction=od, duration=expiry*60)
            if not order:
                bot.send_message(int(uid), "❌ Trade failed!")
                return
            bot.send_message(
                int(uid),
                f"✅ <b>Trade Placed!</b>\n"
                f"{'🟢 BUY' if direction=='call' else '🔴 SELL'} {asset}\n"
                f"${amount} | {expiry}min ⏳",
                parse_mode='HTML'
            )
            result = await client.check_win(order.order_id)
            profit = float(result.profit) if hasattr(result, 'profit') else 0
            if profit > 0:
                bot.send_message(int(uid), f"🎉 <b>WIN!</b> +${profit:.2f}", parse_mode='HTML')
            else:
                bot.send_message(int(uid), f"❌ <b>LOSS</b> -${amount:.2f}", parse_mode='HTML')
        except Exception as e:
            bot.send_message(int(uid), f"❌ Trade error: {e}")
    loop.run_until_complete(run())
    loop.close()

# ── Dashboard ──────────────────────────────────────────────────
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
        f"🤖 <b>Auto Engine</b>\n"
        f"Status: {'🟢 ON' if auto_trading else '🔴 OFF'}\n"
        f"Connected: {'✅' if auto_connected else '❌'}\n"
        f"Trades: {auto_stats['total']} ✅{auto_stats['wins']} ❌{auto_stats['losses']}\n"
        f"Win Rate: {auto_wr:.1f}% | P/L: ${auto_stats['profit']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Personal Engine</b>\n"
        f"Connected: {'✅' if uid in user_clients else '❌'}\n"
        f"Amount: ${user.get('amount',1.0)}\n"
        f"Trades: {my_stats['total']} ✅{my_stats['wins']} ❌{my_stats['losses']}\n"
        f"Win Rate: {my_wr:.1f}% | P/L: ${my_stats['profit']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━",
        parse_mode='HTML'
    )

# ── Balance ────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "💰 Balance")
def cmd_balance(message):
    uid  = str(message.from_user.id)
    text = "💰 <b>BALANCES</b>\n━━━━━━━━━━━━━━━━━━\n"

    def get_bal(client):
        loop = asyncio.new_event_loop()
        bal  = loop.run_until_complete(client.get_balance())
        loop.close()
        return float(bal.balance) if hasattr(bal, 'balance') else float(bal)

    if auto_connected and auto_client:
        try:
            text += f"🤖 Auto: ${get_bal(auto_client):.2f} (DEMO)\n"
        except: text += "🤖 Auto: Error\n"
    else:
        text += "🤖 Auto: Not connected\n"

    if uid in user_clients:
        try:
            mode  = "DEMO" if get_user(uid).get('is_demo', True) else "REAL"
            text += f"👤 Personal: ${get_bal(user_clients[uid]):.2f} ({mode})\n"
        except: text += "👤 Personal: Error\n"
    else:
        text += "👤 Personal: Not connected\n"

    bot.send_message(message.chat.id, text + "━━━━━━━━━━━━━━━━━━", parse_mode='HTML')

# ── Settings ───────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "⚙️ Settings")
def cmd_settings(message):
    uid  = str(message.from_user.id)
    user = get_user(uid)
    mode = "🔵 DEMO" if user.get('is_demo', True) else "🔴 REAL"
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔵 DEMO",   callback_data="set_demo"),
        InlineKeyboardButton("🔴 REAL",   callback_data="set_real"),
        InlineKeyboardButton("$1",        callback_data="set_amt_1.0"),
        InlineKeyboardButton("$2",        callback_data="set_amt_2.0"),
        InlineKeyboardButton("$5",        callback_data="set_amt_5.0"),
        InlineKeyboardButton("💵 Custom", callback_data="set_amt_custom")
    )
    bot.send_message(
        message.chat.id,
        f"⚙️ <b>SETTINGS</b>\nMode: {mode}\nAmount: ${user.get('amount',1.0)}",
        parse_mode='HTML', reply_markup=markup
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
        bot.send_message(call.message.chat.id, "✅ REAL mode! ⚠️")
    elif call.data == "set_amt_custom":
        user_state[uid] = 'wait_amount'
        bot.send_message(call.message.chat.id, "💵 Enter amount:")
    elif call.data.startswith("set_amt_"):
        amt = float(call.data.replace("set_amt_",""))
        user['amount'] = amt; save_users(users)
        bot.send_message(call.message.chat.id, f"✅ Amount: ${amt:.2f}")

# ── Stats ──────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "📈 My Stats")
def cmd_stats(message):
    uid        = str(message.from_user.id)
    user       = get_user(uid)
    auto_stats = user.get('auto_stats', {'total':0,'wins':0,'losses':0,'profit':0.0})
    my_stats   = user.get('stats', {'total':0,'wins':0,'losses':0,'profit':0.0})
    auto_wr    = (auto_stats['wins']/auto_stats['total']*100) if auto_stats['total'] > 0 else 0
    my_wr      = (my_stats['wins']/my_stats['total']*100) if my_stats['total'] > 0 else 0
    bot.send_message(
        message.chat.id,
        f"📈 <b>STATS</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Auto: {auto_stats['total']} | {auto_wr:.1f}% | ${auto_stats['profit']:.2f}\n"
        f"👤 Manual: {my_stats['total']} | {my_wr:.1f}% | ${my_stats['profit']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━",
        parse_mode='HTML'
    )

# ── Help ───────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "❓ Help")
def cmd_help(message):
    bot.send_message(
        message.chat.id,
        "❓ <b>HOW TO USE</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🤖 Auto Signal ON/OFF — Auto trading\n"
        "🔑 My Login — Personal account\n"
        "👤 Manual Trade — Place manually\n"
        "📊 Dashboard — Both engines status\n"
        "💰 Balance — Check balances\n"
        "⚙️ Settings — DEMO/REAL & amount\n"
        "📈 My Stats — Win/loss record\n"
        "━━━━━━━━━━━━━━━━━━",
        parse_mode='HTML'
    )

# ── General Handler ────────────────────────────────────────────
@bot.message_handler(func=lambda m: True)
def handle_text(message):
    uid   = str(message.from_user.id)
    text  = message.text.strip()
    state = user_state.get(uid)
    if state == 'wait_ssid':
        user_state.pop(uid, None)
        user         = get_user(uid)
        user['ssid'] = text
        save_users(users)
        bot.send_message(message.chat.id, "✅ SSID saved! Connecting...")
        threading.Thread(target=connect_personal, args=(uid,), daemon=True).start()
    elif state == 'wait_amount':
        user_state.pop(uid, None)
        try:
            amt = float(text)
            if amt < 1:
                bot.send_message(message.chat.id, "❌ Minimum $1")
                return
            user = get_user(uid)
            user['amount'] = amt
            save_users(users)
            bot.send_message(message.chat.id, f"✅ Amount: ${amt:.2f}")
        except:
            bot.send_message(message.chat.id, "❌ Invalid amount")

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
        f"📊 {stats['total']} trades | ✅{stats['wins']} ❌{stats['losses']} | {wr:.1f}% | ${stats['profit']:.2f}"
    )

# ── Signal Parse ───────────────────────────────────────────────
IGNORE_KEYWORDS = [
    'isaac godwin','one on one','contact me','limited slots',
    'account management','earn daily','training','good morning',
    'good evening','good afternoon','good night','we will use',
    'set your timeframe','win at direct','result update',
    'win ✅','✅ win','loss','lose','direct win',
]

def should_ignore(text):
    if not text: return True
    return any(k in text.lower() for k in IGNORE_KEYWORDS)

def parse_signal(text):
    try:
        if not text or not re.search(r'[A-Z]{3}/[A-Z]{3}', text.upper()):
            return None
        m = re.search(r'[A-Z]{3}/[A-Z]{3}', text.upper())
        asset = m.group(0).replace('/', '') + '_otc' if m else None
        if not asset: return None
        tu = text.upper()
        if 'BUY' in tu or 'CALL' in tu or '🟩' in text:
            direction = 'call'
        elif 'SELL' in tu or 'PUT' in tu or '🟥' in text:
            direction = 'put'
        else: return None
        sig = {'asset': asset, 'direction': direction,
               'expiry': DEFAULT_EXPIRY, 'entry_time': None, 'martingale_times': []}
        for line in text.split('\n'):
            lu = line.upper()
            if 'ENTRY' in lu:
                mt = re.search(r'(\d{1,2}:\d{2})', line)
                if mt: sig['entry_time'] = mt.group(1)
            if '1️⃣' in line:
                mt = re.search(r'(\d{1,2}:\d{2})', line)
                if mt and len(sig['martingale_times'])==0:
                    sig['martingale_times'].append(mt.group(1))
            elif '2️⃣' in line:
                mt = re.search(r'(\d{1,2}:\d{2})', line)
                if mt and len(sig['martingale_times'])<=1:
                    sig['martingale_times'].append(mt.group(1))
        return sig
    except: return None

# ── Execute Auto Signal ────────────────────────────────────────
def execute_auto_signal(signal):
    if not auto_client or not auto_connected: return
    from api_pocket import OrderDirection as OD
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
        f"Entry: {entry_time or 'NOW'} | {expiry}min | ${amount}",
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

    async def place(amt):
        try:
            od    = OD.CALL if direction == 'call' else OD.PUT
            order = await auto_client.place_order(asset=asset, amount=amt, direction=od, duration=expiry*60)
            return order.order_id if order else None
        except Exception as e:
            logger.error(f"Place error: {e}")
            return None

    async def get_result(order_id):
        try:
            result = await auto_client.check_win(order_id)
            profit = float(result.profit) if hasattr(result, 'profit') else 0
            return ('win', profit) if profit > 0 else ('loss', 0)
        except: return ('loss', 0)

    # Entry
    trade_id = loop.run_until_complete(place(amount))
    if not trade_id:
        bot.send_message(TELEGRAM_USER_ID, "❌ Auto entry failed.")
        loop.close(); return

    bot.send_message(TELEGRAM_USER_ID, f"✅ Entry! ${amount}")
    result, profit = loop.run_until_complete(get_result(trade_id))
    if result == 'win':
        bot.send_message(TELEGRAM_USER_ID, f"🎉 <b>WIN!</b> +${profit:.2f}", parse_mode='HTML')
        update_auto_stats('win', profit, amount)
        loop.close(); return
    bot.send_message(TELEGRAM_USER_ID, f"❌ Loss → M1 ${amount*2:.2f}")
    update_auto_stats('loss', 0, amount)

    # M1
    if len(mg_times) >= 1:
        wait_until(mg_times[0])
        trade_id = loop.run_until_complete(place(amount*2))
        if trade_id:
            result, profit = loop.run_until_complete(get_result(trade_id))
            if result == 'win':
                bot.send_message(TELEGRAM_USER_ID, f"🎉 <b>WIN M1!</b> +${profit:.2f}", parse_mode='HTML')
                update_auto_stats('win', profit, amount)
                loop.close(); return
            bot.send_message(TELEGRAM_USER_ID, f"❌ Loss M1 → M2 ${amount*4:.2f}")
            update_auto_stats('loss', 0, amount)

    # M2
    if len(mg_times) >= 2:
        wait_until(mg_times[1])
        trade_id = loop.run_until_complete(place(amount*4))
        if trade_id:
            result, profit = loop.run_until_complete(get_result(trade_id))
            if result == 'win':
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

        @client.on(events.NewMessage(chats=dest))
        async def handler(event):
            if not auto_trading: return
            text = event.message.text or ''
            if not text or should_ignore(text): return
            signal = parse_signal(text)
            if not signal: return
            logger.info(f"✅ Signal: {signal['asset']} {signal['direction']}")
            threading.Thread(target=execute_auto_signal, args=(signal,), daemon=True).start()

        await client.run_until_disconnected()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(watcher())

# ── Main ───────────────────────────────────────────────────────
def main():
    start_keep_alive()
    logger.info("🚀 Starting QuantomBot V8...")

    # Start auto engine
    threading.Thread(target=connect_auto_engine, daemon=True).start()

    try:
        bot.send_message(
            TELEGRAM_USER_ID,
            "🤖 <b>QuantomBot V8 LIVE!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔐 Auto logging into PO...\n"
            "👤 Use 🔑 My Login for personal\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Type /start to see panel!",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Startup msg: {e}")

    threading.Thread(target=run_signal_watcher, daemon=True).start()
    logger.info("✅ Bot polling started!")
    bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)

if __name__ == '__main__':
    while True:
        try:
            main()
        except Exception as e:
            logger.error(f"💥 Crash: {e}")
        logger.warning("🔄 Restarting in 30s...")
        time.sleep(30)
