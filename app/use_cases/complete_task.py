"""
Complete Task Use Case
"""
from app.services.firestore_service import FirestoreService
from app.core.utils import ensure_string_id


class CompleteTaskUseCase:
    """Use case para concluir tarefa"""
    
    def __init__(self):
        self.db = FirestoreService()
    
    def execute(self, chat_id: str, item: str) -> dict:
        """
        Marca tarefa como concluída
        
        Returns:
            dict: {"status": "completed" | "not_found", "item": item}
        """
        chat_id_str = ensure_string_id(chat_id)
        success = self.db.complete_task(chat_id_str, item)
        
        if success:
            return {"status": "completed", "item": item}
        return {"status": "not_found", "item": item}
