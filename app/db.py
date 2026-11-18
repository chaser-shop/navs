import os, psycopg2, psycopg2.extras
from contextlib import contextmanager
DEFAULTS = {"daily_on": "1", "video_on": "1", "emoji": "🙂", "send_time": "00:01",
            "tz": "Europe/Kyiv", "running": "0", "alpha_idx": "0", "last_day": "", "streak_only": "0", "cmd": ""}
SCHEMA = """
create table if not exists settings(key text primary key, value text);
create table if not exists contacts(name text primary key, streak bool default false,
  enabled bool default true, seen_at timestamptz default now());
alter table contacts add column if not exists avatar text;
create table if not exists log(id serial primary key, ts timestamptz default now(),
  kind text, contact text, detail text, status text);
create table if not exists sent(day date, contact text, primary key(day, contact));
create table if not exists seen_videos(contact text, video_id text, primary key(contact, video_id));
"""
@contextmanager
def cur():
    c = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as k:
            yield k
    finally:
        c.close()
def init():
    with cur() as k: k.execute(SCHEMA)
def get(key):
    with cur() as k:
        k.execute("select value from settings where key=%s", (key,)); r = k.fetchone()
    return r["value"] if r else DEFAULTS.get(key)
def put(key, val):
    with cur() as k:
        k.execute("insert into settings values(%s,%s) on conflict(key) do update set value=excluded.value", (key, str(val)))
def log(kind, contact, detail, status="ok"):
    with cur() as k:
        k.execute("insert into log(kind,contact,detail,status) values(%s,%s,%s,%s)", (kind, contact, detail, status))
