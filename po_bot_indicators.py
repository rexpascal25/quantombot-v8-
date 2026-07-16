# ============================================================
# QuantomBot V8 — Dual Engine | All In One File
# Auto Login + Custom WebSocket + Signal Trading
# ============================================================

import os, re, json, asyncio, logging, threading, time, uuid, requests
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession
import telebot
import websockets
from po_login import auto_login_and_get_ssid
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── Environment Variables ──────────────────────────────────────
API_ID             = int(os.environ.get('API_ID', 0))
API_HASH           = os.environ.get('API_HASH', '')
SESSION_STRING     = os.environ.get('SESSION_STRING_2', '')
DEST_GROUP         = os.environ.get('DEST_GROUP', '')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_USER_ID   = int(os.environ.get('TELEGRAM_USER_ID', 0))
PORT               = int(os.environ.get('PORT', 8080))
RAILWAY_SSID       = os.environ.get('PO_SSID', '')
PO_EMAIL           = os.environ.get('PO_EMAIL', '')
PO_PASSWORD        = os.environ.get('PO_PASSWORD', '')
CAPTCHA_KEY        = os.environ.get('CAPTCHA_KEY', '')
CHROME_BIN         = os.environ.get('CHROME_BIN', '/usr/bin/chromium')
CHROMEDRIVER       = os.environ.get('CHROMEDRIVER_PATH', '/usr/bin/chromedriver')

UTC_MINUS_4        = timezone(timedelta(hours=-4))
DEFAULT_EXPIRY     = 2
USERS_FILE         = 'users.json'

# ── Kill old bot instances ─────────────────────────────────────
try:
    requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=true",
        timeout=10
    )
    time.sleep(3)
except: pass

# ── Bot Instance ───────────────────────────────────────────────
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
    except Exception as e:
        logger.error(f"Save error: {e}")

users        = load_users()
user_clients = {}
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

# ── Auto Login ─────────────────────────────────────────────────
def solve_captcha(site_key, url):
    if not CAPTCHA_KEY: return None
    try:
        resp = requests.post('http://2captcha.com/in.php', data={
            'key': CAPTCHA_KEY, 'method': 'userrecaptcha',
            'googlekey': site_key, 'pageurl': url, 'json': 1
        })
        result = resp.json()
        if result.get('status') != 1: return None
        captcha_id = result['request']
        logger.info(f"Captcha ID: {captcha_id}")
        for _ in range(36):
            time.sleep(5)
            resp = requests.get('http://2captcha.com/res.php', params={
                'key': CAPTCHA_KEY, 'action': 'get',
                'id': captcha_id, 'json': 1
            })
            result = resp.json()
            if result.get('status') == 1:
                logger.info("✅ Captcha solved!")
                return result['request']
            if result.get('request') != 'CAPCHA_NOT_READY': return None
        return None
    except Exception as e:
        logger.error(f"Captcha error: {e}")
        return None

def auto_login_and_get_ssid():
    logger.info("🔐 Auto login starting...")
    driver = None
    try:
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        options.binary_location = CHROME_BIN
        service = Service(CHROMEDRIVER)
        driver  = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        driver.get('https://pocketoption.com/en/login/')
        logger.info("Opened login page")
        time.sleep(3)

        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.NAME, 'email'))).send_keys(PO_EMAIL)
        time.sleep(1)
        driver.find_element(By.NAME, 'password').send_keys(PO_PASSWORD)
        time.sleep(1)
        logger.info("Credentials filled")

        # Check for captcha
        try:
            ce = driver.find_element(By.CLASS_NAME, 'g-recaptcha')
            sk = ce.get_attribute('data-sitekey')
            if sk:
                logger.info("Captcha found! Solving...")
                token = solve_captcha(sk, 'https://pocketoption.com/en/login/')
                if token:
                    driver.execute_script(
                        f'document.getElementById("g-recaptcha-response").innerHTML="{token}";'
                    )
                    time.sleep(2)
                    logger.info("Captcha injected!")
        except: logger.info("No captcha detected")

        # Click login
        try:
            driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
        except:
            try: driver.find_element(By.CSS_SELECTOR, '.btn-login').click()
            except: pass
        logger.info("Login clicked")
        time.sleep(10)

        # Inject WS capture script
        driver.execute_script("""
            window._ps = null;
            const ows = window.WebSocket;
            window.WebSocket = function(u,p) {
                const ws = new ows(u,p);
                ws.addEventListener('message', function(e) {
                    if(e.data && e.data.includes('session')) {
                        try {
                            const d = JSON.parse(e.data.slice(2));
                            if(d[1] && d[1].session) window._ps = d[1].session;
                        } catch(x) {}
                    }
                });
                return ws;
            };
        """)
        time.sleep(8)

        # Try WS session
        session = driver.execute_script("return window._ps;")
        if session:
            logger.info(f"✅ Got WS session!")
            return session

        # Try cookies
        cookies = driver.get_cookies()
        logger.info(f"Cookies: {[c['name'] for c in cookies]}")
        for name in ['ssid', 'session', 'SSID', 'SESSION', 'ci_session']:
            for c in cookies:
                if c['name'] == name and len(c.get('value','')) > 5:
                    logger.info(f"✅ Got cookie: {name}")
                    return c['value']

        # Any long cookie
        for c in cookies:
            v = c.get('value','')
            n = c.get('name','')
            if len(v) > 30 and n not in ['_ga','_fbp','FPLC','FPID','gclid','reg_url','qrator_ssid2']:
                logger.info(f"✅ Using cookie: {n}")
                return v

        logger.error("No session found!")
        return None
    except Exception as e:
        logger.error(f"Login error: {e}")
        return None
    finally:
        if driver:
            try: driver.quit()
            except: pass

# ── PO WebSocket URLs ──────────────────────────────────────────
PO_WS_URLS = [
    "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket",
    "wss://try-demo-eu.po.market/socket.io/?EIO=4&transport=websocket",
    "wss://demo-api-us.po.market/socket.io/?EIO=4&transport=websocket",
]

# ── Custom PO WebSocket Client ─────────────────────────────────
class POClient:
    def __init__(self, ssid, is_demo=True):
        self.ssid      = ssid
        self.is_demo   = is_demo
        self.ws        = None
        self.connected = False
        self.balance   = 0.0
        self.orders    = {}

    async def connect(self):
        headers = {
            "Origin": "https://pocketoption.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        for url in PO_WS_URLS:
            try:
                logger.info(f"Trying: {url}")
                self.ws = await websockets.connect(
                    url,
                    extra_headers=headers,
                    ping_interval=20,
                    ping_timeout=10
                )
                msg = await asyncio.wait_for(self.ws.recv(), timeout=10)
                logger.info(f"Connected! msg: {msg[:50]}")
                auth = json.dumps(["auth", {
                    "session": self.ssid,
                    "isDemo": 1 if self.is_demo else 0,
                    "uid": 0,
                    "platform": 2
                }])
                await self.ws.send(f"42{auth}")
                for _ in range(10):
                    msg = await asyncio.wait_for(self.ws.recv(), timeout=10)
                    if any(x in msg for x in ['balance', 'updateStream', 'successauth']):
                        self.connected = True
                        await self._extract_balance(msg)
                        asyncio.create_task(self._listen())
                        return True
                    if msg == '2':
                        await self.ws.send('3')
                self.connected = True
                asyncio.create_task(self._listen())
                return True
            except Exception as e:
                logger.warning(f"URL failed: {e}")
                continue
        return False

    async def _extract_balance(self, msg):
        try:
            if msg.startswith('42'):
                data = json.loads(msg[2:])
                if isinstance(data, list) and len(data) > 1:
                    payload = data[1]
                    if isinstance(payload, dict) and 'balance' in payload:
                        self.balance = float(payload['balance'])
        except: pass

    async def _listen(self):
        try:
            async for msg in self.ws:
                if msg == '2':
                    await self.ws.send('3')
                elif msg.startswith('42'):
                    try:
                        data = json.loads(msg[2:])
                        if isinstance(data, list) and len(data) > 1:
                            event   = data[0]
                            payload = data[1]
                            if event in ['updateStream', 'balance']:
                                if isinstance(payload, dict) and 'balance' in payload:
                                    self.balance = float(payload['balance'])
                            elif event in ['successcloseOrder', 'closeOrder']:
                                oid    = payload.get('id') or payload.get('order_id')
                                profit = float(payload.get('profit', 0) or payload.get('win', 0))
                                if oid:
                                    self.orders[str(oid)] = {'status': 'closed', 'profit': profit}
                    except: pass
        except Exception as e:
            logger.warning(f"WS listener error: {e}")
            self.connected = False

    async def get_balance(self):
        try:
            if self.ws:
                await self.ws.send('42["getBalance",{}]')
                await asyncio.sleep(2)
        except: pass
        return self.balance

    async def place_order(self, asset, amount, direction, duration):
        if not self.ws: return None
        try:
            order_id = str(uuid.uuid4())
            msg = json.dumps(["openOrder", {
                "asset": asset, "amount": amount,
                "action": direction,
                "isDemo": 1 if self.is_demo else 0,
                "requestId": order_id,
                "optionType": 100, "time": duration
            }])
            await self.ws.send(f"42{msg}")
            return type('Order', (), {'order_id': order_id})()
        except Exception as e:
            logger.error(f"Place error: {e}")
            return None

    async def get_order_result(self, order_id, timeout=300):
        start = time.time()
        while time.time() - start < timeout:
            order = self.orders.get(str(order_id))
            if order and order.get('status') == 'closed':
                return type('Result', (), {'profit': order.get('profit', 0)})()
            await asyncio.sleep(2)
        return type('Result', (), {'profit': 0})()

# ── Parse Balance ──────────────────────────────────────────────
def parse_balance(b):
    if b is None: return 0.0
    try: return float(b)
    except:
        nums = re.findall(r'\d+\.?\d*', str(b))
        return float(nums[0]) if nums else 0.0

# ── Connect Auto Engine ────────────────────────────────────────
def connect_auto_engine():
    global auto_client, auto_connected, RAILWAY_SSID
    ssid = RAILWAY_SSID

    # If no SSID, try auto login
    if not ssid and PO_EMAIL and PO_PASSWORD:
        bot.send_message(TELEGRAM_USER_ID, "🔐 No SSID found. Auto logging in...")
        ssid = auto_login_and_get_ssid()
        if ssid:
            RAILWAY_SSID = ssid
            logger.info("✅ Got SSID from auto login!")
        else:
            bot.send_message(TELEGRAM_USER_ID, "❌ Auto login failed! Check PO_EMAIL/PO_PASSWORD.")
            return False

    if not ssid:
        bot.send_message(TELEGRAM_USER_ID, "❌ No SSID and no email/password set!")
        return False

    async def do_connect():
        global auto_client, auto_connected
        try:
            client = POClient(ssid, is_demo=True)
            result = await client.connect()
            if result:
                auto_client    = client
                auto_connected = True
                await asyncio.sleep(3)
                bal = await client.get_balance()
                logger.info(f"✅ Auto Engine: ${bal:.2f}")
                bot.send_message(
                    TELEGRAM_USER_ID,
                    f"🤖 <b>Auto Engine Ready!</b>\n"
                    f"Balance: ${bal:.2f} (DEMO)\n"
                    f"Click 🤖 Auto Signal ON to start!",
                    parse_mode='HTML'
                )
            else:
                # SSID failed, try auto login
                if PO_EMAIL and PO_PASSWORD:
                    bot.send_message(TELEGRAM_USER_ID, "⚠️ SSID failed. Trying auto login...")
                    new_ssid = auto_login_and_get_ssid()
                    if new_ssid:
                        client2 = POClient(new_ssid, is_demo=True)
                        result2 = await client2.connect()
                        if result2:
                            auto_client    = client2
                            auto_connected = True
                            bal = await client2.get_balance()
                            bot.send_message(
                                TELEGRAM_USER_ID,
                                f"✅ <b>Connected via auto login!</b>\nBalance: ${bal:.2f}",
                                parse_mode='HTML'
                            )
                            return
                bot.send_message(TELEGRAM_USER_ID, "❌ Auto engine failed! Check credentials.")
        except Exception as e:
            logger.error(f"Auto engine error: {e}")
            bot.send_message(TELEGRAM_USER_ID, f"❌ Engine error: {e}")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(do_connect())
    loop.close()

# ── Connect Personal ───────────────────────────────────────────
def connect_personal(uid):
    uid  = str(uid)
    user = get_user(uid)
    ssid = user.get('ssid', '')
    if not ssid:
        bot.send_message(int(uid), "❌ No SSID! Use 🔑 My Login first.")
        return

    async def do_connect():
        try:
            client = POClient(ssid, is_demo=user.get('is_demo', True))
            result = await client.connect()
            if result:
                user_clients[uid] = client
                await asyncio.sleep(3)
                bal  = await client.get_balance()
                mode = "🔵 DEMO" if user.get('is_demo', True) else "🔴 REAL"
                bot.send_message(
                    int(uid),
                    f"✅ <b>Personal Account Connected!</b>\n"
                    f"Mode: {mode}\nBalance: ${bal:.2f}\n"
                    f"Use 👤 Manual Trade now!",
                    parse_mode='HTML'
                )
            else:
                bot.send_message(int(uid), "❌ Connection failed! Check your SSID.")
        except Exception as e:
            bot.send_message(int(uid), f"❌ Error: {e}")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(do_connect())
    loop.close()

# ── Main Menu ──────────────────────────────────────────────────
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("📊 Dashboard"),    KeyboardButton("🔑 My Login"),
        KeyboardButton("⚙️ Settings"),     KeyboardButton("📈 My Stats"),
        KeyboardButton("🤖 Auto Signal ON"),KeyboardButton("🛑 Auto Signal OFF"),
        KeyboardButton("👤 Manual Trade"), KeyboardButton("💰 Balance"),
        KeyboardButton("❓ Help")
    )
    return markup

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
        f"Engine 1 — Auto Signal:\n"
        f"▸ Reads Rex Signal Alerts automatically\n"
        f"▸ Click 🤖 Auto Signal ON to activate\n\n"
        f"Engine 2 — Manual Control:\n"
        f"▸ Login with 🔑 My Login\n"
        f"▸ Trade manually with 👤 Manual Trade\n\n"
        f"Both engines work independently! 🚀",
        parse_mode='HTML',
        reply_markup=main_menu()
    )

# ── Auto Signal ON/OFF ─────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "🤖 Auto Signal ON")
def cmd_auto_on(message):
    global auto_trading
    if not auto_connected:
        bot.send_message(message.chat.id, "⏳ Connecting auto engine...")
        threading.Thread(target=connect_auto_engine, daemon=True).start()
        return
    auto_trading = True
    bot.send_message(
        message.chat.id,
        "🤖 <b>Auto Signal ON!</b>\n"
        "✅ Watching Rex Signal Alerts\n"
        "✅ Martingale: $1→$2→$4",
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
    bot.send_message(message.chat.id, "👤 <b>Choose Asset:</b>", parse_mode='HTML', reply_markup=markup)

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
    loop = asyncio.new_event_loop()
    async def run():
        order = await client.place_order(asset, amount, direction, expiry*60)
        if not order:
            bot.send_message(int(uid), "❌ Trade failed!")
            return
        bot.send_message(
            int(uid),
            f"✅ <b>Trade Placed!</b>\n"
            f"{'🟢 BUY' if direction=='call' else '🔴 SELL'} {asset}\n"
            f"${amount} | {expiry}min | ⏳ Waiting...",
            parse_mode='HTML'
        )
        await asyncio.sleep(expiry * 60 + 5)
        result = await client.get_order_result(order.order_id)
        if result and result.profit > 0:
            bot.send_message(int(uid), f"🎉 <b>WIN!</b> +${result.profit:.2f}", parse_mode='HTML')
        else:
            bot.send_message(int(uid), f"❌ <b>LOSS</b> -${amount:.2f}", parse_mode='HTML')
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
        f"🤖 Auto Engine: {'🟢 ON' if auto_trading else '🔴 OFF'}\n"
        f"Connected: {'✅' if auto_connected else '❌'}\n"
        f"Trades: {auto_stats['total']} ✅{auto_stats['wins']} ❌{auto_stats['losses']}\n"
        f"Win Rate: {auto_wr:.1f}% | P/L: ${auto_stats['profit']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 Personal: {'✅' if uid in user_clients else '❌'}\n"
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
        return bal

    if auto_connected and auto_client:
        try: text += f"🤖 Auto: ${get_bal(auto_client):.2f} (DEMO)\n"
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
        InlineKeyboardButton("🔵 DEMO",     callback_data="set_demo"),
        InlineKeyboardButton("🔴 REAL",     callback_data="set_real"),
        InlineKeyboardButton("$1",          callback_data="set_amt_1.0"),
        InlineKeyboardButton("$2",          callback_data="set_amt_2.0"),
        InlineKeyboardButton("$5",          callback_data="set_amt_5.0"),
        InlineKeyboardButton("💵 Custom",   callback_data="set_amt_custom")
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
        user_state[str(call.from_user.id)] = 'wait_amount'
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
        "🤖 Auto Signal ON/OFF\n"
        "🔑 My Login — Personal account\n"
        "👤 Manual Trade — Place trades\n"
        "📊 Dashboard — Both engines\n"
        "💰 Balance — Check balances\n"
        "⚙️ Settings — DEMO/REAL & amount\n"
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
        user = get_user(uid)
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
        sig = {'asset':None,'direction':None,'expiry':DEFAULT_EXPIRY,
               'entry_time':None,'martingale_times':[]}
        m = re.search(r'[A-Z]{3}/[A-Z]{3}', text.upper())
        if not m: return None
        sig['asset'] = m.group(0).replace('/', '') + '_otc'
        tu = text.upper()
        if 'BUY' in tu or 'CALL' in tu or '🟩' in text:
            sig['direction'] = 'call'
        elif 'SELL' in tu or 'PUT' in tu or '🟥' in text:
            sig['direction'] = 'put'
        else: return None
        for line in text.split('\n'):
            lu = line.upper()
            if 'ENTRY' in lu:
                m2 = re.search(r'(\d{1,2}:\d{2})', line)
                if m2: sig['entry_time'] = m2.group(1)
            if '1️⃣' in line:
                m2 = re.search(r'(\d{1,2}:\d{2})', line)
                if m2 and len(sig['martingale_times'])==0:
                    sig['martingale_times'].append(m2.group(1))
            elif '2️⃣' in line:
                m2 = re.search(r'(\d{1,2}:\d{2})', line)
                if m2 and len(sig['martingale_times'])<=1:
                    sig['martingale_times'].append(m2.group(1))
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
        f"Entry: {entry_time or 'NOW'} | {expiry}min | ${amount}",
        parse_mode='HTML'
    )

    loop = asyncio.new_event_loop()

    def wait_until(t):
        now    = datetime.now(UTC_MINUS_4)
        target = datetime.strptime(
            f"{now.strftime('%Y-%m-%d')} {t}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=UTC_MINUS_4)
        wait_s = (target - now).total_seconds()
        if wait_s > 0: time.sleep(wait_s)

    if entry_time: wait_until(entry_time)

    async def place(amt):
        try:
            order = await auto_client.place_order(asset, amt, direction, expiry*60)
            return order.order_id if order else None
        except: return None

    async def get_result(trade_id):
        try:
            await asyncio.sleep(expiry * 60 + 5)
            result = await auto_client.get_order_result(trade_id)
            return ('win', result.profit) if result and result.profit > 0 else ('loss', 0)
        except: return 'loss', 0

    # Entry
    trade_id = loop.run_until_complete(place(amount))
    if not trade_id:
        bot.send_message(TELEGRAM_USER_ID, "❌ Entry failed.")
        loop.close(); return
    bot.send_message(TELEGRAM_USER_ID, f"✅ Entry placed! ${amount}")
    result, profit = loop.run_until_complete(get_result(trade_id))
    if result == 'win':
        bot.send_message(TELEGRAM_USER_ID, f"🎉 <b>WIN!</b> +${profit:.2f}", parse_mode='HTML')
        update_auto_stats('win', profit, amount)
        loop.close(); return
    bot.send_message(TELEGRAM_USER_ID, f"❌ Loss → M1 ${amount*2}")
    update_auto_stats('loss', 0, amount)

    # M1
    if len(mg_times) >= 1:
        wait_until(mg_times[0])
        trade_id = loop.run_until_complete(place(amount * 2))
        if trade_id:
            result, profit = loop.run_until_complete(get_result(trade_id))
            if result == 'win':
                bot.send_message(TELEGRAM_USER_ID, f"🎉 <b>WIN M1!</b> +${profit:.2f}", parse_mode='HTML')
                update_auto_stats('win', profit, amount)
                loop.close(); return
            bot.send_message(TELEGRAM_USER_ID, f"❌ Loss M1 → M2 ${amount*4}")
            update_auto_stats('loss', 0, amount)

    # M2
    if len(mg_times) >= 2:
        wait_until(mg_times[1])
        trade_id = loop.run_until_complete(place(amount * 4))
        if trade_id:
            result, profit = loop.run_until_complete(get_result(trade_id))
            if result == 'win':
                bot.send_message(TELEGRAM_USER_ID, f"🎉 <b>WIN M2!</b> +${profit:.2f}", parse_mode='HTML')
                update_auto_stats('win', profit, amount)
            else:
                bot.send_message(TELEGRAM_USER_ID, "❌ Loss M2. Reset.")
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
        logger.info(f"✅ Telethon: {me.first_name} | Watching: {dest.title}")
        bot.send_message(TELEGRAM_USER_ID, f"👀 Watching: {dest.title}")

        @client.on(events.NewMessage(chats=dest))
        async def handler(event):
            if not auto_trading: return
            text = event.message.text or ''
            if not text or should_ignore(text): return
            signal = parse_signal(text)
            if not signal: return
            logger.info(f"Signal: {signal['asset']} {signal['direction']}")
            threading.Thread(
                target=execute_auto_signal,
                args=(signal,), daemon=True
            ).start()

        await client.run_until_disconnected()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(watcher())

# ── Main ───────────────────────────────────────────────────────
def main():
    start_keep_alive()
    logger.info("🚀 Starting QuantomBot V8 Dual Engine...")

    # Connect auto engine in background
    threading.Thread(target=connect_auto_engine, daemon=True).start()

    try:
        bot.send_message(
            TELEGRAM_USER_ID,
            "🤖 <b>QuantomBot V8 LIVE!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔌 Auto engine connecting...\n"
            "👤 Use 🔑 My Login for personal\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "/start to see panel!",
            parse_mode='HTML'
        )
    except: pass

    threading.Thread(target=run_signal_watcher, daemon=True).start()

    logger.info("✅ Bot polling started!")
    bot.infinity_polling(
        timeout=60,
        long_polling_timeout=60,
        skip_pending=True
    )

if __name__ == '__main__':
    while True:
        try:
            main()
        except Exception as e:
            logger.error(f"💥 Crash: {e}")
        logger.warning("🔄 Restarting in 30s...")
        time.sleep(30)
