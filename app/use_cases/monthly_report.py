"""
Monthly Report Use Case
"""
from datetime import datetime
from app.services.firestore_service import FirestoreService
from app.core.utils import format_currency_br, ensure_string_id


class MonthlyReportUseCase:
    """Use case para relatório mensal"""
    
    def __init__(self):
        self.db = FirestoreService()
    
    def execute(self, chat_id: str) -> dict:
        """
        Gera relatório mensal de gastos com soma por categoria
        
        Returns:
            dict: {"status": "ok", "total": float, "by_category": dict, "formatted": str}
        """
        chat_id_str = ensure_string_id(chat_id)
        now = datetime.now()
        start = datetime(now.year, now.month, 1)
        end = datetime(now.year, now.month + 1, 1) if now.month < 12 else datetime(now.year + 1, 1, 1)
        
        expenses = self.db.get_expenses(chat_id_str, start, end)
        
        if not expenses:
            return {
                "status": "ok",
                "total": 0.0,
                "by_category": {},
                "formatted": "💸 Nada."
            }
        
        # Soma por categoria
        by_category = {}
        total = 0.0
        
        for exp in expenses:
            amount = exp.get('amount', 0)
            category = exp.get('category', 'outros')
            total += amount
            by_category[category] = by_category.get(category, 0) + amount
        
        # Formata texto
        txt = ""
        for cat, cat_total in by_category.items():
            txt += f"• {cat}: R$ {format_currency_br(cat_total)}\n"
        
        txt += f"\n📊 Total: R$ {format_currency_br(total)}\n"
        
        # Adiciona itens detalhados
        for exp in expenses:
            txt += f"• R$ {format_currency_br(exp['amount'])} - {exp.get('item')}\n"
        
        return {
            "status": "ok",
            "total": total,
            "by_category": by_category,
            "formatted": txt
        }
