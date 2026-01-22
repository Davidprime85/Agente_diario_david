"""
List Events Use Case
"""
from app.services.calendar_service import CalendarService
from typing import List, Dict


class ListEventsUseCase:
    """Use case para listar eventos"""
    
    def __init__(self):
        self.calendar = CalendarService()
    
    def execute(self, time_min: str, time_max: str) -> dict:
        """
        Lista eventos do calendário
        
        Returns:
            dict: {"status": "ok", "events": List[Dict]} - JSON estruturado
        """
        events = self.calendar.list_events(time_min, time_max)
        
        # Retorna sempre JSON estruturado
        events_list = []
        for event in events:
            events_list.append({
                "summary": event.get('summary', 'Sem título'),
                "start": event.get('start', {}).get('dateTime', ''),
                "end": event.get('end', {}).get('dateTime', ''),
                "description": event.get('description', '')
            })
        
        return {
            "status": "ok",
            "events": events_list,
            "count": len(events_list)
        }
