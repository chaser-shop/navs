"""Фоновий воркер: керує TikTok Web через Playwright (без офіційного API).
ВАЖЛИВО: селектори в SEL треба звірити з реальною версткою tiktok.com/messages."""
import time, re, json, datetime as dt
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright
from . import db, crypto

ALPHABET = list("АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ")  # 33
CATCHUP_HOURS = 6      # пропущене завдання старше цього — не виконується
VIDEO_EVERY = 180      # сек між перевірками відео
SEL = dict(item='[data-e2e="chat-list-item"]', name='[data-e2e="chat-nickname"]',
           msg='[data-e2e="chat-item"]',
           input='[data-e2e="message-input-area"] [contenteditable="true"]')
_last_video = 0

def open_page(p):
    enc = db.get("cookies")
    if not enc: raise RuntimeError("Акаунт не підключено (немає cookies)")
    b = p.chromium.launch(args=["--no-sandbox"])
    ctx = b.new_context(locale="uk-UA", timezone_id="Europe/Kyiv", viewport={"width": 1280, "height": 900})
    ctx.add_cookies(json.loads(crypto.dec(enc)))
    pg = ctx.new_page(); pg.goto("https://www.tiktok.com/messages", wait_until="domcontentloaded")
    try: pg.wait_for_selector(SEL["item"], timeout=30000)
    except Exception: b.close(); db.put("auth", "expired"); raise RuntimeError("Сесія TikTok недійсна або потрібна капча")
    db.put("auth", "ok"); return b, pg

def scan(pg):
    out = []
    for it in pg.query_selector_all(SEL["item"]):
        n = it.query_selector(SEL["name"]); txt = it.inner_text()
        out.append(((n.inner_text() if n else txt.split("\n")[0]).strip(),
                    "🔥" in txt or it.query_selector('[data-e2e*="streak"]') is not None))
    return out

def open_chat(pg, name):
    for it in pg.query_selector_all(SEL["item"]):
        if name in it.inner_text(): it.click(); pg.wait_for_timeout(1500); return True
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

def daily(pg, today):
    idx = int(db.get("alpha_idx")) % len(ALPHABET); letter = ALPHABET[idx]
    with db.cur() as k:
        k.execute("update contacts set streak=false")
        for n, s in scan(pg):
            k.execute("insert into contacts(name,streak) values(%s,%s) on conflict(name) do update "
                      "set streak=excluded.streak, seen_at=now()", (n, s))
        k.execute("select name from contacts where streak and enabled"); names = [r["name"] for r in k.fetchall()]
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
    emoji = db.get("emoji")
    with db.cur() as k:
        k.execute("select name from contacts where not enabled"); off = {r["name"] for r in k.fetchall()}
    for n, _ in scan(pg):
        if n in off or not open_chat(pg, n): continue
        ids = chat_videos(pg)
        with db.cur() as k:
            k.execute("insert into seen_videos values(%s,'__base__') on conflict do nothing", (n,)); base = k.rowcount == 1
            for v in ids:
                k.execute("insert into seen_videos values(%s,%s) on conflict do nothing", (n, v))
                if k.rowcount == 1 and not base:  # першу появу чату лише запам'ятовуємо
                    db.log("video_in", n, v)
                    try: send(pg, emoji); db.log("video_reply", n, emoji)
                    except Exception as e: db.log("video_reply", n, emoji, "error: " + str(e)[:200])

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

def main():
    db.init()
    while True:
        try: tick()
        except Exception as e: db.log("worker", None, str(e)[:300], "error")
        db.put("heartbeat", time.time()); time.sleep(60)

if __name__ == "__main__": main()
