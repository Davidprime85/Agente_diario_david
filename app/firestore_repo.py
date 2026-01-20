from datetime import date, datetime
import os
from typing import Any, Dict, List, Optional

from google.cloud import firestore


def _encode_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _encode_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode_value(item) for item in value]
    return value


def _ensure_credentials_from_repo() -> None:
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return
    current_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(current_dir)
    key_path = os.path.join(backend_dir, "firebase-key.json")
    if os.path.exists(key_path):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = key_path


def build_firestore_client(project_id: Optional[str] = None):
    _ensure_credentials_from_repo()
    if project_id:
        return firestore.Client(project=project_id)
    return firestore.Client()

class FirestoreRepository:
    # O repositório volta a aceitar o cliente externo
    def __init__(self, client):
        self.client = client

    def save(self, collection: str, data: Dict[str, Any], doc_id: Optional[str] = None):
        if doc_id:
            self.client.collection(collection).document(doc_id).set(_encode_value(data))
            return doc_id
        else:
            update_time, ref = self.client.collection(collection).add(_encode_value(data))
            return ref.id

    def list_documents(self, collection: str) -> List[Dict[str, Any]]:
        docs = self.client.collection(collection).stream()
        items = []
        for doc in docs:
            item = doc.to_dict()
            item["id"] = int(doc.id) if doc.id.isdigit() else doc.id
            items.append(item)
        return items

    def set_document(self, collection: str, doc_id: str, data: Dict[str, Any]):
        self.client.collection(collection).document(doc_id).set(_encode_value(data))

    def get_document(self, collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
        doc = self.client.collection(collection).document(doc_id).get()
        if doc.exists:
            return doc.to_dict()
        return None

    def update_document(self, collection: str, doc_id: str, data: Dict[str, Any]):
        self.client.collection(collection).document(doc_id).update(_encode_value(data))

    def add_document(self, collection: str, data: Dict[str, Any]):
        self.client.collection(collection).add(_encode_value(data))

    def clear_collection(self, collection: str):
        docs = self.client.collection(collection).stream()
        for doc in docs:
            doc.reference.delete()