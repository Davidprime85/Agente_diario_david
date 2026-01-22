"""
Cron Jobs Router
"""
import logging
from datetime import datetime
from fastapi import APIRouter

from app.services.firestore_service import FirestoreService
from app.services.calendar_service import CalendarService
from app.services.gemini_service import GeminiService
from app.core.config import TELEGRAM_TOKEN
import requests

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cron", tags=["cron"])

# Instâncias dos serviços
db = FirestoreService()
calendar = CalendarService()
ai = GeminiService()


def send_telegram_message(chat_id: str, text: str):
    """Helper para enviar mensagem via Telegram"""
    if TELEGRAM_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=5
            )
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")


@router.get("/bom-dia")
def cron_bom_dia():
    """
    Cron job para enviar mensagem matinal automática.
    Lê eventos do dia, tarefas, usa Gemini para gerar mensagem motivacional
    e envia para todos os usuários.
    """
    if not db.db:
        return {"sent": 0, "error": "Firestore não disponível"}
    
    try:
        all_chats = db.get_all_chats()
        count = 0
        now = datetime.now()
        time_min = now.strftime("%Y-%m-%dT00:00:00-03:00")
        time_max = now.strftime("%Y-%m-%dT23:59:59-03:00")
        
        for chat_id in all_chats:
            # Busca eventos do dia
            events = calendar.list_events(time_min, time_max)
            events_text = ", ".join([e.get('summary', '') for e in events]) if events else "Nada"
            
            # Busca tarefas
            from app.use_cases.list_tasks import ListTasksUseCase
            tasks_uc = ListTasksUseCase()
            tasks_text = tasks_uc.execute(chat_id)
            
            # Gera mensagem motivacional com Gemini
            prompt = (
                f"Crie um Bom Dia motivacional curto e positivo. "
                f"Agenda do dia: {events_text}. "
                f"Tarefas pendentes: {tasks_text}. "
                f"Seja breve e inspirador."
            )
            morning_msg = ai.generate_content(prompt)
            
            if morning_msg:
                # Envia mensagem
                if send_telegram_message(chat_id, morning_msg):
                    count += 1
        
        return {"sent": count, "total_users": len(all_chats)}
    
    except Exception as e:
        logger.error(f"Erro no cron: {e}")
        return {"sent": 0, "error": str(e)}
