"""
Google Calendar Service
"""
import logging
from typing import List, Dict, Optional
from googleapiclient.discovery import build

from app.services.google_auth import GoogleAuth
from app.core.config import GOOGLE_CALENDAR_ID

logger = logging.getLogger(__name__)


class CalendarService:
    """Serviço de integração com Google Calendar"""
    
    def __init__(self):
        creds = GoogleAuth.get_credentials()
        self.service = build('calendar', 'v3', credentials=creds) if creds else None
        self.calendar_id = GOOGLE_CALENDAR_ID
    
    def create_event(self, title: str, start_iso: str, end_iso: str, description: str = "") -> bool:
        """Cria evento no calendário"""
        if not self.service:
            return False
        
        try:
            body = {
                'summary': title,
                'description': description,
                'start': {'dateTime': start_iso},
                'end': {'dateTime': end_iso}
            }
            self.service.events().insert(calendarId=self.calendar_id, body=body).execute()
            return True
        except Exception as e:
            logger.error(f"Erro ao criar evento: {e}")
            return False
    
    def list_events(self, time_min: str, time_max: str) -> List[Dict]:
        """Lista eventos do calendário"""
        if not self.service:
            return []
        
        try:
            # Ajusta timezone se necessário
            if not time_min.endswith('Z') and '-' not in time_min[-6:]:
                time_min += '-03:00'
            if not time_max.endswith('Z') and '-' not in time_max[-6:]:
                time_max += '-03:00'
            
            result = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy='startTime'
                )
                .execute()
            )
            return result.get('items', [])
        except Exception as e:
            logger.error(f"Erro ao listar eventos: {e}")
            return []
