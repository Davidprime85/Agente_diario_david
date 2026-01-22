"""
Create Event Use Case
"""
from app.services.calendar_service import CalendarService


class CreateEventUseCase:
    """Use case para criar evento no calendário"""
    
    def __init__(self):
        self.calendar = CalendarService()
    
    def execute(self, title: str, start_iso: str, end_iso: str, description: str = "") -> dict:
        """
        Cria evento no Google Calendar
        
        Returns:
            dict: {"status": "created" | "error", "title": title}
        """
        success = self.calendar.create_event(title, start_iso, end_iso, description)
        
        if success:
            return {"status": "created", "title": title}
        return {"status": "error", "title": title}
