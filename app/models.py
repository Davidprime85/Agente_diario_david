from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Project(BaseModel):
    id: int
    name: str
    status: str = "active"
    description: Optional[str] = None
    deadline: Optional[datetime] = None


class AgendaItem(BaseModel):
    id: int
    title: str
    start: datetime
    end: datetime
    location: Optional[str] = None
    project_id: Optional[int] = None


class Task(BaseModel):
    id: int
    title: str
    due: Optional[datetime] = None
    project_id: Optional[int] = None
    status: str = "open"
    priority: str = "normal"


class OrchestrateRequest(BaseModel):
    intent: str = Field(..., description="Ex: 'planejar semana', 'agendar reunião'")
    payload: Dict[str, Any] = Field(default_factory=dict)


class OrchestrateResponse(BaseModel):
    steps: List[str]
    actions: List[str]
    notes: List[str]
