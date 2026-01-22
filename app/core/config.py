"""
Core configuration and environment variables
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Environment Variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")
