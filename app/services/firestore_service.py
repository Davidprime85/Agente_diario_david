"""
Firestore Service - Persistência de dados
"""
import logging
from datetime import datetime
from typing import Any, List, Optional
from google.cloud import firestore

from app.services.google_auth import GoogleAuth
from app.core.utils import ensure_string_id

logger = logging.getLogger(__name__)


class FirestoreService:
    """Serviço de persistência no Firestore"""
    
    def __init__(self):
        self.db = GoogleAuth.get_firestore_client()
    
    def is_message_processed(self, chat_id: Any, message_id: int) -> bool:
        """
        REGRA 3: Anti-Loop - Verifica se mensagem já foi processada.
        Previne processamento duplicado de mensagens.
        """
        if not self.db:
            return False
        
        chat_id_str = ensure_string_id(chat_id)
        doc_ref = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('processed_ids')
            .document(str(message_id))
        )
        
        if doc_ref.get().exists:
            return True
        
        # Salva imediatamente para prevenir duplicação
        doc_ref.set({'timestamp': datetime.now()})
        return False
    
    def save_message(self, chat_id: Any, role: str, content: str):
        """Salva mensagem no histórico"""
        if not self.db:
            return
        
        chat_id_str = ensure_string_id(chat_id)
        self.db.collection('chats').document(chat_id_str).set(
            {"last_active": datetime.now()}, merge=True
        )
        self.db.collection('chats').document(chat_id_str).collection('mensagens').add({
            'role': role,
            'content': content,
            'timestamp': datetime.now()
        })
    
    def get_history(self, chat_id: Any, limit: int = 6) -> str:
        """Retorna histórico de mensagens"""
        if not self.db:
            return ""
        
        chat_id_str = ensure_string_id(chat_id)
        docs = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('mensagens')
            .order_by('timestamp', direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        
        messages = []
        for doc in docs:
            data = doc.to_dict()
            messages.append(f"{data['role']}: {data['content']}")
        
        return "\n".join(reversed(messages))
    
    def reset_history(self, chat_id: Any, limit: int = 50):
        """Limpa histórico de mensagens (últimas 50)"""
        if not self.db:
            return
        
        chat_id_str = ensure_string_id(chat_id)
        msgs = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('mensagens')
            .limit(limit)
            .stream()
        )
        for msg in msgs:
            msg.reference.delete()
    
    # --- TAREFAS ---
    def add_task(self, chat_id: Any, item: str):
        """Adiciona tarefa"""
        if not self.db:
            return
        
        chat_id_str = ensure_string_id(chat_id)
        self.db.collection('chats').document(chat_id_str).collection('tasks').add({
            'item': item,
            'status': 'pendente',
            'created_at': datetime.now()
        })
    
    def get_tasks(self, chat_id: Any) -> List[dict]:
        """Retorna lista de tarefas pendentes"""
        if not self.db:
            return []
        
        chat_id_str = ensure_string_id(chat_id)
        docs = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('tasks')
            .where(filter=firestore.FieldFilter('status', '==', 'pendente'))
            .stream()
        )
        
        return [doc.to_dict() for doc in docs]
    
    def complete_task(self, chat_id: Any, item: str) -> bool:
        """Marca tarefa como concluída"""
        if not self.db:
            return False
        
        chat_id_str = ensure_string_id(chat_id)
        docs = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('tasks')
            .where(filter=firestore.FieldFilter('status', '==', 'pendente'))
            .stream()
        )
        
        for doc in docs:
            if item.lower() in doc.to_dict()['item'].lower():
                doc.reference.update({'status': 'concluido'})
                return True
        return False
    
    # --- FINANCEIRO ---
    def add_expense(self, chat_id: Any, amount: float, category: str, item: str):
        """Adiciona gasto financeiro"""
        if not self.db:
            return
        
        chat_id_str = ensure_string_id(chat_id)
        self.db.collection('chats').document(chat_id_str).collection('expenses').add({
            'amount': amount,
            'category': category,
            'item': item,
            'timestamp': datetime.now()
        })
    
    def get_expenses(self, chat_id: Any, start_date: datetime, end_date: datetime) -> List[dict]:
        """Retorna gastos no período"""
        if not self.db:
            return []
        
        chat_id_str = ensure_string_id(chat_id)
        docs = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('expenses')
            .where(filter=firestore.FieldFilter('timestamp', '>=', start_date))
            .where(filter=firestore.FieldFilter('timestamp', '<=', end_date))
            .stream()
        )
        
        return [doc.to_dict() for doc in docs]
    
    def get_all_chats(self) -> List[str]:
        """Retorna lista de todos os chat_ids ativos"""
        if not self.db:
            return []
        
        docs = self.db.collection('chats').stream()
        return [doc.id for doc in docs]
    
    # --- CONTEXTO DE PASTA/ARQUIVO ---
    def save_last_folder_context(self, chat_id: Any, folder_name: str, files: List[dict]):
        """Salva contexto da última pasta listada"""
        if not self.db:
            logger.warning("Firestore não disponível para salvar contexto")
            return
        
        chat_id_str = ensure_string_id(chat_id)
        try:
            # Converte files para formato serializável
            files_data = []
            for f in files:
                files_data.append({
                    'name': f.get('name', ''),
                    'id': f.get('id', '')
                })
            
            self.db.collection('chats').document(chat_id_str).set({
                'last_folder_name': folder_name,
                'last_folder_files': files_data,
                'last_folder_timestamp': datetime.now()
            }, merge=True)
            
            logger.info(f"Contexto salvo: pasta={folder_name}, arquivos={len(files_data)}")
        except Exception as e:
            logger.error(f"Erro ao salvar contexto: {e}", exc_info=True)
    
    def get_last_folder_context(self, chat_id: Any) -> Optional[dict]:
        """Recupera contexto da última pasta listada"""
        if not self.db:
            return None
        
        chat_id_str = ensure_string_id(chat_id)
        try:
            doc = self.db.collection('chats').document(chat_id_str).get()
            
            if doc.exists:
                data = doc.to_dict()
                folder_name = data.get('last_folder_name')
                files = data.get('last_folder_files', [])
                
                if folder_name and files:
                    logger.info(f"Contexto recuperado: pasta={folder_name}, arquivos={len(files)}")
                    return {
                        'folder_name': folder_name,
                        'files': files
                    }
            
            logger.warning(f"Nenhum contexto encontrado para chat_id={chat_id_str}")
            return None
        except Exception as e:
            logger.error(f"Erro ao recuperar contexto: {e}", exc_info=True)
            return None
