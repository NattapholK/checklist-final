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
import atexit # เพิ่มเข้ามาเพื่อปิด Scheduler ตอนปิดแอป

# นำเข้าไลบรารีสำหรับตั้งเวลา
from apscheduler.schedulers.background import BackgroundScheduler

from student import students

# ====== 1. โหลดตัวแปรแวดล้อม ======
load_dotenv()
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
FIREBASE_SERVICE_BASE64 = os.getenv("FIREBASE_SERVICE")
# ไม่ต้องใช้ CRON_SECRET_KEY แล้ว

# ====== 2. เชื่อมต่อ Firebase ======
try:
    if not firebase_admin._apps:
        firebase_key_dict = json.loads(base64.b64decode(FIREBASE_SERVICE_BASE64))
        cred = credentials.Certificate(firebase_key_dict)
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase initialized successfully.")
except Exception as e:
    print(f"🔥 Firebase initialization error: {e}")

# ====== 3. สร้าง Flask App ======
app = Flask(__name__)

# ====== 4. ฟังก์ชันสำหรับส่ง LINE (เหมือนเดิม) ======

def reply_text(reply_token, text):
    """ส่งข้อความตอบกลับ (Reply)"""
    headers = {"Authorization": f"Bearer {LINE_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
    try:
        r = requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=payload)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"🔥 Reply error: {e.response.text}")

def send_line_broadcast_to_all(message):
    """ส่งข้อความหาทุกคนที่ติดตาม (Broadcast)"""
    print("Attempting to send broadcast message...")
    headers = {"Authorization": f"Bearer {LINE_ACCESS_TOKEN}", "Content-Type": "application/json"}
    broadcast_url = "https://api.line.me/v2/bot/message/broadcast"
    payload = {"messages": [{"type": "text", "text": message}]}
    
    try:
        r = requests.post(broadcast_url, headers=headers, json=payload)
        r.raise_for_status()
        print("✅ Broadcast message sent to LINE API successfully.")
    except requests.exceptions.RequestException as e:
        print(f"🔥 LINE Broadcast API error: {e}")
        if e.response:
            print(f"Response status: {e.response.status_code}, body: {e.response.text}")

# ====== 5. ฟังก์ชันสำหรับสร้างรายงาน (เหมือนเดิม) ======

def generate_and_send_report():
    """สร้างและส่งรายงานสรุปการเช็คชื่อผ่าน LINE"""
    # ใช้ with app.app_context() เพื่อให้แน่ใจว่าโค้ดที่เกี่ยวกับ Flask ทำงานได้ถูกต้องใน Thread แยก
    with app.app_context():
        now_bkk = datetime.now(ZoneInfo("Asia/Bangkok"))
        date_str = now_bkk.strftime("%Y-%m-%d")
        time_now = now_bkk.strftime("%H:%M")

        users_ref = db.collection("attendances").document(date_str).collection("users")
        checked_names = []
        checked_numbers = set()

        try:
            docs = users_ref.stream()
            for doc in docs:
                data = doc.to_dict()
                if data.get("number") and data.get("name"):
                    checked_numbers.add(data["number"])
                    checked_names.append(data["name"])

            absent_names = [students[i] for i in range(len(students)) if (i + 1) not in checked_numbers]
            
            report = (
                f"📋 รายงานเช็คชื่ออัตโนมัติ\n"
                f"ตัดรอบเวลา: {time_now} น. ({date_str})\n\n"
                f"🟢 มาเรียน: {len(checked_numbers)} คน\n"
                f"🔴 ขาดเรียน: {len(absent_names)} คน\n"
            )
            if absent_names:
                report += "\nรายชื่อคนขาด:\n"
                for name in sorted(absent_names):
                    report += f"  - {name}\n"

            send_line_broadcast_to_all(report)
            print(f"📤 Report for {date_str} sent successfully.")

        except Exception as e:
            print(f"🔥 Error during report generation: {e}")
            # ไม่ควร raise e ที่นี่ เพราะอาจทำให้ scheduler หยุดทำงานได้
            
# ====== 6. ✨ ส่วนที่เพิ่มเข้ามา: ตั้งค่าตัวตั้งเวลา (Scheduler) ======
scheduler = BackgroundScheduler(daemon=True, timezone=ZoneInfo("Asia/Bangkok"))
# ตั้งเวลาให้ฟังก์ชัน generate_and_send_report ทำงานทุกวัน เวลา 8 โมง 30 นาที
scheduler.add_job(func=generate_and_send_report, trigger='cron', hour=8, minute=30)
scheduler.start()

# สั่งให้ปิดการทำงานของ Scheduler อย่างถูกต้องเมื่อแอปพลิเคชันปิดตัวลง
atexit.register(lambda: scheduler.shutdown())
print("✅ Scheduler started. Report will be sent daily at 08:30 Bangkok time.")

# ====== 7. Routes ทั้งหมด (เหมือนเดิมแต่ไม่มี /trigger-report) ======

@app.route("/")
def index():
    date_list = []
    try:
        attendance_docs = db.collection("attendances").get()
        date_list = sorted([doc.id for doc in attendance_docs], reverse=True)
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
        if data.get("number") and data.get("name"):
            checked.append(data)
            checked_numbers.add(data["number"])
    absent = [{"number": i + 1, "name": students[i]} for i in range(len(students)) if (i + 1) not in checked_numbers]
    return render_template("detail.html", date=date_str, checked=checked, absent=absent)

@app.route("/checkin", methods=["POST"])
def checkin():
    data = request.get_json()
    name, number = data.get("name"), data.get("number")
    if not name or not number: return jsonify({"error": "กรุณาระบุชื่อและเลขที่ให้ครบ"}), 400
    try: number = int(number)
    except ValueError: return jsonify({"error": "เลขที่ไม่ถูกต้อง"}), 400
    if not (1 <= number <= len(students)): return jsonify({"error": "เลขที่ไม่อยู่ในรายชื่อ"}), 400
    
    date_str = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d")
    doc_ref = db.collection("attendances").document(date_str).collection("users").document(str(number))
    try:
        if doc_ref.get().exists: return jsonify({"error": "เช็คชื่อไปแล้ว"}), 409
        db.collection("attendances").document(date_str).set({}, merge=True)
        doc_ref.set({"name": name, "number": number, "timestamp": firestore.SERVER_TIMESTAMP})
        return jsonify({"message": f"✅ เช็คชื่อสำเร็จ {number} - {name}"}), 200
    except Exception as e:
        print(f"🔥 Checkin error: {e}")
        return jsonify({"error": "เกิดข้อผิดพลาด"}), 500

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    for event in data.get("events", []):
        if "source" not in event or "userId" not in event["source"]:
            continue
        user_id = event["source"]["userId"]
        reply_token = event.get("replyToken")

        if event["type"] == "follow":
            print(f"New follow event from user_id: {user_id}")
            db.collection("recipients").document(user_id).set({"joined_at": firestore.SERVER_TIMESTAMP})
            reply_text(reply_token, "✅ สมัครรับรายงานผลเช็คชื่ออัตโนมัติเรียบร้อยแล้วค่ะ")

        elif event["type"] == "unfollow":
            print(f"Unfollow event from user_id: {user_id}")
            db.collection("recipients").document(user_id).delete()

        elif event["type"] == "message" and event["message"]["type"] == "text":
            text = event["message"]["text"].lower()
            if "รายงาน" in text:
                reply_text(reply_token, "รายงานจะถูกส่งให้โดยอัตโนมัติทุกวันเวลา 8:30 น. ค่ะ")
            else:
                reply_text(reply_token, "ระบบนี้ใช้สำหรับรับรายงานผลเช็คชื่ออัตโนมัติเท่านั้นค่ะ")
    return "OK", 200

# ====== 8. Run App ======
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # ต้องปิดการใช้งาน reloader ของ Flask เพื่อให้ scheduler ทำงานแค่ครั้งเดียว
    app.run(host="0.0.0.0", port=port, use_reloader=False)