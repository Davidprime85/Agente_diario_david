"""
List Tasks Use Case
"""
from app.services.firestore_service import FirestoreService
from app.core.utils import ensure_string_id


class ListTasksUseCase:
    """Use case para listar tarefas"""
    
    def __init__(self):
        self.db = FirestoreService()
    
    def execute(self, chat_id: str) -> str:
        """
        Lista tarefas pendentes formatadas
        
        Returns:
            str: Lista formatada com bullets
        """
        chat_id_str = ensure_string_id(chat_id)
        tasks = self.db.get_tasks(chat_id_str)
        
        if tasks:
            items = [t.get('item', '') for t in tasks]
            return "📝 \n" + "\n".join([f"- {item}" for item in items])
        return "✅ Nada."
