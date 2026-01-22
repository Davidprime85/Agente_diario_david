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
        Adiciona gasto financeiro
        
        Args:
            amount_str: Valor em formato BR ("50,00") ou EN ("50.00")
        
        Returns:
            dict: {"status": "created" | "error", "amount": float, "message": str}
        """
        try:
            chat_id_str = ensure_string_id(chat_id)
            # REGRA 2: Conversão de moeda padronizada
            amount = to_float(amount_str)
            
            self.db.add_expense(chat_id_str, amount, category, item)
            
            return {
                "status": "created",
                "amount": amount,
                "category": category,
                "item": item
            }
        except ValueError as e:
            return {
                "status": "error",
                "message": f"Erro ao converter valor: {str(e)}"
            }
