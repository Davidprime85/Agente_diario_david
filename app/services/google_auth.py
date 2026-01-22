"""
Google Authentication Service (Singleton)
"""
import os
import json
import logging
from typing import Optional
from google.oauth2 import service_account
from google.cloud import firestore

from app.core.config import FIREBASE_CREDENTIALS

logger = logging.getLogger(__name__)


class GoogleAuth:
    """Gerencia autenticação unificada para todos os serviços Google"""
    
    SCOPES = [
        'https://www.googleapis.com/auth/calendar',
        'https://www.googleapis.com/auth/drive.readonly',
        'https://www.googleapis.com/auth/datastore'
    ]
    
    _credentials = None
    _firestore_client = None
    
    @classmethod
    def get_credentials(cls) -> Optional[service_account.Credentials]:
        """Retorna credenciais do Google (singleton)"""
        if cls._credentials:
            return cls._credentials
        
        try:
            if FIREBASE_CREDENTIALS:
                creds_dict = json.loads(FIREBASE_CREDENTIALS)
                cls._credentials = service_account.Credentials.from_service_account_info(
                    creds_dict, scopes=cls.SCOPES
                )
            elif os.path.exists("firebase-key.json"):
                cls._credentials = service_account.Credentials.from_service_account_file(
                    "firebase-key.json", scopes=cls.SCOPES
                )
            
            return cls._credentials
        except Exception as e:
            logger.error(f"❌ Erro na autenticação Google: {e}")
            return None
    
    @classmethod
    def get_firestore_client(cls) -> Optional[firestore.Client]:
        """Retorna cliente Firestore (singleton)"""
        if cls._firestore_client:
            return cls._firestore_client
        
        creds = cls.get_credentials()
        if creds:
            cls._firestore_client = firestore.Client(credentials=creds)
        
        return cls._firestore_client
