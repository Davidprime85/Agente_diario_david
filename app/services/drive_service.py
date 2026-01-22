"""
Google Drive Service
"""
import io
import logging
from typing import List, Dict, Optional
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from app.services.google_auth import GoogleAuth

logger = logging.getLogger(__name__)


class DriveService:
    """Serviço de integração com Google Drive"""
    
    def __init__(self):
        creds = GoogleAuth.get_credentials()
        self.service = build('drive', 'v3', credentials=creds) if creds else None
    
    def search_folder(self, name_query: str) -> Optional[Dict]:
        """
        REGRA 5: Busca pasta com case-insensitive contains.
        Usa 'contains' na query do Drive API.
        """
        if not self.service:
            return None
        
        try:
            query = (
                f"mimeType='application/vnd.google-apps.folder' "
                f"and name contains '{name_query}' "
                f"and trashed=false"
            )
            
            result = (
                self.service.files()
                .list(q=query, fields="files(id, name)")
                .execute()
            )
            
            folders = result.get('files', [])
            return folders[0] if folders else None
        except Exception as e:
            logger.error(f"Erro ao buscar pasta: {e}")
            return None
    
    def list_files_in_folder(self, folder_id: str) -> List[Dict]:
        """Lista arquivos de uma pasta"""
        if not self.service:
            return []
        
        try:
            query = f"'{folder_id}' in parents and trashed=false"
            result = (
                self.service.files()
                .list(q=query, fields="files(id, name, mimeType)")
                .execute()
            )
            return result.get('files', [])
        except Exception as e:
            logger.error(f"Erro ao listar arquivos: {e}")
            return []
    
    def read_file_content(self, file_id: str, mime_type: str, max_length: int = 3000) -> str:
        """Lê conteúdo de um arquivo (primeiros max_length chars)"""
        if not self.service:
            return ""
        
        try:
            if "google-apps.document" in mime_type:
                request = self.service.files().export_media(fileId=file_id, mimeType='text/plain')
            else:
                request = self.service.files().get_media(fileId=file_id)
            
            file_handle = io.BytesIO()
            downloader = MediaIoBaseDownload(file_handle, request)
            done = False
            
            while not done:
                _, done = downloader.next_chunk()
            
            # Decodifica com tratamento de erros
            content = file_handle.getvalue().decode('utf-8', errors='ignore')
            return content[:max_length]
        except Exception as e:
            logger.error(f"Erro ao ler arquivo: {e}")
            return ""
