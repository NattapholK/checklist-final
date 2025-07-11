from flask import Flask, request, render_template, jsonify
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
import requests
import base64
import json
from student import students # ตรวจสอบให้แน่ใจว่าไฟล์ student.py มีอยู่และถูก deploy ด้วย

load_dotenv()

# แก้ไข: เพิ่มวงเล็บปิด ) ที่ท้ายบรรทัดนี้
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
FIREBASE_SERVICE_BASE64 = os.getenv("FIREBASE_SERVICE")

# Initialize Firebase
try:
    firebase_key_dict = json.loads(base64.b64decode(FIREBASE_SERVICE_BASE64))
    cred = credentials.Certificate(firebase_key_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase initialized successfully.")
except Exception as e:
    print(f"Error initializing Firebase: {e}")
    exit(1)

# Initialize Flask
app = Flask(__name__)

# Home Page - List all attendance dates and show check-in form
@app.route("/")
def index():
    print("DEBUG: index() function called.")
    date_list = []
    try:
        print("DEBUG: Attempting to stream attendances collection.")
        # ดึงเอกสารทั้งหมดจากคอลเลกชัน "attendances"
        # เพิ่ม .get() เพื่อให้แน่ใจว่าได้ข้อมูลมาทันที
        attendance_docs = db.collection("attendances").get() 
        
        docs_count = 0
        for doc in attendance_docs:
            docs_count += 1
            date_list.append(doc.id)
        
        date_list.sort(reverse=True)
        print(f"DEBUG: Number of docs from stream (after .get()): {docs_count}")
        print(f"Fetched dates: {date_list}")
    except Exception as e:
        print(f"Error fetching attendance dates from Firestore: {e}")
    
    return render_template("index.html", dates=date_list, students=students)

# Attendance Detail Page
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

    absent = [
        {"number": i + 1, "name": students[i]}
        for i in range(len(students))
        if (i + 1) not in checked_numbers
    ]

    return render_template("detail.html", date=date_str, checked=checked, absent=absent)

# Web Check-in Page
@app.route("/checkin", methods=["POST"])
def checkin():
    data = request.get_json()
    student_name = data.get("name")
    student_number = data.get("number")

    if not student_name or not student_number:
        return jsonify({"error": "กรุณาระบุชื่อและเลขที่นักเรียนให้ครบถ้วน"}), 400

    try:
        student_number = int(student_number)
    except ValueError:
        return jsonify({"error": "เลขที่นักเรียนไม่ถูกต้อง"}), 400

    if not (1 <= student_number <= len(students)):
        return jsonify({"error": "เลขที่นักเรียนไม่อยู่ในรายชื่อ"}), 400

    date_str = datetime.now().strftime("%Y-%m-%d")
    # อ้างอิงถึงเอกสารวันที่หลักด้วย เพื่อให้แน่ใจว่าเอกสารวันที่ถูกสร้างขึ้น
    attendance_date_doc_ref = db.collection("attendances").document(date_str)
    attendance_user_doc_ref = attendance_date_doc_ref.collection("users").document(str(student_number))

    try:
        doc = attendance_user_doc_ref.get()
        if doc.exists:
            return jsonify({"error": f"นักเรียนเลขที่ {student_number} - {student_name} ได้เช็คชื่อไปแล้ววันนี้"}), 409

        # สร้างเอกสารวันที่หลักก่อน หากยังไม่มี (เป็นเพียงการตั้งค่าเปล่าๆ)
        # เพื่อให้แน่ใจว่าเอกสารวันที่นั้นมีอยู่จริงและสามารถถูก stream ได้
        attendance_date_doc_ref.set({}, merge=True) # ใช้ merge=True เพื่อไม่ให้เขียนทับข้อมูลเดิมหากมี

        attendance_user_doc_ref.set({
            "name": student_name,
            "number": student_number,
            "timestamp": firestore.SERVER_TIMESTAMP
        })
        print(f"Student {student_name} (No. {student_number}) checked in successfully for {date_str}.")
        return jsonify({"message": f"✅ เช็คชื่อนักเรียนเลขที่ {student_number} - {student_name} สำเร็จแล้ว"}), 200

    except Exception as e:
        print(f"Error during check-in for {student_name} (No. {student_number}): {e}")
        return jsonify({"error": "เกิดข้อผิดพลาดในการเช็คชื่อ กรุณาลองใหม่"}), 500

# LINE Messaging API

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
        response = requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=payload)
        response.raise_for_status()
        print(f"Reply sent. Status: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending reply message: {e}")

def send_line_broadcast(message):
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    users_ref = db.collection("recipients")
    try:
        docs = users_ref.stream()
        for doc in docs:
            user_id = doc.id
            payload = {
                "to": user_id,
                "messages": [{"type": "text", "text": message}]
            }
            try:
                response = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)
                response.raise_for_status()
                print(f"Broadcast message sent to {user_id}. Status: {response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"Error sending push message to {user_id}: {e}")
    except Exception as e:
        print(f"Error fetching recipients from Firestore: {e}")

def send_attendance_report():
    print("Generating attendance report...")
    date_str = datetime.now().strftime("%Y-%m-%d")
    attendances_doc_ref = db.collection("attendances").document(date_str)
    users_attendance_ref = attendances_doc_ref.collection("users")

    checked_numbers = set()
    checked_names = []

    try:
        docs = users_attendance_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            number = data.get("number")
            name = data.get("name")
            if number and name:
                checked_numbers.add(number)
                checked_names.append(name)

        checked_names.sort()
        report = f"📋 รายงานเช็คชื่อ ตัดเวลา {datetime.now().strftime('%H:%M')}\n"
        report += f"📅 วันที่: {date_str}\n"
        report += f"🟢 มาแล้ว: {len(checked_numbers)} คน\n"
        for name in checked_names:
            report += f"✅ {name}\n"

        absent_names = [students[i - 1] for i in range(1, len(students) + 1) if i not in checked_numbers]
        absent_names.sort()

        report += f"🔴 ขาด: {len(absent_names)} คน\n"
        for name in absent_names:
            report += f"❌ {name}\n"

        print(f"Attendance report generated:\n{report}")
        send_line_broadcast(report)
        print("Attendance report broadcasted.")

    except Exception as e:
        print(f"Error generating or sending attendance report: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        print("Received empty or invalid JSON data.")
        return "Bad Request", 400

    events = data.get("events", [])
    if not events:
        print("No events found in webhook data.")
        return "OK"

    for event in events:
        print(f"Received event: {json.dumps(event, indent=2)}")

        if event["type"] == "follow":
            user_id = event["source"]["userId"]
            reply_token = event["replyToken"]
            reply_text(reply_token, "✅ คุณได้สมัครรับรายงานเช็คชื่อแล้ว ขอบคุณที่ติดตาม!")
            try:
                db.collection("recipients").document(user_id).set({"joined_at": firestore.SERVER_TIMESTAMP})
                print(f"User {user_id} added to recipients.")
            except Exception as e:
                print(f"Error saving user {user_id} to Firestore: {e}")

        elif event["type"] == "message" and event["message"]["type"] == "text":
            user_id = event["source"]["userId"]
            reply_token = event["replyToken"]
            message_text = event["message"]["text"]
            print(f"Message from {user_id}: {message_text}")

            if message_text.lower() == "เช็คชื่อ":
                reply_text(reply_token, "กรุณาใช้ระบบเช็คชื่อที่กำหนดไว้")
            elif message_text.lower() == "รายงาน":
                reply_text(reply_token, "รายงานจะถูกส่งอัตโนมัติเวลา 8:30 น. ทุกวัน")
            else:
                reply_text(reply_token, "บอทนี้มีไว้สำหรับส่งรายงานเช็คชื่ออัตโนมัติเท่านั้น")

        elif event["type"] == "unfollow":
            user_id = event["source"]["userId"]
            try:
                db.collection("recipients").document(user_id).delete()
                print(f"User {user_id} unfollowed and removed from recipients.")
            except Exception as e:
                print(f"Error removing user {user_id} from Firestore: {e}")

    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask app on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
