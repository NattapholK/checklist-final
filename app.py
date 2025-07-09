from flask import Flask, request
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
import requests
import base64
import json

# Import students list - assuming 'student.py' exists and contains a 'students' list
# Example: students = ["สมชาย", "สมหญิง", "มานะ", "ปิติ"]
from student import students

# Load environment variables from .env file
load_dotenv()

# Retrieve LINE Access Token and Firebase Service Account JSON (base64 encoded)
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
FIREBASE_SERVICE_BASE64 = os.getenv("FIREBASE_SERVICE")

# --- Firebase Initialization ---
# Decode the base64 encoded Firebase service account JSON string
try:
    firebase_key_dict = json.loads(base64.b64decode(FIREBASE_SERVICE_BASE64))
    cred = credentials.Certificate(firebase_key_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase initialized successfully.")
except Exception as e:
    print(f"Error initializing Firebase: {e}")
    # Exit or raise an error if Firebase cannot be initialized, as it's critical
    # In a production environment, you might want more robust error handling.
    exit(1) # Exit the application if Firebase initialization fails

# --- Flask Application Initialization ---
app = Flask(__name__)

# --- Add this new root route ---
@app.route("/", methods=["GET"])
def home():
    """
    A simple root route to confirm the Flask app is running.
    This will display a message when you visit the main domain URL in a browser.
    """
    return "LINE Bot Backend is running! Webhook is at /webhook (POST only)."

# --- LINE Messaging Functions ---

def reply_text(reply_token, text):
    """
    Sends a text reply message to LINE.
    Args:
        reply_token (str): Reply token from LINE webhook event.
        text (str): The text message to send.
    """
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
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        print(f"Reply sent. Status: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending reply message: {e}")

def send_line_broadcast(message):
    """
    Sends a push message to all registered recipients in Firestore.
    Args:
        message (str): The text message to broadcast.
    """
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    users_ref = db.collection("recipients")
    try:
        docs = users_ref.stream() # Efficiently streams documents
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

# --- Attendance Report Function (can be called by a separate scheduler) ---

def send_attendance_report():
    """
    Generates and sends an attendance report based on Firestore data.
    This function is now designed to be called by an external scheduler (e.g., Railway Cron Job).
    """
    print("Generating attendance report...")
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # Reference to the attendance collection for today's date
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
            if number and name: # Ensure both number and name exist
                checked_numbers.add(number)
                checked_names.append(name)
        
        # Sort checked names for consistent report order
        checked_names.sort()

        report = f"📋 รายงานเช็คชื่อ ตัดเวลา {datetime.now().strftime('%H:%M')}\n"
        report += f"📅 วันที่: {date_str}\n"
        report += f"🟢 มาแล้ว: {len(checked_numbers)} คน\n"
        for name in checked_names:
            report += f"✅ {name}\n"

        # Calculate absent students based on the 'students' list
        # Assuming 'students' list is 0-indexed and student numbers are 1-indexed.
        # So, student number `i` corresponds to `students[i-1]`.
        absent_names = [
            students[i-1] for i in range(1, len(students) + 1) if i not in checked_numbers
        ]
        absent_names.sort() # Sort absent names for consistent report order

        report += f"🔴 ขาด: {len(absent_names)} คน\n"
        for name in absent_names:
            report += f"❌ {name}\n"
        
        print(f"Attendance report generated:\n{report}")
        send_line_broadcast(report)
        print("Attendance report broadcasted.")

    except Exception as e:
        print(f"Error generating or sending attendance report: {e}")

# --- LINE Webhook Endpoint ---

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Handles incoming LINE webhook events.
    """
    data = request.get_json()
    if not data:
        print("Received empty or invalid JSON data.")
        return "Bad Request", 400

    events = data.get("events", [])
    if not events:
        print("No events found in webhook data.")
        return "OK" # LINE might send empty events sometimes

    for event in events:
        print(f"Received event: {json.dumps(event, indent=2)}")
        
        # Handle 'follow' event when a user adds the bot as a friend
        if event["type"] == "follow":
            user_id = event["source"]["userId"]
            reply_token = event["replyToken"]

            # Reply to the user
            reply_text(reply_token, "✅ คุณได้สมัครรับรายงานเช็คชื่อแล้ว ขอบคุณที่ติดตาม!")

            # Save userId to Firestore for broadcasting purposes
            try:
                db.collection("recipients").document(user_id).set({"joined_at": firestore.SERVER_TIMESTAMP})
                print(f"User {user_id} added to recipients.")
            except Exception as e:
                print(f"Error saving user {user_id} to Firestore: {e}")
        
        # Handle 'message' event (optional, for future features)
        elif event["type"] == "message" and event["message"]["type"] == "text":
            user_id = event["source"]["userId"]
            reply_token = event["replyToken"]
            message_text = event["message"]["text"]
            print(f"Message from {user_id}: {message_text}")
            
            # Example: Basic echo or command handling
            if message_text.lower() == "เช็คชื่อ":
                reply_text(reply_token, "กรุณาใช้ระบบเช็คชื่อที่กำหนดไว้")
            elif message_text.lower() == "รายงาน":
                # You might want to trigger a manual report for an admin user
                # For now, just a placeholder reply
                reply_text(reply_token, "รายงานจะถูกส่งอัตโนมัติเวลา 8:30 น. ทุกวัน")
            else:
                reply_text(reply_token, "บอทนี้มีไว้สำหรับส่งรายงานเช็คชื่ออัตโนมัติเท่านั้น")

        # Handle 'unfollow' event to remove user from recipients (good practice)
        elif event["type"] == "unfollow":
            user_id = event["source"]["userId"]
            try:
                db.collection("recipients").document(user_id).delete()
                print(f"User {user_id} unfollowed and removed from recipients.")
            except Exception as e:
                print(f"Error removing user {user_id} from Firestore: {e}")

    return "OK"

# --- Local Development Server (DO NOT run in production with Gunicorn) ---
if __name__ == "__main__":
    # This block is for running the Flask app locally for development purposes.
    # When deployed with Gunicorn, Gunicorn will manage the server.
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask app on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=True) # debug=True for local development
