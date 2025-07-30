from flask import Flask, request, render_template, jsonify
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
import requests
import base64
import json
from student import students

# Scheduler
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo

# ====== Load ENV ======
load_dotenv()
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
FIREBASE_SERVICE_BASE64 = os.getenv("FIREBASE_SERVICE")

# ====== Firebase Init ======
try:
    firebase_key_dict = json.loads(base64.b64decode(FIREBASE_SERVICE_BASE64))
    cred = credentials.Certificate(firebase_key_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase initialized successfully.")
except Exception as e:
    print(f"Error initializing Firebase: {e}")
    exit(1)

# ====== Flask Init ======
app = Flask(__name__)

# ====== ROUTES ======
@app.route("/")
def index():
    date_list = []
    try:
        attendance_docs = db.collection("attendances").get()
        for doc in attendance_docs:
            date_list.append(doc.id)
        date_list.sort(reverse=True)
    except Exception as e:
        print(f"Error fetching attendance dates: {e}")
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

    absent = [{"number": i+1, "name": students[i]} for i in range(len(students)) if (i+1) not in checked_numbers]
    return render_template("detail.html", date=date_str, checked=checked, absent=absent)

@app.route("/checkin", methods=["POST"])
def checkin():
    data = request.get_json()
    name = data.get("name")
    number = data.get("number")

    if not name or not number:
        return jsonify({"error": "กรุณาระบุชื่อและเลขที่นักเรียนให้ครบถ้วน"}), 400

    try:
        number = int(number)
    except ValueError:
        return jsonify({"error": "เลขที่ไม่ถูกต้อง"}), 400

    if not (1 <= number <= len(students)):
        return jsonify({"error": "เลขที่ไม่อยู่ในรายชื่อ"}), 400

    date_str = datetime.now().strftime("%Y-%m-%d")
    doc_ref = db.collection("attendances").document(date_str).collection("users").document(str(number))

    try:
        if doc_ref.get().exists:
            return jsonify({"error": f"เช็คชื่อแล้ว"}), 409
        db.collection("attendances").document(date_str).set({}, merge=True)
        doc_ref.set({
            "name": name,
            "number": number,
            "timestamp": firestore.SERVER_TIMESTAMP
        })
        return jsonify({"message": f"✅ เช็คชื่อสำเร็จ {number} - {name}"}), 200
    except Exception as e:
        print(f"Checkin error: {e}")
        return jsonify({"error": "เกิดข้อผิดพลาด"}), 500

# ====== LINE FUNCTIONS ======
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
        print(f"Line reply error: {e}")

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
        print(f"Broadcast error: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    events = data.get("events", [])
    for event in events:
        user_id = event["source"]["userId"]
        reply_token = event.get("replyToken")

        if event["type"] == "follow":
            reply_text(reply_token, "✅ สมัครรับรายงานเช็คชื่อเรียบร้อย")
            db.collection("recipients").document(user_id).set({"joined_at": firestore.SERVER_TIMESTAMP})

        elif event["type"] == "unfollow":
            db.collection("recipients").document(user_id).delete()

        elif event["type"] == "message" and event["message"]["type"] == "text":
            text = event["message"]["text"].lower()
            if "เช็คชื่อ" in text:
                reply_text(reply_token, "โปรดใช้ระบบเช็คชื่อผ่านเว็บไซต์")
            elif "รายงาน" in text:
                reply_text(reply_token, "รายงานจะส่งเวลา 8:30 น. ทุกวัน")
            else:
                reply_text(reply_token, "ระบบนี้ใช้สำหรับส่งรายงานเท่านั้น")
    return "OK"

# ====== SCHEDULED REPORT JOB ======
def schedule_attendance_report():
    now = datetime.now(ZoneInfo('Asia/Bangkok'))
    date_str = now.strftime("%Y-%m-%d")
    report_time = now.strftime("%H:%M")

    users_ref = db.collection("attendances").document(date_str).collection("users")
    checked_numbers = set()
    checked_names = []

    try:
        docs = users_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            if data.get("number") and data.get("name"):
                checked_numbers.add(data["number"])
                checked_names.append(data["name"])

        report = f"📋 รายงานเช็คชื่อ ตัดเวลา {report_time}\n"
        report += f"📅 วันที่: {date_str}\n"
        report += f"🟢 มาแล้ว: {len(checked_numbers)} คน\n"
        for name in sorted(checked_names):
            report += f"✅ {name}\n"

        absent_names = [students[i-1] for i in range(1, len(students)+1) if i not in checked_numbers]
        report += f"🔴 ขาด: {len(absent_names)} คน\n"
        for name in sorted(absent_names):
            report += f"❌ {name}\n"

        send_line_broadcast(report)
        print("[Scheduler] Sent report successfully.")
    except Exception as e:
        print(f"[Scheduler] Error: {e}")

# ====== START SCHEDULER ======
scheduler = BackgroundScheduler(timezone=ZoneInfo('Asia/Bangkok'))
scheduler.add_job(schedule_attendance_report, 'cron', hour=8, minute=30)
scheduler.start()
print("Scheduler started.")

# ====== FLASK RUN ======
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
