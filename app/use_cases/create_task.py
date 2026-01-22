"""
Create Task Use Case
"""
from app.services.firestore_service import FirestoreService


class CreateTaskUseCase:
    """Use case para criar tarefa"""
    
    def __init__(self):
        self.db = FirestoreService()
    
    def execute(self, chat_id: str, item: str) -> dict:
        """
        Cria uma nova tarefa
        
        Returns:
            dict: {"status": "created", "item": item}
        """
        self.db.add_task(chat_id, item)
        return {"status": "created", "item": item}
