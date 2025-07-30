from flask import Flask, request, render_template, jsonify
from datetime import datetime
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
import requests
import base64
import json
from apscheduler.schedulers.background import BackgroundScheduler
from student import students

# ====== โหลดตัวแปรสิ่งแวดล้อม ======
load_dotenv()
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
FIREBASE_SERVICE_BASE64 = os.getenv("FIREBASE_SERVICE")

# ====== Firebase Init ======
try:
    firebase_key_dict = json.loads(base64.b64decode(FIREBASE_SERVICE_BASE64))
    cred = credentials.Certificate(firebase_key_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase initialized.")
except Exception as e:
    print(f"🔥 Firebase init error: {e}")
    exit(1)

# ====== Flask Init ======
app = Flask(__name__)

# ====== Routes ======
@app.route("/")
def index():
    date_list = []
    try:
        attendance_docs = db.collection("attendances").get()
        for doc in attendance_docs:
            date_list.append(doc.id)
        date_list.sort(reverse=True)
    except Exception as e:
        print(f"🔥 Error loading dates: {e}")
    return render_template("index.html", dates=date_list, students=students)

@app.route("/report/<date_str>")
def report_detail(date_str):
    users_ref = db.collection("attendances").document(date_str).collection("users")
    docs = users_ref.stream()
    checked = []
    checked_numbers = set()

    for doc in docs:
        data = doc.to_dict()
        number = data.get("number")
        name = data.get("name")
        if number:
            checked.append({"number": number, "name": name})
            checked_numbers.add(number)

    absent = [{"number": i + 1, "name": students[i]} for i in range(len(students)) if (i + 1) not in checked_numbers]
    return render_template("detail.html", date=date_str, checked=checked, absent=absent)

@app.route("/checkin", methods=["POST"])
def checkin():
    data = request.get_json()
    name = data.get("name")
    number = data.get("number")

    if not name or not number:
        return jsonify({"error": "กรุณาระบุชื่อและเลขที่ให้ครบ"}), 400

    try:
        number = int(number)
    except ValueError:
        return jsonify({"error": "เลขที่ไม่ถูกต้อง"}), 400

    if not (1 <= number <= len(students)):
        return jsonify({"error": "เลขที่ไม่อยู่ในรายชื่อ"}), 400

    date_str = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d")
    doc_ref = db.collection("attendances").document(date_str).collection("users").document(str(number))

    try:
        if doc_ref.get().exists:
            return jsonify({"error": "เช็คชื่อไปแล้ว"}), 409
        db.collection("attendances").document(date_str).set({}, merge=True)
        doc_ref.set({
            "name": name,
            "number": number,
            "timestamp": firestore.SERVER_TIMESTAMP
        })
        return jsonify({"message": f"✅ เช็คชื่อสำเร็จ {number} - {name}"}), 200
    except Exception as e:
        print(f"🔥 Checkin error: {e}")
        return jsonify({"error": "เกิดข้อผิดพลาด"}), 500

# ====== LINE Messaging ======
def reply_text(reply_token, text):
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    try:
        r = requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=payload)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"🔥 Reply error: {e}")

def send_line_broadcast(message):
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        for doc in db.collection("recipients").stream():
            user_id = doc.id
            payload = {
                "to": user_id,
                "messages": [{"type": "text", "text": message}]
            }
            r = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)
            r.raise_for_status()
    except Exception as e:
        print(f"🔥 Broadcast error: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    events = data.get("events", [])
    for event in events:
        user_id = event["source"]["userId"]
        reply_token = event.get("replyToken")

        if event["type"] == "follow":
            reply_text(reply_token, "✅ สมัครรับรายงานเรียบร้อย")
            db.collection("recipients").document(user_id).set({"joined_at": firestore.SERVER_TIMESTAMP})

        elif event["type"] == "unfollow":
            db.collection("recipients").document(user_id).delete()

        elif event["type"] == "message" and event["message"]["type"] == "text":
            text = event["message"]["text"].lower()
            if "เช็คชื่อ" in text:
                reply_text(reply_token, "กรุณาเช็คชื่อผ่านเว็บที่กำหนด")
            elif "รายงาน" in text:
                reply_text(reply_token, "รายงานจะถูกส่งอัตโนมัติทุกวัน 8:30 น.")
            else:
                reply_text(reply_token, "ระบบนี้ใช้ส่งรายงานเช็คชื่อเท่านั้น")
    return "OK"

# ====== รายงานเช็คชื่อแบบตั้งเวลา (ใช้เวลาประเทศไทย) ======
def schedule_attendance_report():
    now_bkk = datetime.now(ZoneInfo("Asia/Bangkok"))
    date_str = now_bkk.strftime("%Y-%m-%d")
    time_now = now_bkk.strftime("%H:%M")

    users_ref = db.collection("attendances").document(date_str).collection("users")
    checked = []
    checked_numbers = set()

    try:
        for doc in users_ref.stream():
            data = doc.to_dict()
            if data.get("number") and data.get("name"):
                checked_numbers.add(data["number"])
                checked.append(data["name"])

        report = f"📋 รายงานเช็คชื่อ ตัดเวลา {time_now}\n"
        report += f"📅 วันที่: {date_str}\n"
        report += f"🟢 มาแล้ว: {len(checked_numbers)} คน\n"
        for name in sorted(checked):
            report += f"✅ {name}\n"

        absent = [students[i - 1] for i in range(1, len(students) + 1) if i not in checked_numbers]
        report += f"🔴 ขาด: {len(absent)} คน\n"
        for name in sorted(absent):
            report += f"❌ {name}\n"

        send_line_broadcast(report)
        print("📤 รายงานเช็คชื่อส่งแล้ว")
    except Exception as e:
        print(f"🔥 Scheduler error: {e}")

# ====== ตั้ง Scheduler เวลาไทย ======
scheduler = BackgroundScheduler(timezone=ZoneInfo("Asia/Bangkok"))
scheduler.add_job(schedule_attendance_report, 'cron', hour=8, minute=30)
scheduler.start()
print("✅ Scheduler started")

# ====== Run App ======
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
