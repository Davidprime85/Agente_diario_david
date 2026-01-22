"""
Telegram Service
"""
import requests
import logging
from typing import Any, Optional

from app.core.config import TELEGRAM_TOKEN
from app.core.utils import ensure_string_id

logger = logging.getLogger(__name__)


class TelegramService:
    """Serviço de integração com Telegram"""
    
    def __init__(self):
        self.token = TELEGRAM_TOKEN
        self.base_url = f"https://api.telegram.org/bot{self.token}" if self.token else None
    
    def send_message(self, chat_id: Any, text: str) -> bool:
        """Envia mensagem via Telegram"""
        if not self.base_url:
            return False
        
        try:
            chat_id_str = ensure_string_id(chat_id)
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": chat_id_str, "text": text},
                timeout=5
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")
            return False
    
    def download_voice(self, file_id: str) -> Optional[str]:
        """Baixa arquivo de áudio do Telegram"""
        if not self.base_url:
            return None
        
        try:
            response = requests.get(
                f"{self.base_url}/getFile?file_id={file_id}",
                timeout=5
            )
            file_path = response.json().get("result", {}).get("file_path")
            
            if not file_path:
                return None
            
            content = requests.get(
                f"https://api.telegram.org/file/bot{self.token}/{file_path}",
                timeout=10
            ).content
            
            temp_path = "/tmp/voice.ogg"
            with open(temp_path, "wb") as f:
                f.write(content)
            
            return temp_path
        except Exception as e:
            logger.error(f"Erro ao baixar áudio: {e}")
            return None
