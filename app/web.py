import os, json, time, hmac, re
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware
from . import db, crypto

app = FastAPI(docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.environ["SECRET_KEY"], same_site="strict",
                   https_only=os.environ.get("COOKIE_SECURE", "1") == "1", max_age=7 * 86400)
app.on_event("startup")(db.init)
def auth(r: Request):
    if not r.session.get("ok"): raise HTTPException(401)
D = [Depends(auth)]
KEYS = {"streak_only", "daily_on", "video_on", "emoji", "send_time", "tz"}

@app.get("/health")
def health(): return {"ok": True}
@app.get("/")
def index(): return FileResponse(os.path.join(os.path.dirname(__file__), "static/index.html"))

@app.post("/api/login")
async def login(r: Request):
    pw = (await r.json()).get("password", "")
    if not hmac.compare_digest(pw.encode(), os.environ["ADMIN_PASSWORD"].encode()):
        time.sleep(1); raise HTTPException(401, "Невірний пароль")
    r.session["ok"] = True; return {"ok": True}
@app.post("/api/logout")
def logout(r: Request): r.session.clear(); return {"ok": True}
@app.get("/api/me")
def me(r: Request): return {"ok": bool(r.session.get("ok"))}

@app.get("/api/status", dependencies=D)
def status():
    with db.cur() as k:
        k.execute("select count(*) c from contacts"); c = k.fetchone()["c"]
        k.execute("select count(*) c from log where kind='letter' and status='ok'"); s = k.fetchone()["c"]
        k.execute("select count(*) c from contacts where enabled"); sel = k.fetchone()["c"]
    hb = float(db.get("heartbeat") or 0); i = int(db.get("alpha_idx"))
    from .worker import ALPHABET
    prof = db.get("profile")
    return {"profile": json.loads(prof) if prof else None, "cmd": db.get("cmd"), "selected": sel, "worker_online": time.time() - hb < 180, "running": db.get("running") == "1",
            "last_run": db.get("last_run"), "next_letter": ALPHABET[i % 33], "contacts": c, "sent": s,
            "auth": db.get("auth") or ("ok" if db.get("cookies") else "none")}
@app.post("/api/running", dependencies=D)
async def running(r: Request): db.put("running", "1" if (await r.json()).get("on") else "0"); return {"ok": True}

@app.get("/api/settings", dependencies=D)
def get_settings(): return {k: db.get(k) for k in KEYS}
@app.put("/api/settings", dependencies=D)
async def put_settings(r: Request):
    d = await r.json()
    if "send_time" in d and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", d["send_time"]): raise HTTPException(400, "Час HH:MM")
    if "tz" in d:
        try: ZoneInfo(d["tz"])
        except Exception: raise HTTPException(400, "Невірний часовий пояс")
    for k, v in d.items():
        if k in KEYS: db.put(k, v)
    return {"ok": True}

@app.get("/api/contacts", dependencies=D)
def contacts(q: str = ""):
    with db.cur() as k:
        k.execute("select name,streak,enabled from contacts where name ilike %s order by streak desc,name", (f"%{q}%",))
        return k.fetchall()
@app.post("/api/contacts/toggle", dependencies=D)
async def toggle(r: Request):
    d = await r.json()
    with db.cur() as k: k.execute("update contacts set enabled=%s where name=%s", (bool(d["enabled"]), d["name"]))
    return {"ok": True}

@app.get("/api/history", dependencies=D)
def history(kind: str = "all", limit: int = 200):
    kinds = {"letter": ["letter"], "video": ["video_in", "video_reply"]}.get(kind)
    with db.cur() as k:
        k.execute("select to_char(ts at time zone 'Europe/Kyiv','DD.MM.YYYY HH24:MI:SS') ts,kind,contact,detail,status from log "
                  "where (%s::text[] is null or kind=any(%s)) order by id desc limit %s", (kinds, kinds, min(limit, 500)))
        return k.fetchall()

@app.post("/api/cookies", dependencies=D)
async def set_cookies(r: Request):
    raw = json.loads((await r.json())["cookies"]); out = []
    for c in raw:  # нормалізація експорту з розширень браузера під Playwright
        x = {"name": c["name"], "value": c["value"], "domain": c.get("domain", ".tiktok.com"),
             "path": c.get("path", "/"), "secure": True}
        e = c.get("expirationDate") or c.get("expires")
        if isinstance(e, (int, float)) and e > 0: x["expires"] = e
        out.append(x)
    if not any(c["name"] == "sessionid" for c in out): raise HTTPException(400, "Немає cookie sessionid")
    db.put("cookies", crypto.enc(json.dumps(out))); db.put("auth", "pending"); db.put("profile", ""); db.put("cmd", "check"); return {"ok": True}
@app.delete("/api/cookies", dependencies=D)
def del_cookies():
    with db.cur() as k: k.execute("delete from settings where key in ('cookies','auth','profile')")
    db.put("running", "0"); return {"ok": True}

@app.post("/api/contacts/bulk", dependencies=D)
async def bulk(r: Request):
    d = await r.json()
    with db.cur() as k:
        k.execute("update contacts set enabled=%s where streak or not %s", (bool(d["enabled"]), bool(d.get("streak_only"))))
    return {"ok": True}
@app.post("/api/contacts/add", dependencies=D)
async def add(r: Request):
    n = ((await r.json()).get("name") or "").strip()
    if not n: raise HTTPException(400, "Введіть ім'я")
    with db.cur() as k: k.execute("insert into contacts(name,enabled) values(%s,true) on conflict do nothing", (n,))
    return {"ok": True}
@app.post("/api/refresh", dependencies=D)
def refresh(): db.put("cmd", "refresh"); return {"ok": True}
@app.post("/api/run_now", dependencies=D)
def run_now(): db.put("cmd", "run_now"); return {"ok": True}

@app.post("/api/check", dependencies=D)
def check(): db.put("cmd", "check"); return {"ok": True}

@app.get("/api/debug", dependencies=D)
def debug():
    i = db.get("debug_info"); return {"info": json.loads(i) if i else None, "shot": db.get("debug_shot")}
