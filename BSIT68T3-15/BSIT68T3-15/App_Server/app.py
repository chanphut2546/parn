# -*- coding: utf-8 -*-
# app.py บน Raspberry Pi 4  -- ฉบับที่รันได้ (ประกอบโค้ดจากเล่ม + ส่วนที่เขียนเพิ่ม)
#
# *** อ่านก่อน ***
# ไฟล์นี้ประกอบจาก 2 แหล่ง ซึ่งมีป้ายกำกับในแต่ละส่วน:
#   [จากเล่ม ค.2]  = โค้ดที่ตีพิมพ์ในภาคผนวก ค.2 ของปริญญานิพนธ์ BSIT68T3-15 (คงไว้ตามเดิม
#                    ยกเว้นจุดที่ระบุว่า "แก้เล็กน้อย") ฉบับเดิมอยู่ในภาคผนวก ค.2 ของเล่ม
#   [เขียนเพิ่ม]    = โค้ดที่เขียนขึ้นใหม่ตามสเปกในเล่ม (บทที่ 5.6, ภาคผนวก ก) เพื่อให้รันได้
#                    ไม่ใช่ต้นฉบับของผู้จัดทำ หน้าตาการ์ด LINE/หน้ากราฟ/ชื่อฟังก์ชันอาจต่างจากของจริง
#                    ถ้ามีต้นฉบับบน Raspberry Pi (~/linebot/app.py) ให้ใช้ต้นฉบับนั้นแทน
# รหัส LINE, API Key, Token อ่านจากไฟล์ .env (ดู .env.example) จึงไม่ปรากฏในโค้ด

import os
import time
import math
import hmac
import sqlite3
import logging
import threading
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs

from flask import Flask, request, jsonify, abort, Response
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, PostbackEvent, TextSendMessage, FlexSendMessage,
)

try:                                   # อ่านไฟล์ .env ถ้ามีไลบรารี python-dotenv
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==============================================================================
# [เขียนเพิ่ม] ค่าตั้งค่าจาก .env
# ==============================================================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
DEVICE_API_KEY = os.environ.get("DEVICE_API_KEY", "")        # ESP32 ส่งมาใน X-API-Key
GRAPH_TOKEN = os.environ.get("GRAPH_TOKEN", "")              # รหัสในลิงก์หน้ากราฟ
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")   # https://<โดเมน ngrok>
ALLOWED_USER_IDS = {u.strip() for u in os.environ.get("ALLOWED_USER_IDS", "").split(",") if u.strip()}
LOCAL_TZ = timezone(timedelta(hours=7))                      # เวลาไทย

# ==============================================================================
# [จากเล่ม ค.2] ค่าตั้งต้นของระบบ
# ==============================================================================
DB_PATH = os.path.expanduser(os.environ.get("DB_PATH", "~/linebot/readings.db"))  # แก้เล็กน้อย: อ่านจาก env ได้
DEVICE_ID = "esp32-1"
RELAY_COUNT = 4
ONLINE_TIMEOUT = 60
OFFLINE_AFTER = 120      # ไม่ได้ยินจาก ESP32 เกิน 120 วินาที ถือว่าขาดการเชื่อมต่อ
WATCH_INTERVAL = 15      # ตรวจการเชื่อมต่อทุก 15 วินาที
MAX_WAIT = 25            # Long Polling รอได้สูงสุด 25 วินาที
MAX_ON_MIN = 30          # รีเลย์แต่ละช่องเปิดค้างได้ไม่เกิน 30 นาที
WATER_DEFAULT_MIN = int(os.environ.get("WATER_DEFAULT_MIN", "10"))
PUMP_CHANNEL = int(os.environ.get("PUMP_CHANNEL", str(RELAY_COUNT)))

# [เขียนเพิ่ม] โซนที่แสดงเป็นปุ่มบนการ์ด "รดน้ำ" (ช่องรีเลย์ที่ไม่ใช่ปั๊ม)
# สวนจริงมีปั๊มตัวเดียว ทุกโซนจึงสั่งเปิดปั๊มตัวเดียวกัน (ตามเล่มข้อ 5.1)
ZONES = [int(x) for x in os.environ.get("ZONES", "1,2,3").split(",")
         if x.strip() and int(x) != PUMP_CHANNEL]

# [เขียนเพิ่ม] ตัวแปรหลักของแอป
app = Flask(__name__)
log = logging.getLogger("sprinkler")
_relay_changed = threading.Condition()   # ปลุก Long Polling เมื่อสถานะรีเลย์เปลี่ยน
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN or "dummy-token")
handler = WebhookHandler(LINE_CHANNEL_SECRET or "dummy-secret")

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


# ==============================================================================
# [จากเล่ม ค.2] ฐานข้อมูล SQLite: สร้างตารางเมื่อเริ่มทำงาน
# ==============================================================================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS readings (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " device TEXT, temperature REAL, humidity REAL, ts REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS relays (device TEXT, ch INTEGER, state INTEGER,"
        " ts REAL, PRIMARY KEY (device, ch))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS device_state (device TEXT PRIMARY KEY,"
        " last_seen REAL, reported TEXT, offline_notified INTEGER DEFAULT 0)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pump_log (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts REAL, state INTEGER, source TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS pump_log_ts ON pump_log (ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS readings_ts ON readings (ts)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS timers (device TEXT, ch INTEGER, off_at REAL,"
        " minutes INTEGER, PRIMARY KEY (device, ch))"
    )
    return conn


# ==============================================================================
# [เขียนเพิ่ม] ฟังก์ชันช่วยที่ไม่มีในเล่ม (ฐานข้อมูล / อุปกรณ์ / LINE)
# ==============================================================================
def set_alert_target(user_id):
    """จำผู้ใช้ LINE ล่าสุดที่สั่งงาน เพื่อใช้ส่งข้อความแจ้งเตือน (เก็บในตาราง kv)"""
    with db() as conn:
        conn.execute(
            "INSERT INTO kv (k, v) VALUES ('alert_user', ?)"
            " ON CONFLICT(k) DO UPDATE SET v=excluded.v", (user_id,)
        )


def get_alert_target():
    with db() as conn:
        row = conn.execute("SELECT v FROM kv WHERE k='alert_user'").fetchone()
    return row[0] if row else None


def push_messages(messages):
    """ส่งข้อความหาผู้ใช้ที่จำไว้ คืน True ถ้าส่งสำเร็จ"""
    to = get_alert_target()
    if not to or not LINE_CHANNEL_ACCESS_TOKEN:
        log.warning("ยังไม่มีผู้รับแจ้งเตือนหรือยังไม่ได้ตั้ง LINE token ข้ามการ push")
        return False
    try:
        line_bot_api.push_message(to, messages)
        return True
    except Exception:
        log.exception("push error")
        return False


def touch_device(device, known=""):
    """บันทึกว่าเพิ่งได้ยินจาก ESP32 คืน (วินาทีที่เงียบไปก่อนหน้านี้, เคยแจ้งออฟไลน์แล้วหรือยัง)"""
    now = time.time()
    with db() as conn:
        row = conn.execute(
            "SELECT last_seen, offline_notified FROM device_state WHERE device=?", (device,)
        ).fetchone()
        if row:
            gap = now - (row[0] or now)
            notified = row[1] or 0
            conn.execute(
                "UPDATE device_state SET last_seen=?, reported=? WHERE device=?",
                (now, known, device),
            )
        else:
            gap, notified = 0, 0
            conn.execute(
                "INSERT INTO device_state (device, last_seen, reported, offline_notified)"
                " VALUES (?,?,?,0)", (device, now, known),
            )
    return gap, notified


def esp_online(device):
    with db() as conn:
        row = conn.execute("SELECT last_seen FROM device_state WHERE device=?", (device,)).fetchone()
    return bool(row and row[0] and time.time() - row[0] <= ONLINE_TIMEOUT)


def get_relays(device):
    """สถานะที่ต้องการของรีเลย์ช่อง 1..RELAY_COUNT เป็นลิสต์ 0/1"""
    with db() as conn:
        rows = dict(conn.execute("SELECT ch, state FROM relays WHERE device=?", (device,)).fetchall())
    return [int(rows.get(ch, 0)) for ch in range(1, RELAY_COUNT + 1)]


def get_timers(device):
    """{ช่อง: นาทีที่เหลือ} ของตัวจับเวลาที่ยังไม่หมด"""
    now = time.time()
    with db() as conn:
        rows = conn.execute("SELECT ch, off_at FROM timers WHERE device=?", (device,)).fetchall()
    return {ch: max(1, math.ceil((off_at - now) / 60)) for ch, off_at in rows if off_at > now}


def running_zones():
    """{ช่องโซน: นาทีที่เหลือ} ของโซนที่กำลังรดอยู่ (ไม่รวมช่องปั๊ม)"""
    relays = get_relays(DEVICE_ID)
    timers = get_timers(DEVICE_ID)
    return {ch: timers.get(ch, 0) for ch in range(1, RELAY_COUNT + 1)
            if ch != PUMP_CHANNEL and relays[ch - 1] == 1}


def names_of(channels):
    return ", ".join("ปั๊มน้ำ" if c == PUMP_CHANNEL else f"โซน {c}" for c in channels)


def start_on(user_id, channels, minutes):
    """เปิดช่องที่ระบุ (กรณีสั่งปั๊มโดยตรง ไม่มีโซน)"""
    minutes = max(1, min(int(minutes), MAX_ON_MIN))
    set_alert_target(user_id)
    set_relays(DEVICE_ID, channels, 1, minutes)
    return [confirm_on_card(channels, minutes)]


def stop_water(user_id):
    """หยุดน้ำ: ตั้งรีเลย์ปิดทุกช่อง (ESP32 จะกดรีเลย์ STOP)"""
    set_alert_target(user_id)
    was_on = any(get_relays(DEVICE_ID))
    set_relays(DEVICE_ID, range(1, RELAY_COUNT + 1), 0, source="stop")
    msgs = [stopped_card(was_on)]
    if not esp_online(DEVICE_ID):
        msgs.append(plain("หมายเหตุ: อุปกรณ์ที่สวนออฟไลน์อยู่ คำสั่งจะมีผลเมื่ออุปกรณ์กลับมาเชื่อมต่อ "
                          "หากต้องการหยุดทันทีให้กดปุ่ม STOP ที่ตู้ควบคุม"))
    return msgs


def local_str(ts, fmt="%d/%m %H:%M"):
    return datetime.fromtimestamp(ts, LOCAL_TZ).strftime(fmt)


# ==============================================================================
# [เขียนเพิ่ม] การ์ดข้อความ LINE (Flex Message)  -- หน้าตาเป็นแบบง่าย ไม่ใช่ของจริง
# ==============================================================================
def plain(text):
    return TextSendMessage(text=text)


def _button(label, data=None, uri=None, style="primary"):
    action = ({"type": "uri", "label": label, "uri": uri} if uri else
              {"type": "postback", "label": label, "data": data, "displayText": label})
    return {"type": "button", "style": style, "action": action}


def _card(alt, title, lines, color="#1DB446", buttons=None):
    body = [{"type": "text", "text": title, "weight": "bold", "size": "lg", "color": color, "wrap": True}]
    body += [{"type": "text", "text": t, "size": "sm", "wrap": True, "margin": "md"} for t in lines]
    bubble = {"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": body}}
    if buttons:
        bubble["footer"] = {"type": "box", "layout": "vertical", "spacing": "sm", "contents": buttons}
    return FlexSendMessage(alt_text=alt, contents=bubble)


def water_menu_card():
    buttons = [_button(f"รดน้ำ โซน {c} ({WATER_DEFAULT_MIN} นาที)", f"act=water&ch={c}") for c in ZONES]
    return _card("เลือกโซนที่ต้องการรดน้ำ", "เลือกโซนที่ต้องการรดน้ำ",
                 [f"ระบบรดน้ำครั้งละ {WATER_DEFAULT_MIN} นาที แล้วปิดเอง"], buttons=buttons)


def confirm_on_card(channels, minutes):
    return _card("เริ่มรดน้ำแล้ว", "เริ่มรดน้ำแล้ว",
                 [f"{names_of(channels)}", f"ระบบจะปิดน้ำเองในอีก {minutes} นาที"],
                 buttons=[_button("หยุดน้ำ", "act=off_all", style="secondary")])


def done_card(channels, minutes):
    return _card("รดน้ำเสร็จแล้ว", "รดน้ำเสร็จแล้ว",
                 [f"{names_of(channels)} รดครบ {minutes} นาที ปิดปั๊มเรียบร้อย"])


def stopped_card(was_on):
    return _card("หยุดน้ำแล้ว", "หยุดน้ำแล้ว",
                 ["สั่งหยุดปั๊มเรียบร้อย" if was_on else "ขณะนี้ปั๊มไม่ได้ทำงานอยู่"], color="#E67E22")


def offline_card():
    return _card("อุปกรณ์ขาดการเชื่อมต่อ", "อุปกรณ์ที่สวนขาดการเชื่อมต่อ",
                 ["ระบบสั่งปิดน้ำไว้ก่อนเพื่อความปลอดภัย",
                  "ยังกดปุ่ม START/STOP ที่ตู้ควบคุมได้ตามปกติ"], color="#D93025")


def back_online_card():
    return _card("อุปกรณ์กลับมาเชื่อมต่อแล้ว", "อุปกรณ์กลับมาเชื่อมต่อแล้ว",
                 ["สถานะปั๊มคือปิด ระบบจะไม่เปิดน้ำเอง หากต้องการรดน้ำให้กด \"รดน้ำ\" อีกครั้ง"])


def cannot_open_card():
    return _card("สั่งรดน้ำไม่ได้", "สั่งรดน้ำไม่ได้ในขณะนี้",
                 ["อุปกรณ์ที่สวนออฟไลน์อยู่ (อินเทอร์เน็ตที่สวนอาจไม่ถึง)",
                  "ลองใหม่อีกครั้ง หรือกดปุ่ม START ที่ตู้ควบคุมโดยตรง"], color="#D93025")


def status_card():
    with db() as conn:
        row = conn.execute(
            "SELECT temperature, humidity, ts FROM readings WHERE device=? ORDER BY ts DESC LIMIT 1",
            (DEVICE_ID,),
        ).fetchone()
    relays = get_relays(DEVICE_ID)
    pump_on = bool(relays[PUMP_CHANNEL - 1]) if 1 <= PUMP_CHANNEL <= RELAY_COUNT else any(relays)
    left = get_timers(DEVICE_ID).get(PUMP_CHANNEL)
    lines = []
    if row:
        lines.append(f"อุณหภูมิ {row[0]:.1f} °C   ความชื้น {row[1]:.1f} %RH")
        lines.append(f"ข้อมูลเมื่อ {local_str(row[2])} น.")
    else:
        lines.append("ยังไม่มีข้อมูลอุณหภูมิ/ความชื้น")
    lines.append("ปั๊มน้ำ: " + (f"กำลังรดน้ำ (เหลือ {left} นาที)" if pump_on and left else
                                 "กำลังรดน้ำ" if pump_on else "หยุด"))
    lines.append("อุปกรณ์ที่สวน: " + ("ออนไลน์" if esp_online(DEVICE_ID) else "ออฟไลน์"))
    return _card("อุณหภูมิและความชื้น", "สภาพอากาศในสวน", lines, color="#1A73E8")


def graph_url():
    return f"{PUBLIC_BASE_URL}/graph?token={GRAPH_TOKEN}" if PUBLIC_BASE_URL and GRAPH_TOKEN else ""


def more_card():
    url = graph_url()
    if not url:
        return plain("ยังไม่ได้ตั้งค่า PUBLIC_BASE_URL / GRAPH_TOKEN ในไฟล์ .env จึงยังเปิดหน้ากราฟไม่ได้")
    return _card("เมนูเพิ่มเติม", "เมนูเพิ่มเติม", ["ดูกราฟอุณหภูมิ ความชื้น และการเปิด-ปิดปั๊มย้อนหลัง"],
                 color="#1A73E8", buttons=[_button("ดูกราฟข้อมูลทั้งหมด", uri=url)])


# ==============================================================================
# [จากเล่ม ค.2] สั่งเปิด/ปิดรีเลย์ ตั้งเวลาปิดเอง และบันทึกประวัติปั๊ม
# ==============================================================================
def set_relays(device, channels, state, minutes=None, source="line"):
    now = time.time()
    channels = list(channels)
    with db() as conn:
        if state:
            m = min(minutes or MAX_ON_MIN, MAX_ON_MIN)
            conn.executemany(
                "INSERT OR REPLACE INTO timers (device, ch, off_at, minutes) VALUES (?,?,?,?)",
                [(device, ch, now + m * 60, m) for ch in channels],
            )
        else:
            conn.executemany(
                "DELETE FROM timers WHERE device=? AND ch=?", [(device, ch) for ch in channels]
            )
        if PUMP_CHANNEL in channels:
            prev = conn.execute(
                "SELECT state FROM relays WHERE device=? AND ch=?", (device, PUMP_CHANNEL)
            ).fetchone()
            if (prev[0] if prev else 0) != int(state):
                conn.execute(
                    "INSERT INTO pump_log (ts, state, source) VALUES (?,?,?)",
                    (now, int(state), source),
                )
        conn.executemany(
            "INSERT INTO relays (device, ch, state, ts) VALUES (?,?,?,?)"
            " ON CONFLICT(device, ch) DO UPDATE SET state=excluded.state, ts=excluded.ts",
            [(device, ch, int(state), now) for ch in channels],
        )
    with _relay_changed:
        _relay_changed.notify_all()


# ==============================================================================
# [จากเล่ม ค.2] งานจับเวลา: ปิดน้ำเมื่อครบเวลาและแจ้งทาง LINE
# ==============================================================================
def timer_loop():
    while True:
        time.sleep(5)
        try:
            with db() as conn:
                due = conn.execute(
                    "SELECT device, ch, minutes FROM timers WHERE off_at<=?", (time.time(),)
                ).fetchall()
            groups = {}
            for device, ch, minutes in due:
                groups.setdefault((device, minutes), []).append(ch)
            for (device, minutes), chs in groups.items():
                set_relays(device, chs, 0, source="timer")
                if any(c != PUMP_CHANNEL for c in chs):
                    push_messages([done_card(chs, minutes)])
        except Exception:
            log.exception("timer error")


# ==============================================================================
# [จากเล่ม ค.2] งานเฝ้าระวัง: ESP32 ขาดการเชื่อมต่อ ให้สถานะเป็นปิดและแจ้งเตือน
# ==============================================================================
def handle_offline(device):
    set_relays(device, range(1, RELAY_COUNT + 1), 0, source="watchdog")
    with db() as conn:
        row = conn.execute(
            "SELECT offline_notified FROM device_state WHERE device=?", (device,)
        ).fetchone()
    if row and row[0]:
        return
    if push_messages([offline_card()]):
        with db() as conn:
            conn.execute("UPDATE device_state SET offline_notified=1 WHERE device=?", (device,))


def watchdog_loop():
    with db() as conn:
        conn.execute("UPDATE device_state SET last_seen=?", (time.time(),))
    while True:
        time.sleep(WATCH_INTERVAL)
        try:
            with db() as conn:
                rows = conn.execute("SELECT device, last_seen FROM device_state").fetchall()
            for device, seen in rows:
                if time.time() - seen > OFFLINE_AFTER:
                    handle_offline(device)
        except Exception:
            log.exception("watchdog error")


# ==============================================================================
# [จากเล่ม ค.2] รดน้ำ: เปิดปั๊มตามเวลาที่ตั้ง (ค่าเริ่มต้น 10 นาที)
# ==============================================================================
def start_water(user_id, channels, minutes):
    if not esp_online(DEVICE_ID):
        return [cannot_open_card()]
    zones = [c for c in channels if c != PUMP_CHANNEL]
    if not zones:
        return start_on(user_id, channels, minutes)
    running = running_zones()
    todo = [c for c in zones if c not in running]
    if not todo:
        mins = max((running[c] for c in zones if running[c]), default=None)
        return [plain(f"{names_of(zones)} กำลังรดอยู่แล้วครับ" + (f" เหลือ {mins} นาที" if mins else ""))]
    minutes = max(1, min(int(minutes), MAX_ON_MIN))
    set_alert_target(user_id)
    set_relays(DEVICE_ID, todo, 1, minutes)
    if 1 <= PUMP_CHANNEL <= RELAY_COUNT:
        pump_left = get_timers(DEVICE_ID).get(PUMP_CHANNEL, 0)
        set_relays(DEVICE_ID, [PUMP_CHANNEL], 1, max(minutes, pump_left))
    return [confirm_on_card(todo, minutes)]


# ==============================================================================
# [จากเล่ม ค.2] API
# ==============================================================================
def check_key():
    key = request.headers.get("X-API-Key", "")
    return bool(DEVICE_API_KEY) and hmac.compare_digest(key, DEVICE_API_KEY)


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@app.route("/api/reading", methods=["POST"])
def api_reading():
    if not check_key():
        return jsonify(error="unauthorized"), 401
    d = request.get_json(silent=True) or {}
    try:
        device = str(d["device"])
        t = float(d["temperature"])
        h = float(d["humidity"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="bad request"), 400
    with db() as conn:
        conn.execute(
            "INSERT INTO readings (device, temperature, humidity, ts) VALUES (?,?,?,?)",
            (device, t, h, time.time()),
        )
    return jsonify(ok=True)


@app.route("/api/relays", methods=["GET"])
def api_relays():
    if not check_key():
        return jsonify(error="unauthorized"), 401
    device = request.args.get("device", DEVICE_ID)
    known = request.args.get("known", "")
    wait = min(max(request.args.get("wait", 0, type=int), 0), MAX_WAIT)
    gap, was_notified = touch_device(device, known)
    if gap > OFFLINE_AFTER and not was_notified:
        handle_offline(device)
        was_notified = 1
    if was_notified:
        with db() as conn:
            conn.execute("UPDATE device_state SET offline_notified=0 WHERE device=?", (device,))
        push_messages([back_online_card()])

    deadline = time.time() + wait
    relays = get_relays(device)
    while "".join(map(str, relays)) == known and time.time() < deadline:
        with _relay_changed:
            _relay_changed.wait(timeout=max(0, deadline - time.time()))
        relays = get_relays(device)
        touch_device(device, known)
    return jsonify(relays=relays)


# ==============================================================================
# [เขียนเพิ่ม] ตัวจัดการข้อความ/Postback จาก LINE
#   act=water (รดน้ำ) act=off_all (หยุดน้ำ) act=temp (วัดอุณหภูมิ) act=more (เพิ่มเติม)
# ==============================================================================
TEXT_COMMANDS = {"รดน้ำ": "water", "หยุดน้ำ": "off_all", "วัดอุณหภูมิ": "temp", "เพิ่มเติม": "more"}


def dispatch(user_id, act, params):
    if act == "water":
        ch = params.get("ch")
        if ch is None:
            return [water_menu_card()]
        try:
            ch = int(ch)
        except ValueError:
            return [plain("ไม่รู้จักโซนที่เลือกครับ")]
        if ch not in ZONES:
            return [plain("ไม่รู้จักโซนที่เลือกครับ")]
        return start_water(user_id, [ch], WATER_DEFAULT_MIN)
    if act == "off_all":
        return stop_water(user_id)
    if act == "temp":
        return [status_card()]
    if act == "more":
        return [more_card()]
    return [plain("ไม่รู้จักคำสั่งนี้ครับ")]


def reply(event, messages):
    try:
        line_bot_api.reply_message(event.reply_token, messages)
    except Exception:
        log.exception("reply error")


def handle_command(event, act, params):
    user_id = getattr(event.source, "user_id", None)
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return reply(event, [plain("ขออภัย คุณไม่มีสิทธิ์สั่งงานระบบนี้")])
    reply(event, dispatch(user_id, act, params))


@handler.add(PostbackEvent)
def on_postback(event):
    params = {k: v[0] for k, v in parse_qs(event.postback.data).items()}
    handle_command(event, params.get("act", ""), params)


@handler.add(MessageEvent, message=TextMessage)
def on_text(event):
    act = TEXT_COMMANDS.get(event.message.text.strip())
    if act:
        handle_command(event, act, {})
    else:
        reply(event, [plain("กดปุ่มในเมนูด้านล่างได้เลยครับ: รดน้ำ / หยุดน้ำ / วัดอุณหภูมิ / เพิ่มเติม")])


# ==============================================================================
# [เขียนเพิ่ม] หน้ากราฟ /graph และ /api/graph (ต้องมี token ในลิงก์)
#   24 ชั่วโมง เฉลี่ยทุก 5 นาที / 7 วัน เฉลี่ยทุก 1 ชั่วโมง + ประวัติปั๊ม 10 รายการล่าสุด
# ==============================================================================
def check_graph_token():
    token = request.args.get("token", "")
    return bool(GRAPH_TOKEN) and hmac.compare_digest(token, GRAPH_TOKEN)


@app.route("/api/graph")
def api_graph():
    if not check_graph_token():
        return jsonify(error="unauthorized"), 401
    week = request.args.get("range", "24h") == "7d"
    span, bucket = (7 * 86400, 3600) if week else (86400, 300)
    now = time.time()
    start = now - span
    nb = span // bucket
    with db() as conn:
        rows = conn.execute(
            "SELECT CAST((ts-?)/? AS INTEGER) AS b, AVG(temperature), AVG(humidity)"
            " FROM readings WHERE ts>=? GROUP BY b", (start, bucket, start),
        ).fetchall()
        before = conn.execute("SELECT state FROM pump_log WHERE ts<? ORDER BY ts DESC LIMIT 1", (start,)).fetchone()
        logs = conn.execute("SELECT ts, state FROM pump_log WHERE ts>=? ORDER BY ts", (start,)).fetchall()
        recent = conn.execute("SELECT ts, state, source FROM pump_log ORDER BY ts DESC LIMIT 10").fetchall()
    by_bucket = {int(b): (t, h) for b, t, h in rows if b is not None and 0 <= b < nb}
    fmt = "%d/%m %H:00" if week else "%H:%M"
    labels, temps, hums, pump = [], [], [], []
    state = int(before[0]) if before else 0
    i = 0
    for b in range(nb):
        end = start + (b + 1) * bucket
        while i < len(logs) and logs[i][0] <= end:
            state = int(logs[i][1])
            i += 1
        t, h = by_bucket.get(b, (None, None))
        labels.append(local_str(start + b * bucket, fmt))
        temps.append(round(t, 1) if t is not None else None)
        hums.append(round(h, 1) if h is not None else None)
        pump.append(state)
    return jsonify(
        range="7d" if week else "24h", labels=labels, temperature=temps, humidity=hums, pump=pump,
        history=[{"time": local_str(ts, "%d/%m/%Y %H:%M:%S"), "state": "เปิด" if s else "ปิด", "source": src}
                 for ts, s, src in recent],
    )


GRAPH_HTML = """<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>กราฟสภาพอากาศในสวน</title>
<style>body{font-family:sans-serif;margin:0;padding:12px;background:#f5f7fa;color:#222}
h1{font-size:1.2rem}button{padding:8px 14px;margin-right:6px;border:0;border-radius:6px;background:#dde3ea}
button.on{background:#1a73e8;color:#fff}.card{background:#fff;border-radius:10px;padding:10px;margin:12px 0}
table{width:100%;border-collapse:collapse;font-size:.9rem}td,th{padding:4px;border-bottom:1px solid #eee;text-align:left}</style>
</head><body><h1>สภาพอากาศและการรดน้ำในสวน</h1>
<div><button id="b24" class="on">24 ชั่วโมง</button><button id="b7">7 วัน</button></div>
<div class="card"><canvas id="c1" height="140"></canvas></div>
<div class="card"><canvas id="c2" height="60"></canvas></div>
<div class="card"><b>ประวัติปั๊มน้ำ 10 รายการล่าสุด</b><table id="hist"></table></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
const token=new URLSearchParams(location.search).get('token')||'';let ch1,ch2;
async function load(r){
  const res=await fetch('/api/graph?range='+r+'&token='+encodeURIComponent(token));
  if(!res.ok){document.body.innerHTML='<p>เปิดหน้ากราฟไม่ได้ (รหัสไม่ถูกต้อง)</p>';return;}
  const d=await res.json();
  document.getElementById('b24').className=r==='24h'?'on':'';
  document.getElementById('b7').className=r==='7d'?'on':'';
  if(ch1)ch1.destroy();if(ch2)ch2.destroy();
  ch1=new Chart(document.getElementById('c1'),{type:'line',data:{labels:d.labels,datasets:[
    {label:'อุณหภูมิ (°C)',data:d.temperature,borderColor:'#d93025',yAxisID:'y',pointRadius:0,spanGaps:false},
    {label:'ความชื้น (%RH)',data:d.humidity,borderColor:'#1a73e8',yAxisID:'y1',pointRadius:0,spanGaps:false}]},
    options:{scales:{y:{position:'left'},y1:{position:'right',grid:{drawOnChartArea:false}},x:{ticks:{maxTicksLimit:8}}}}});
  ch2=new Chart(document.getElementById('c2'),{type:'line',data:{labels:d.labels,datasets:[
    {label:'ปั๊มน้ำ (1=เปิด)',data:d.pump,borderColor:'#1db446',stepped:true,pointRadius:0,fill:true}]},
    options:{scales:{y:{min:0,max:1,ticks:{stepSize:1}},x:{ticks:{maxTicksLimit:8}}}}});
  document.getElementById('hist').innerHTML='<tr><th>เวลา</th><th>สถานะ</th><th>สาเหตุ</th></tr>'+
    d.history.map(h=>'<tr><td>'+h.time+'</td><td>'+h.state+'</td><td>'+h.source+'</td></tr>').join('');
}
document.getElementById('b24').onclick=()=>load('24h');document.getElementById('b7').onclick=()=>load('7d');load('24h');
</script></body></html>"""


@app.route("/graph")
def graph_page():
    if not check_graph_token():
        abort(403)
    return Response(GRAPH_HTML, mimetype="text/html")


# ==============================================================================
# [จากเล่ม ค.2] เริ่มโปรแกรม
# ==============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=watchdog_loop, daemon=True).start()
    threading.Thread(target=timer_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, threaded=True)
