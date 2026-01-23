"""
Add Expense Use Case
"""
from app.services.firestore_service import FirestoreService
from app.core.utils import to_float, ensure_string_id


class AddExpenseUseCase:
    """Use case para adicionar gasto"""
    
    def __init__(self):
        self.db = FirestoreService()
    
    def execute(self, chat_id: str, amount_str: str, category: str, item: str) -> dict:
        """
        Adiciona gasto financeiro.
        amount_str pode ser: "50,00", "R$ 50,00" ou até o texto completo "Adicione gasto 50,00 lanche".
        to_float extrai o valor de qualquer um desses formatos.
        """
        chat_id_str = ensure_string_id(chat_id)
        amount = to_float(amount_str)

        if amount <= 0:
            return {
                "status": "error",
                "message": "Não consegui identificar o valor. Ex: Adicione gasto 50,00 lanche"
            }

        self.db.add_expense(chat_id_str, amount, category or "outros", item or "gasto")

        return {
            "status": "created",
            "amount": amount,
            "category": category or "outros",
            "item": item or "gasto"
        }
