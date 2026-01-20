import os
from typing import Optional

from app.firestore_repo import FirestoreRepository, build_firestore_client

_repo: Optional[FirestoreRepository] = None


def get_repo() -> FirestoreRepository:
    global _repo
    if _repo is None:
        project_id = os.getenv("FIRESTORE_PROJECT")
        client = build_firestore_client(project_id)
        _repo = FirestoreRepository(client)
    return _repo
