"""
Web API Router - Para frontend
"""
import logging
from datetime import datetime
from typing import List
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from google.cloud import firestore

from app.services.firestore_service import FirestoreService
from app.use_cases.create_task import CreateTaskUseCase
from app.use_cases.list_tasks import ListTasksUseCase
from app.use_cases.complete_task import CompleteTaskUseCase
from app.use_cases.create_event import CreateEventUseCase
from app.use_cases.list_events import ListEventsUseCase
from app.use_cases.add_expense import AddExpenseUseCase
from app.use_cases.monthly_report import MonthlyReportUseCase
from app.core.utils import ensure_string_id, to_float

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["web"])

db = FirestoreService()

# Use cases
create_task_uc = CreateTaskUseCase()
list_tasks_uc = ListTasksUseCase()
complete_task_uc = CompleteTaskUseCase()
create_event_uc = CreateEventUseCase()
list_events_uc = ListEventsUseCase()
add_expense_uc = AddExpenseUseCase()
monthly_report_uc = MonthlyReportUseCase()


@router.get("/health")
def health():
    """Health check endpoint"""
    return {"status": "ok", "service": "Jarvis API"}


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Dashboard HTML com resumo financeiro dos usuários"""
    if not db.db:
        return HTMLResponse("<html><body><h1>Erro: Firestore não disponível</h1></body></html>")
    
    html = """<html>
    <head>
        <title>Jarvis Dashboard</title>
        <style>
            body { font-family: sans-serif; padding: 20px; background: #f0f2f5; }
            .card { background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 10px; border-bottom: 1px solid #ddd; text-align: left; }
            th { background: #007bff; color: white; }
            .total { color: green; font-weight: bold; text-align: right; margin-top: 10px; }
        </style>
    </head>
    <body>
        <h1>📊 Dashboard Financeiro</h1>
    """
    
    try:
        all_chats = db.get_all_chats()
        
        for chat_id in all_chats:
            now = datetime.now()
            start = datetime(now.year, now.month, 1)
            end = datetime(now.year, now.month + 1, 1) if now.month < 12 else datetime(now.year + 1, 1, 1)
            
            expenses = db.get_expenses(chat_id, start, end)
            
            if expenses:
                rows = ""
                total = 0
                
                for expense in expenses:
                    amount = expense.get('amount', 0)
                    total += amount
                    rows += f"""
                        <tr>
                            <td>{expense.get('timestamp', datetime.now()).strftime('%d/%m')}</td>
                            <td>{expense.get('item', 'N/A')}</td>
                            <td>{expense.get('category', 'N/A')}</td>
                            <td>R$ {amount:.2f}</td>
                        </tr>
                    """
                
                html += f"""
                    <div class='card'>
                        <h2>User: {chat_id}</h2>
                        <table>
                            <tr>
                                <th>Data</th>
                                <th>Item</th>
                                <th>Categoria</th>
                                <th>Valor</th>
                            </tr>
                            {rows}
                        </table>
                        <div class='total'>Total: R$ {total:.2f}</div>
                    </div>
                """
        
        html += "</body></html>"
        return HTMLResponse(html)
    
    except Exception as e:
        logger.error(f"Erro no dashboard: {e}")
        return HTMLResponse(f"<html><body><h1>Erro: {str(e)}</h1></body></html>")


@router.get("/tasks/{chat_id}")
def get_tasks(chat_id: str):
    """Lista tarefas de um usuário"""
    chat_id_str = ensure_string_id(chat_id)
    return {"tasks": list_tasks_uc.execute(chat_id_str)}


@router.post("/tasks/{chat_id}")
def create_task(chat_id: str, item: str):
    """Cria nova tarefa"""
    chat_id_str = ensure_string_id(chat_id)
    result = create_task_uc.execute(chat_id_str, item)
    return result


@router.post("/tasks/{chat_id}/complete")
def complete_task(chat_id: str, item: str):
    """Conclui tarefa"""
    chat_id_str = ensure_string_id(chat_id)
    result = complete_task_uc.execute(chat_id_str, item)
    return result


@router.post("/events")
def create_event(title: str, start_iso: str, end_iso: str, description: str = ""):
    """Cria evento no calendário"""
    result = create_event_uc.execute(title, start_iso, end_iso, description)
    return result


@router.get("/events")
def get_events(time_min: str, time_max: str):
    """Lista eventos do calendário"""
    result = list_events_uc.execute(time_min, time_max)
    return result


@router.post("/expenses/{chat_id}")
def create_expense(chat_id: str, amount: str, category: str, item: str):
    """Registra novo gasto"""
    chat_id_str = ensure_string_id(chat_id)
    result = add_expense_uc.execute(chat_id_str, amount, category, item)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "Erro ao processar"))
    return result


@router.get("/expenses/{chat_id}/report")
def get_expense_report(chat_id: str):
    """Retorna relatório mensal de gastos"""
    chat_id_str = ensure_string_id(chat_id)
    result = monthly_report_uc.execute(chat_id_str)
    return result
