"""
Main FastAPI Application
"""
import logging
from fastapi import FastAPI
from app.routers import telegram, cron, web_api

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Jarvis AI Assistant",
    version="14.0.0 (Clean Architecture)",
    description="Bot de Telegram integrado com Google Calendar, Drive, Firestore e Gemini AI"
)

# Inclui routers
app.include_router(telegram.router)
app.include_router(cron.router)
app.include_router(web_api.router)


@app.get("/")
def root():
    return {
        "status": "Jarvis V14.0 Clean Architecture Online 🟢",
        "version": "14.0.0",
        "architecture": "Clean Architecture",
        "endpoints": {
            "telegram": "/telegram/webhook",
            "cron": "/cron/bom-dia",
            "api": "/api/*",
            "dashboard": "/api/dashboard"
        }
    }
