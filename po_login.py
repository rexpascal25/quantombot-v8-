# ============================================================
# Pocket Option Auto Login — Playwright Edition
# No Selenium! Pure Playwright WebSocket capture
# ============================================================

import os, time, logging, requests, re
logger = logging.getLogger(__name__)

PO_EMAIL    = os.environ.get('PO_EMAIL', '')
PO_PASSWORD = os.environ.get('PO_PASSWORD', '')
CAPTCHA_KEY = os.environ.get('CAPTCHA_KEY', '')

def solve_captcha(site_key, url):
    if not CAPTCHA_KEY:
        return None
    try:
        resp = requests.post('http://2captcha.com/in.php', data={
            'key': CAPTCHA_KEY, 'method': 'userrecaptcha',
            'googlekey': site_key, 'pageurl': url, 'json': 1
        })
        result = resp.json()
        if result.get('status') != 1:
            return None
        captcha_id = result['request']
        logger.info(f"Captcha ID: {captcha_id}")
        for _ in range(36):
            time.sleep(5)
            resp   = requests.get('http://2captcha.com/res.php', params={
                'key': CAPTCHA_KEY, 'action': 'get',
                'id': captcha_id, 'json': 1
            })
            result = resp.json()
            if result.get('status') == 1:
                logger.info("✅ Captcha solved!")
                return result['request']
            if result.get('request') != 'CAPCHA_NOT_READY':
                return None
        return None
    except Exception as e:
        logger.error(f"Captcha error: {e}")
        return None

def get_fresh_ssid():
    if not PO_EMAIL or not PO_PASSWORD:
        logger.error("Email/password not set!")
        return None

    captured_session = [None]

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-setuid-sandbox',
                ]
            )
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080}
            )

            page = context.new_page()

            # Intercept WebSocket to capture session
            def on_websocket(ws):
                logger.info(f"🔌 WS opened: {ws.url[:50]}")

                def on_sent(payload):
                    try:
                        if 'auth' in str(payload) and 'session' in str(payload):
                            match = re.search(r'"session"\s*:\s*"([^"]{10,})"', str(payload))
                            if match:
                                captured_session[0] = match.group(1)
                                logger.info(f"✅ WS session captured (sent): {captured_session[0][:15]}...")
                    except: pass

                def on_received(payload):
                    try:
                        if 'session' in str(payload) and len(str(payload)) > 20:
                            match = re.search(r'"session"\s*:\s*"([^"]{10,})"', str(payload))
                            if match and not captured_session[0]:
                                captured_session[0] = match.group(1)
                                logger.info(f"✅ WS session captured (recv): {captured_session[0][:15]}...")
                    except: pass

                ws.on('framesent',     lambda payload: on_sent(payload))
                ws.on('framereceived', lambda payload: on_received(payload))

            page.on('websocket', on_websocket)

            # Go to login page
            logger.info("🌐 Opening PO login page...")
            page.goto('https://pocketoption.com/en/login/', timeout=30000)
            time.sleep(3)

            # Fill credentials
            page.fill('input[name="email"]',    PO_EMAIL)
            logger.info("✅ Email filled")
            time.sleep(1)
            page.fill('input[name="password"]', PO_PASSWORD)
            logger.info("✅ Password filled")
            time.sleep(1)

            # Handle captcha
            try:
                recaptcha = page.query_selector('.g-recaptcha')
                if recaptcha:
                    site_key = recaptcha.get_attribute('data-sitekey')
                    if site_key:
                        logger.info("🔐 Solving captcha...")
                        token = solve_captcha(site_key, 'https://pocketoption.com/en/login/')
                        if token:
                            page.evaluate(f'''() => {{
                                document.getElementById("g-recaptcha-response").innerHTML="{token}";
                                try {{ ___grecaptcha_cfg.clients[0].aa.l.callback("{token}"); }} catch(e) {{}}
                            }}''')
                            logger.info("✅ Captcha injected!")
                            time.sleep(2)
            except Exception as e:
                logger.info(f"No captcha: {e}")

            # Click login
            try:
                page.click('button[type="submit"]')
            except:
                page.click('form button')
            logger.info("✅ Login clicked")

            # Wait for redirect
            for _ in range(30):
                url = page.url
                if any(x in url for x in ['cabinet', 'trade', 'quick', 'dashboard']):
                    logger.info(f"✅ Logged in! URL: {url}")
                    break
                time.sleep(2)
            else:
                logger.warning(f"No redirect, current URL: {page.url}")

            # Wait for WebSocket session
            logger.info("⏳ Waiting for WS session...")
            for i in range(30):
                time.sleep(2)
                if captured_session[0]:
                    logger.info(f"✅ Session ready after {i*2}s!")
                    break
                logger.info(f"Waiting {i+1}/30...")

            browser.close()

            if captured_session[0]:
                return captured_session[0]

            logger.error("❌ No session captured!")
            return None

    except ImportError:
        logger.error("Playwright not installed!")
        return None
    except Exception as e:
        logger.error(f"Login error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def auto_login_and_get_ssid():
    logger.info("🔐 Auto login starting...")
    for attempt in range(3):
        logger.info(f"Attempt {attempt+1}/3")
        ssid = get_fresh_ssid()
        if ssid:
            logger.info("✅ Session obtained!")
            return ssid
        logger.warning(f"Attempt {attempt+1} failed, retrying in 15s...")
        time.sleep(15)
    logger.error("All attempts failed!")
    return None
