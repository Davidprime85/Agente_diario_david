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
            logger.error("Calendar service não disponível")
            return False
        
        if not self.calendar_id:
            logger.error("GOOGLE_CALENDAR_ID não configurado")
            return False
        
        try:
            from datetime import datetime, timedelta
            
            # Normaliza formato ISO
            start_clean = start_iso.replace('Z', '+00:00')
            if not end_iso:
                # Se não tem end, adiciona 1 hora
                try:
                    dt_start = datetime.fromisoformat(start_clean)
                    dt_end = dt_start + timedelta(hours=1)
                    end_iso = dt_end.isoformat()
                except:
                    end_iso = start_iso
            
            end_clean = end_iso.replace('Z', '+00:00')
            
            # Garante timezone se não tiver
            if '+' not in start_clean and '-' not in start_clean[-6:]:
                start_clean += '-03:00'
            if '+' not in end_clean and '-' not in end_clean[-6:]:
                end_clean += '-03:00'
            
            body = {
                'summary': title,
                'description': description or "",
                'start': {'dateTime': start_clean, 'timeZone': 'America/Sao_Paulo'},
                'end': {'dateTime': end_clean, 'timeZone': 'America/Sao_Paulo'}
            }
            
            logger.info(f"Criando evento: {title} em {start_clean}")
            result = self.service.events().insert(calendarId=self.calendar_id, body=body).execute()
            logger.info(f"Evento criado com sucesso: {result.get('id')}")
            return True
        except Exception as e:
            logger.error(f"Erro ao criar evento: {e}", exc_info=True)
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
