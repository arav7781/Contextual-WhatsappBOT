from flask import Flask, request, Response, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
from groq import Groq
import os
import logging
from datetime import datetime, timedelta
import requests
import base64
from requests.auth import HTTPBasicAuth
import sqlite3
import json
import hashlib
import re
from collections import defaultdict
import threading
import time

# Load environment variables
load_dotenv()

# Twilio credentials
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# Initialize clients
groq_client = Groq(api_key="")

# Initialize Flask app
app = Flask(__name__)
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("whatsapp_bot.log"),
        logging.StreamHandler()
    ]
)

logger.info("Flask app initialized")

# Database setup
DB_PATH = "conversation_history.db"

# ============================================
# NEW FEATURES: Enhanced Database Schema
# ============================================

def init_db():
    """Initialize SQLite database with enhanced schema."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Original conversations table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            content_type TEXT DEFAULT 'text',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # User profiles with preferences
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_profiles (
            phone_number TEXT PRIMARY KEY,
            name TEXT,
            preferred_language TEXT DEFAULT 'en',
            medical_conditions TEXT,
            allergies TEXT,
            emergency_contact TEXT,
            notification_preference TEXT DEFAULT 'all',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_active DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Medication reminders
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS medication_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL,
            medication_name TEXT NOT NULL,
            dosage TEXT,
            frequency TEXT,
            reminder_times TEXT,
            start_date DATE,
            end_date DATE,
            active INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Appointment scheduling
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL,
            appointment_type TEXT,
            preferred_date DATE,
            preferred_time TEXT,
            doctor_specialty TEXT,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            reminder_sent INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Symptom tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS symptom_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL,
            symptoms TEXT NOT NULL,
            severity INTEGER,
            notes TEXT,
            image_analysis TEXT,
            logged_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Health tips and personalized recommendations
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS health_tips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            tip_content TEXT,
            priority INTEGER DEFAULT 0
        )
    ''')
    
    # Conversation analytics
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversation_analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL,
            session_id TEXT,
            intent TEXT,
            sentiment TEXT,
            satisfaction_score INTEGER,
            response_time REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Quick replies cache
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quick_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_phrase TEXT UNIQUE,
            reply_text TEXT,
            category TEXT,
            usage_count INTEGER DEFAULT 0
        )
    ''')
    
    # Create indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_phone_number ON conversations(phone_number, timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_appointments ON appointments(phone_number, status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_reminders ON medication_reminders(phone_number, active)')
    
    conn.commit()
    conn.close()
    logger.info("Enhanced database initialized")

# ============================================
# NEW FEATURE 1: Intelligent Intent Detection
# ============================================

def detect_intent(message):
    """Detect user intent using pattern matching and keywords."""
    message_lower = message.lower()
    
    intents = {
        'appointment': ['appointment', 'book', 'schedule', 'doctor', 'consultation', 'visit'],
        'medication': ['medicine', 'medication', 'pill', 'dose', 'prescription', 'remind'],
        'symptom': ['symptom', 'pain', 'fever', 'headache', 'sick', 'feeling', 'hurt'],
        'emergency': ['emergency', 'urgent', 'critical', 'severe', '911', 'ambulance'],
        'profile': ['my info', 'profile', 'update', 'personal', 'allergies', 'conditions'],
        'report': ['report', 'summary', 'history', 'show me', 'track'],
        'greeting': ['hello', 'hi', 'hey', 'good morning', 'good afternoon', 'good evening'],
        'help': ['help', 'what can you do', 'commands', 'options', 'menu']
    }
    
    for intent, keywords in intents.items():
        if any(keyword in message_lower for keyword in keywords):
            return intent
    
    return 'general'

# ============================================
# NEW FEATURE 2: Smart Context-Aware Responses
# ============================================

def get_contextual_prompt(intent, user_profile, recent_history):
    """Generate context-aware system prompts based on intent and user history."""
    base_prompt = "You are Tanya, a nurse assistant at Symbiosis Hospital."
    
    context_additions = {
        'appointment': "\n\nThe user wants to schedule an appointment. Ask for: preferred date/time, type of consultation needed, and specific concerns. Be helpful in finding suitable time slots.",
        'medication': "\n\nThe user is asking about medications. Provide clear information about dosage, timing, and important warnings. Ask if they'd like to set up reminders.",
        'symptom': "\n\nThe user is describing symptoms. Listen carefully, ask clarifying questions, and assess urgency. If symptoms seem severe, recommend immediate medical attention.",
        'emergency': "\n\n🚨 EMERGENCY MODE: The user may be experiencing a medical emergency. Provide immediate guidance and strongly recommend calling emergency services or visiting the ER.",
        'profile': "\n\nHelp the user manage their health profile. You can store information about medical conditions, allergies, and emergency contacts.",
    }
    
    prompt = base_prompt + context_additions.get(intent, "")
    
    # Add user profile context if available
    if user_profile:
        prompt += f"\n\nUser Profile:"
        if user_profile.get('name'):
            prompt += f"\n- Name: {user_profile['name']}"
        if user_profile.get('medical_conditions'):
            prompt += f"\n- Conditions: {user_profile['medical_conditions']}"
        if user_profile.get('allergies'):
            prompt += f"\n- Allergies: {user_profile['allergies']}"
    
    return prompt

# ============================================
# NEW FEATURE 3: Medication Reminder System
# ============================================

def add_medication_reminder(phone_number, medication_name, dosage, frequency, times):
    """Add a medication reminder for a user."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO medication_reminders 
            (phone_number, medication_name, dosage, frequency, reminder_times, start_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (phone_number, medication_name, dosage, frequency, json.dumps(times), datetime.now().date()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to add reminder: {str(e)}")
        return False

def get_active_reminders(phone_number):
    """Get active medication reminders for a user."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT medication_name, dosage, frequency, reminder_times
            FROM medication_reminders
            WHERE phone_number = ? AND active = 1
        ''', (phone_number,))
        reminders = cursor.fetchall()
        conn.close()
        return [{'medication': r[0], 'dosage': r[1], 'frequency': r[2], 'times': json.loads(r[3])} for r in reminders]
    except Exception as e:
        logger.error(f"Failed to get reminders: {str(e)}")
        return []

# ============================================
# NEW FEATURE 4: Symptom Tracking & Analysis
# ============================================

def log_symptom(phone_number, symptoms, severity, notes="", image_analysis=""):
    """Log a symptom entry."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO symptom_logs (phone_number, symptoms, severity, notes, image_analysis)
            VALUES (?, ?, ?, ?, ?)
        ''', (phone_number, symptoms, severity, notes, image_analysis))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to log symptom: {str(e)}")
        return False

def get_symptom_history(phone_number, days=7):
    """Get symptom history for analysis."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cutoff = datetime.now() - timedelta(days=days)
        cursor.execute('''
            SELECT symptoms, severity, notes, logged_at
            FROM symptom_logs
            WHERE phone_number = ? AND logged_at > ?
            ORDER BY logged_at DESC
        ''', (phone_number, cutoff))
        logs = cursor.fetchall()
        conn.close()
        return [{'symptoms': l[0], 'severity': l[1], 'notes': l[2], 'date': l[3]} for l in logs]
    except Exception as e:
        logger.error(f"Failed to get symptom history: {str(e)}")
        return []

# ============================================
# NEW FEATURE 5: User Profile Management
# ============================================

def get_user_profile(phone_number):
    """Get user profile."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, preferred_language, medical_conditions, allergies, emergency_contact
            FROM user_profiles WHERE phone_number = ?
        ''', (phone_number,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'name': row[0],
                'language': row[1],
                'medical_conditions': row[2],
                'allergies': row[3],
                'emergency_contact': row[4]
            }
        return None
    except Exception as e:
        logger.error(f"Failed to get profile: {str(e)}")
        return None

def update_user_profile(phone_number, **kwargs):
    """Update or create user profile."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if profile exists
        cursor.execute('SELECT phone_number FROM user_profiles WHERE phone_number = ?', (phone_number,))
        exists = cursor.fetchone()
        
        if exists:
            # Update existing
            set_clause = ', '.join([f"{k} = ?" for k in kwargs.keys()])
            query = f"UPDATE user_profiles SET {set_clause}, last_active = ? WHERE phone_number = ?"
            cursor.execute(query, list(kwargs.values()) + [datetime.now(), phone_number])
        else:
            # Insert new
            columns = ', '.join(['phone_number'] + list(kwargs.keys()))
            placeholders = ', '.join(['?'] * (len(kwargs) + 1))
            query = f"INSERT INTO user_profiles ({columns}) VALUES ({placeholders})"
            cursor.execute(query, [phone_number] + list(kwargs.values()))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to update profile: {str(e)}")
        return False

# ============================================
# NEW FEATURE 6: Smart Quick Replies
# ============================================

QUICK_REPLIES = {
    'hours': "🏥 Symbiosis Hospital Hours:\n\n• Emergency: 24/7\n• OPD: 8 AM - 8 PM (Mon-Sat)\n• Pharmacy: 7 AM - 11 PM",
    'location': "📍 Symbiosis Hospital Location:\nSymbiosis Road, Artist Village\nMaharashtra, India\n\nGoogle Maps: [Link would be here]",
    'emergency': "🚨 For emergencies:\n• Call: 108 (Ambulance)\n• Hospital Emergency: +91-XXX-XXX-XXXX\n• This is urgent - please call immediately!",
    'departments': "🏥 Our Departments:\n• Cardiology\n• Neurology\n• Orthopedics\n• Pediatrics\n• General Medicine\n• Radiology\n• Pathology",
    'menu': "📋 I can help you with:\n\n1️⃣ Book appointments\n2️⃣ Medication reminders\n3️⃣ Symptom tracking\n4️⃣ Health reports\n5️⃣ Update profile\n6️⃣ Emergency guidance\n\nJust tell me what you need!",
}

def check_quick_reply(message):
    """Check if message matches a quick reply."""
    message_lower = message.lower().strip()
    
    for trigger, reply in QUICK_REPLIES.items():
        if trigger in message_lower or message_lower == trigger:
            return reply
    return None

# ============================================
# NEW FEATURE 7: Conversation Analytics
# ============================================

def log_analytics(phone_number, session_id, intent, response_time):
    """Log conversation analytics."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO conversation_analytics 
            (phone_number, session_id, intent, response_time)
            VALUES (?, ?, ?, ?)
        ''', (phone_number, session_id, intent, response_time))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log analytics: {str(e)}")

# ============================================
# Original Helper Functions (Enhanced)
# ============================================

def save_message(phone_number, role, content, content_type='text', intent='general'):
    """Save a message to the database with intent tracking."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO conversations (phone_number, role, content, content_type)
            VALUES (?, ?, ?, ?)
        ''', (phone_number, role, json.dumps(content) if isinstance(content, (list, dict)) else content, content_type))
        conn.commit()
        conn.close()
        
        # Update user's last active time
        update_user_profile(phone_number, last_active=datetime.now())
    except Exception as e:
        logger.error(f"Failed to save message: {str(e)}")

def get_conversation_history(phone_number, limit=10):
    """Retrieve conversation history for a phone number."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT role, content, content_type FROM conversations
            WHERE phone_number = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (phone_number, limit))
        rows = cursor.fetchall()
        conn.close()
        
        history = []
        for role, content, content_type in reversed(rows):
            try:
                if content_type in ['image', 'multimodal']:
                    content = json.loads(content)
            except:
                pass
            history.append({
                'role': role,
                'content': content
            })
        return history
    except Exception as e:
        logger.error(f"Failed to retrieve history: {str(e)}")
        return []

def clear_conversation_history(phone_number):
    """Clear conversation history for a phone number."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM conversations WHERE phone_number = ?', (phone_number,))
        conn.commit()
        conn.close()
        logger.info(f"Cleared history for {phone_number}")
    except Exception as e:
        logger.error(f"Failed to clear history: {str(e)}")

def fetch_twilio_media(media_url, return_base64=False):
    """Fetch media from Twilio and return as raw bytes or Base64-encoded string."""
    try:
        response = requests.get(
            media_url,
            auth=HTTPBasicAuth(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=10
        )
        response.raise_for_status()
        if return_base64:
            content_type = response.headers.get('content-type', 'image/jpeg')
            image_data = base64.b64encode(response.content).decode('utf-8')
            return f"data:{content_type};base64,{image_data}"
        else:
            return response.content
    except Exception as e:
        logger.error(f"Failed to fetch media: {str(e)}")
        return None

# Load system prompts
try:
    with open("prompt1.md", "r") as file:
        TEXT_PROMPT = file.read()
    logger.info("Loaded prompt1.md")
except FileNotFoundError:
    logger.warning("prompt1.md not found, using default prompt")
    TEXT_PROMPT = "You are Tanya, a nurse assistant at Symbiosis Hospital. Provide helpful, concise, and accurate responses to user queries."

try:
    with open("vision_prompt.md", "r") as file:
        VISION_PROMPT = file.read()
    logger.info("Loaded vision_prompt.md")
except FileNotFoundError:
    logger.warning("vision_prompt.md not found, using default prompt")
    VISION_PROMPT = "You are Tanya, a nurse assistant at Symbiosis Hospital. Analyze the provided image and respond helpfully based on its content."

# ============================================
# ENHANCED MAIN WEBHOOK
# ============================================

@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    """Handle incoming WhatsApp messages with enhanced features."""
    start_time = time.time()
    
    try:
        incoming_msg = request.form.get('Body', '').strip()
        sender_number = request.form.get('From', '')
        media_url = request.form.get('MediaUrl0')
        media_content_type = request.form.get('MediaContentType0')

        logger.info(f"Incoming message from {sender_number}: {incoming_msg}, Media: {media_url}")

        # Generate session ID
        session_id = hashlib.md5(f"{sender_number}{datetime.now().date()}".encode()).hexdigest()

        # Handle reset command
        if incoming_msg.lower() in ["start over", "reset", "clear history", "new conversation"]:
            clear_conversation_history(sender_number)
            resp = MessagingResponse()
            resp.message("✨ Conversation reset!\n\nHello! I'm Tanya, your nurse assistant at Symbiosis Hospital.\n\nType 'menu' to see what I can help you with! 🏥")
            return Response(str(resp), mimetype="application/xml")

        # Check for quick replies first
        quick_reply = check_quick_reply(incoming_msg)
        if quick_reply:
            resp = MessagingResponse()
            resp.message(quick_reply)
            return Response(str(resp), mimetype="application/xml")

        # Detect intent
        intent = detect_intent(incoming_msg) if incoming_msg else 'general'
        logger.info(f"Detected intent: {intent}")

        # Get user profile
        user_profile = get_user_profile(sender_number)

        # Check for image
        has_image = media_url and media_content_type and media_content_type.startswith('image/')
        
        # Prepare user content
        if has_image:
            base64_image = fetch_twilio_media(media_url, return_base64=True)
            if not base64_image:
                resp = MessagingResponse()
                resp.message("Sorry, I couldn't access the image. Please try sending it again.")
                return Response(str(resp), mimetype="application/xml")
            
            user_content = []
            if incoming_msg:
                user_content.append({"type": "text", "text": incoming_msg})
            user_content.append({"type": "image_url", "image_url": {"url": base64_image}})
            content_type = 'multimodal'
            
            # If symptom-related, log it
            if intent == 'symptom':
                logger.info("Logging symptom with image")
        else:
            if not incoming_msg:
                resp = MessagingResponse()
                resp.message("Please send a message or image. Type 'help' to see what I can do! 😊")
                return Response(str(resp), mimetype="application/xml")
            user_content = incoming_msg
            content_type = 'text'

        # Get conversation history
        history = get_conversation_history(sender_number, limit=20)
        
        # Generate contextual system prompt
        system_prompt = get_contextual_prompt(intent, user_profile, history)
        if has_image:
            system_prompt = VISION_PROMPT + "\n\n" + system_prompt
        
        # Build messages for Groq API
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history
        for msg in history:
            messages.append({
                "role": msg['role'],
                "content": msg['content']
            })
        
        # Add current user message
        messages.append({"role": "user", "content": user_content})
        
        # Save user message
        save_message(sender_number, "user", user_content, content_type, intent)

        # Get response from Groq
        logger.info(f"Sending {len(messages)} messages to Groq API")
        chat_completion = groq_client.chat.completions.create(
            messages=messages,
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            temperature=0.7,
            max_tokens=500,
            top_p=1
        )
        
        llm_response = chat_completion.choices[0].message.content.strip()
        logger.info(f"LLM Response: {llm_response}")

        # Add helpful footer based on intent
        footer = ""
        if intent == 'appointment':
            footer = "\n\n💡 Tip: Type 'my appointments' to see scheduled appointments."
        elif intent == 'symptom':
            footer = "\n\n💡 Tip: I'm tracking your symptoms. Type 'symptom report' to see your history."
        elif intent == 'medication':
            footer = "\n\n💡 Tip: I can set up medication reminders! Just ask."

        final_response = llm_response + footer

        # Save assistant response
        save_message(sender_number, "assistant", final_response, 'text', intent)

        # Log analytics
        response_time = time.time() - start_time
        log_analytics(sender_number, session_id, intent, response_time)

        # Send response
        resp = MessagingResponse()
        resp.message(final_response)

        logger.info(f"Response sent in {response_time:.2f}s")
        return Response(str(resp), mimetype="application/xml")

    except Exception as e:
        logger.error(f"Error processing request: {str(e)}", exc_info=True)
        resp = MessagingResponse()
        resp.message("Sorry, I encountered an issue. Please try again or call Symbiosis Hospital directly at [hospital number]. 🏥")
        return Response(str(resp), mimetype="application/xml")

# ============================================
# ENHANCED API ENDPOINTS
# ============================================

@app.route("/health", methods=['GET'])
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}, 200

@app.route("/stats/<phone_number>", methods=['GET'])
def get_stats(phone_number):
    """Get comprehensive statistics for a phone number."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Message count
        cursor.execute('SELECT COUNT(*) FROM conversations WHERE phone_number = ?', (phone_number,))
        message_count = cursor.fetchone()[0]
        
        # Intent distribution
        cursor.execute('''
            SELECT intent, COUNT(*) FROM conversation_analytics 
            WHERE phone_number = ? 
            GROUP BY intent
        ''', (phone_number,))
        intents = dict(cursor.fetchall())
        
        # Active reminders
        cursor.execute('''
            SELECT COUNT(*) FROM medication_reminders 
            WHERE phone_number = ? AND active = 1
        ''', (phone_number,))
        reminders = cursor.fetchone()[0]
        
        # Pending appointments
        cursor.execute('''
            SELECT COUNT(*) FROM appointments 
            WHERE phone_number = ? AND status = 'pending'
        ''', (phone_number,))
        appointments = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "phone_number": phone_number,
            "message_count": message_count,
            "intent_distribution": intents,
            "active_reminders": reminders,
            "pending_appointments": appointments,
            "generated_at": datetime.now().isoformat()
        }, 200
    except Exception as e:
        logger.error(f"Failed to get stats: {str(e)}")
        return {"error": str(e)}, 500

@app.route("/profile/<phone_number>", methods=['GET'])
def get_profile_endpoint(phone_number):
    """Get user profile via API."""
    profile = get_user_profile(phone_number)
    if profile:
        return jsonify(profile), 200
    return {"error": "Profile not found"}, 404

@app.route("/reminders/<phone_number>", methods=['GET'])
def get_reminders_endpoint(phone_number):
    """Get active medication reminders."""
    reminders = get_active_reminders(phone_number)
    return jsonify({"phone_number": phone_number, "reminders": reminders}), 200

@app.route("/symptoms/<phone_number>", methods=['GET'])
def get_symptoms_endpoint(phone_number):
    """Get symptom history."""
    days = request.args.get('days', 7, type=int)
    symptoms = get_symptom_history(phone_number, days)
    return jsonify({"phone_number": phone_number, "history": symptoms, "days": days}), 200

@app.route("/analytics", methods=['GET'])
def get_analytics():
    """Get overall system analytics."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Total users
        cursor.execute('SELECT COUNT(DISTINCT phone_number) FROM user_profiles')
        total_users = cursor.fetchone()[0]
        
        # Total conversations
        cursor.execute('SELECT COUNT(*) FROM conversations')
        total_messages = cursor.fetchone()[0]
        
        # Intent distribution
        cursor.execute('''
            SELECT intent, COUNT(*) FROM conversation_analytics 
            GROUP BY intent
        ''')
        intent_stats = dict(cursor.fetchall())
        
        # Average response time
        cursor.execute('SELECT AVG(response_time) FROM conversation_analytics')
        avg_response = cursor.fetchone()[0] or 0
        
        conn.close()
        
        return jsonify({
            "total_users": total_users,
            "total_messages": total_messages,
            "intent_distribution": intent_stats,
            "avg_response_time_seconds": round(avg_response, 2),
            "generated_at": datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Analytics error: {str(e)}")
        return {"error": str(e)}, 500

if __name__ == "__main__":
    # Initialize database on startup
    init_db()
    logger.info("🚀 Enhanced WhatsApp Medical Bot starting...")
    logger.info("📊 Features: Intent Detection, Profile Management, Symptom Tracking, Med Reminders, Analytics")
    app.run(host="0.0.0.0", port=5000, debug=False)
