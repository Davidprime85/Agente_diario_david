"""
Finance Models
"""
from pydantic import BaseModel
from typing import Optional


class ExpenseCreate(BaseModel):
    """Modelo para criação de gasto"""
    amount: str  # Aceita "50,00" ou "50.00"
    category: str = "outros"
    item: str


class ExpenseResponse(BaseModel):
    """Modelo de resposta de gasto"""
    status: str
    amount: Optional[float] = None
    category: Optional[str] = None
    item: Optional[str] = None


class MonthlyReportResponse(BaseModel):
    """Modelo de resposta de relatório mensal"""
    status: str
    total: float
    by_category: dict
    formatted: str
