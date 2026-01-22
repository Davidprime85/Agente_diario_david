"""
Telegram Models
"""
from pydantic import BaseModel
from typing import Optional


class TelegramWebhook(BaseModel):
    """Modelo para webhook do Telegram"""
    message_id: int
    chat_id: str
    text: Optional[str] = None
    voice: Optional[dict] = None
