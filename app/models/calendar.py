"""
Calendar Models
"""
from pydantic import BaseModel
from typing import List, Optional


class EventCreate(BaseModel):
    """Modelo para criação de evento"""
    title: str
    start_iso: str
    end_iso: str
    description: Optional[str] = ""


class EventResponse(BaseModel):
    """Modelo de resposta de evento"""
    summary: str
    start: str
    end: str
    description: str


class ListEventsResponse(BaseModel):
    """Modelo de resposta de lista de eventos"""
    status: str
    events: List[EventResponse]
    count: int
