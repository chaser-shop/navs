"""Фоновий воркер: керує TikTok Web через Playwright (без офіційного API).
ВАЖЛИВО: селектори в SEL треба звірити з реальною версткою tiktok.com/messages."""
import time, re, json, base64, datetime as dt
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright
from . import db, crypto

ALPHABET = list("АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ")  # 33
CATCHUP_HOURS = 6      # пропущене завдання старше цього — не виконується
VIDEO_EVERY = 180      # сек між перевірками відео
SEL = dict(item='[data-e2e="chat-list-item"]', name='[data-e2e="chat-nickname"]',
           msg='[data-e2e="chat-item"]', alt='[class*="ConversationItem"]',
           input='[data-e2e="message-input-area"] [contenteditable="true"], div[contenteditable="true"]')
_last_video = 0
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def grab_profile(pg):
    try:
        info = pg.evaluate("""() => {
          const a = document.querySelector('a[data-e2e="nav-profile"]');
          const img = document.querySelector('[data-e2e="profile-icon"] img') || (a && a.querySelector('img'));
          const u = a && (a.getAttribute('href') || '').split('@')[1];
          return {user: u ? u.split('/')[0].split('?')[0] : null, avatar: img ? img.src : null};
        }""")
        if not info.get("user") and not info.get("avatar"):
            db.log("info", None, "Профіль на сторінці не знайдено (селектори треба уточнити)"); return
        avatar = None
        if info.get("avatar"):
            try:
                r = pg.context.request.get(info["avatar"]); body = r.body()
                if len(body) < 150000:
                    avatar = "data:" + r.headers.get("content-type", "image/jpeg") + ";base64," + base64.b64encode(body).decode()
            except Exception: pass
        db.put("profile", json.dumps({"user": info.get("user") or "", "avatar": avatar}, ensure_ascii=False))
        db.log("info", None, "Підключено як @" + (info.get("user") or "?"))
    except Exception as e:
        db.log("info", None, "Не вдалося прочитати профіль: " + str(e)[:150], "error")

def diag(pg):
    try:
        url, title = pg.url, pg.title()
        e2e = pg.evaluate("() => [...new Set([...document.querySelectorAll('[data-e2e]')].map(e => e.getAttribute('data-e2e')))].slice(0, 80)")
        db.put("debug_shot", base64.b64encode(pg.screenshot(type="jpeg", quality=40)).decode())
        db.put("debug_info", json.dumps({"url": url, "title": title, "e2e": e2e}, ensure_ascii=False))
        db.log("info", None, f"Діагностика: {url} | {title}", "error")
        return "login" in url.lower() or "login" in title.lower()
    except Exception as e:
        db.log("info", None, "Діагностика не вдалася: " + str(e)[:150], "error"); return False

def open_page(p):
    enc = db.get("cookies")
    if not enc: raise RuntimeError("Акаунт не підключено (немає cookies)")
    b = p.chromium.launch(args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(locale="uk-UA", timezone_id="Europe/Kyiv", viewport={"width": 1280, "height": 900}, user_agent=UA)
    ctx.add_cookies(json.loads(crypto.dec(enc)))
    pg = ctx.new_page(); pg.goto("https://www.tiktok.com/messages", wait_until="domcontentloaded")
    try: pg.wait_for_selector(SEL["item"] + ", " + SEL["alt"], timeout=45000)
    except Exception:
        login = diag(pg); b.close()
        if login: db.put("auth", "expired"); raise RuntimeError("Сесія TikTok недійсна або потрібен вхід/капча")
        raise RuntimeError("Список чатів не знайдено (капча або інша верстка). Див. «Діагностика» в Налаштуваннях")
    db.put("auth", "ok"); db.log("info", None, "TikTok відкрито, сесія активна")
    if not db.get("profile"): grab_profile(pg)
    return b, pg


def parse_item(text, nick=None):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    name = (nick or (lines[0] if lines else "")).strip()
    preview = lines[1] if len(lines) > 1 else ""
    tm = next((l for l in lines[2:] if re.fullmatch(r"\d{1,2}[:.]\d{1,2}([.]\d{2,4})?", l)), "")
    return name, preview, tm

def item_info(it):
    n = it.query_selector(SEL["name"])
    return parse_item(it.inner_text(), n.inner_text() if n else None)

def items(pg):
    return pg.query_selector_all(SEL["item"]) or pg.query_selector_all(SEL["alt"])

def is_video(p):  # у списку чатів: «Поділився(-лась) відео»; власні повідомлення починаються з «Ви»
    t = p.lower().strip()
    if t.startswith(("ви ", "you ")): return False
    return ("поділи" in t and "відео" in t) or "shared a video" in t

def scan(pg):  # вогник: лише за реальним елементом; текст 🔥 у повідомленні — це не серія
    out = []
    for it in items(pg):
        name = item_info(it)[0]
        if name: out.append((name, it.query_selector('[data-e2e*="streak"], [aria-label*="treak"], [aria-label*="огник"]') is not None))
    return out


def open_chat(pg, name):  # точний збіг ніку (раніше було «входить у текст» — помилково чіпало інші чати)
    for it in items(pg):
        if item_info(it)[0] == name: it.click(); pg.wait_for_timeout(1500); return True
    return False

def send(pg, text):
    box = pg.locator(SEL["input"]).first; box.click(); box.fill(text)
    pg.keyboard.press("Enter"); pg.wait_for_timeout(800)

def chat_videos(pg):  # лише ВХІДНІ відео (повідомлення в лівій половині екрана)
    out = []
    for m in pg.query_selector_all(SEL["msg"]):
        a = m.query_selector('a[href*="/video/"]'); bb = m.bounding_box()
        mid = re.search(r"/video/(\d+)", a.get_attribute("href") or "") if a else None
        if mid and bb and bb["x"] + bb["width"] / 2 < 640: out.append(mid.group(1))
    return out

def sync_contacts(pg):
    found = scan(pg)
    with db.cur() as k:
        k.execute("update contacts set streak=false")
        for n, s in found:
            k.execute("insert into contacts(name,streak,enabled) values(%s,%s,false) on conflict(name) do update "
                      "set streak=excluded.streak, seen_at=now()", (n, s))
    db.log("info", None, f"Знайдено чатів: {len(found)}, з вогником: {sum(1 for _, s in found if s)}")

def daily(pg, today):
    idx = int(db.get("alpha_idx")) % len(ALPHABET); letter = ALPHABET[idx]
    sync_contacts(pg)
    with db.cur() as k:
        k.execute("select name from contacts where enabled and (streak or %s='0')", (db.get("streak_only"),))
        names = [r["name"] for r in k.fetchall()]
    db.log("info", None, f"Літера «{letter}»: вибрано для надсилання {len(names)}")
    for n in names:
        with db.cur() as k:
            k.execute("insert into sent values(%s,%s) on conflict do nothing", (today, n)); fresh = k.rowcount == 1
        if not fresh: continue  # захист від дублів
        try:
            if not open_chat(pg, n): raise RuntimeError("чат не знайдено")
            send(pg, letter); db.log("letter", n, letter)
        except Exception as e: db.log("letter", n, letter, "error: " + str(e)[:200])
    db.put("alpha_idx", (idx + 1) % len(ALPHABET))


def videos(pg):
    emoji = db.get("emoji"); today = dt.datetime.now(ZoneInfo(db.get("tz"))).date().isoformat()
    with db.cur() as k:
        k.execute("select name from contacts where enabled"); on = {r["name"] for r in k.fetchall()}
    todo = []
    for it in items(pg):
        name, preview, tm = item_info(it)
        if name not in on: continue
        with db.cur() as k:
            k.execute("insert into seen_videos values(%s,'__base__') on conflict do nothing", (name,)); base = k.rowcount == 1
            if not is_video(preview): continue
            k.execute("insert into seen_videos values(%s,%s) on conflict do nothing", (name, f"{today}|{preview}|{tm}"))
            if k.rowcount == 1 and not base: todo.append(name)  # перший прохід лише запам'ятовує
    for name in todo:
        db.log("video_in", name, "відео")
        try:
            if not open_chat(pg, name): raise RuntimeError("чат не знайдено")
            send(pg, emoji); db.log("video_reply", name, emoji)
        except Exception as e: db.log("video_reply", name, emoji, "error: " + str(e)[:200])
    if todo: db.log("info", None, f"Нових відео: {len(todo)}")

def tick():
    global _last_video
    if db.get("running") != "1": return
    now = dt.datetime.now(ZoneInfo(db.get("tz"))); today = now.date().isoformat()
    h, m = map(int, db.get("send_time").split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    due = db.get("daily_on") == "1" and now >= target and db.get("last_day") != today
    if due and now - target > dt.timedelta(hours=CATCHUP_HOURS):
        db.put("last_day", today); db.log("letter", None, "Пропущено: надто велике запізнення", "skipped"); due = False
    if due:  # атомарно «займаємо» день
        with db.cur() as k:
            k.execute("insert into settings values('last_day',%s) on conflict(key) do update set value=excluded.value "
                      "where settings.value<>excluded.value", (today,)); due = k.rowcount == 1
    vid = db.get("video_on") == "1" and time.time() - _last_video > VIDEO_EVERY
    if not (due or vid): return
    try:
        with sync_playwright() as p:
            b, pg = open_page(p)
            try:
                if due: daily(pg, today); db.put("last_run", now.isoformat())
                if vid: videos(pg); _last_video = time.time()
            finally: b.close()
    except Exception:
        if due: db.put("last_day", "")  # повторимо; sent-таблиця не дасть дублів
        raise

def run_cmd():
    cmd = db.get("cmd")
    if not cmd: return False
    db.put("cmd", ""); db.log("info", None, "Команда з панелі: " + cmd)
    now = dt.datetime.now(ZoneInfo(db.get("tz")))
    with sync_playwright() as p:
        b, pg = open_page(p)
        try:
            if cmd == "refresh": sync_contacts(pg)
            elif cmd == "check": db.log("info", None, "Підключення перевірено: успішно")
            elif cmd == "run_now":
                daily(pg, now.date().isoformat()); db.put("last_day", now.date().isoformat()); db.put("last_run", now.isoformat())
        finally: b.close()
    return True

def main():
    db.init()
    while True:
        try:
            if not run_cmd(): tick()
        except Exception as e: db.log("worker", None, str(e)[:300], "error")
        db.put("heartbeat", time.time()); time.sleep(60)

if __name__ == "__main__": main()
