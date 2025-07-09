from flask import Flask, request
from datetime import datetime
from student import students
import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
import requests
from apscheduler.schedulers.background import BackgroundScheduler
import base64
import json

# Load env
load_dotenv()
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
FIREBASE_SERVICE_BASE64 = os.getenv("FIREBASE_SERVICE")  

# Init Firebase จาก base64
firebase_key_dict = json.loads(base64.b64decode(FIREBASE_SERVICE_BASE64))
cred = credentials.Certificate(firebase_key_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Init Flask
app = Flask(__name__)

# ✅ ฟังก์ชันตอบกลับ LINE
def reply_text(reply_token, text):
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=payload)

# ✅ LINE Broadcast
def send_line_broadcast(message):
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    users_ref = db.collection("recipients")
    docs = users_ref.stream()

    for doc in docs:
        user_id = doc.id
        payload = {
            "to": user_id,
            "messages": [{"type": "text", "text": message}]
        }
        requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)

# ✅ เช็คชื่อ 8:30
def send_attendance_report():
    date_str = datetime.now().strftime("%Y-%m-%d")
    users_ref = db.collection("attendances").document(date_str).collection("users")
    docs = users_ref.stream()

    checked_numbers = set()
    checked_names = []
    for doc in docs:
        data = doc.to_dict()
        number = data.get("number")
        name = data.get("name")
        if number:
            checked_numbers.add(number)
            checked_names.append(name)

    report = f"📋 รายงานเช็คชื่อ ตัดเวลา 8:30\n"
    report += f"🟢 มาแล้ว: {len(checked_numbers)} คน\n"
    for name in checked_names:
        report += f"✅ {name}\n"

    absent_names = [students[i] for i in range(len(students)) if (i + 1) not in checked_numbers]
    report += f"🔴 ขาด: {len(absent_names)} คน\n"
    for name in absent_names:
        report += f"❌ {name}\n"

    send_line_broadcast(report)

# ✅ Scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(send_attendance_report, 'cron', hour=8, minute=30)
scheduler.start()

# ✅ LINE Webhook
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    events = data.get("events", [])

    for event in events:
        if event["type"] == "follow":
            user_id = event["source"]["userId"]
            reply_token = event["replyToken"]

            # ✅ ตอบรับ
            reply_text(reply_token, "✅ คุณได้สมัครรับรายงานเช็คชื่อแล้ว")

            # ✅ บันทึก userId ลง Firestore
            db.collection("recipients").document(user_id).set({"joined": True})

    return "OK"

# ❌ อย่าเรียก app.run() ใน production (Railway ใช้ gunicorn)
# ✅ แต่ยังคงไว้สำหรับรัน local
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
