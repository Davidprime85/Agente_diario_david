"""
Task Models
"""
from pydantic import BaseModel


class TaskCreate(BaseModel):
    """Modelo para criação de tarefa"""
    item: str


class TaskResponse(BaseModel):
    """Modelo de resposta de tarefa"""
    status: str
    item: str
