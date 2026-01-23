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
        self.creds = GoogleAuth.get_credentials()
        self.service = build('drive', 'v3', credentials=self.creds) if self.creds else None
        
        # --- NOVO: Captura o e-mail do robô para diagnóstico ---
        try:
            self.email = self.creds.service_account_email
        except AttributeError:
            self.email = "Email não identificado (verifique credenciais)"
            
    def get_bot_email(self) -> str:
        """Retorna o e-mail da conta de serviço"""
        return self.email
    
    def search_folder(self, name_query: str) -> Optional[Dict]:
        """
        Busca pasta com case-insensitive contains.
        """
        if not self.service:
            logger.error("Drive service não disponível - verifique credenciais")
            return None
        
        # Limpa aspas para evitar erro de sintaxe
        safe_name = name_query.replace("'", "")
        
        try:
            # 1. Busca exata primeiro (mais rápido)
            query_exact = (
                f"mimeType='application/vnd.google-apps.folder' "
                f"and name='{safe_name}' "
                f"and trashed=false"
            )
            
            result = (
                self.service.files()
                .list(q=query_exact, fields="files(id, name)")
                .execute()
            )
            
            folders = result.get('files', [])
            if folders:
                return folders[0]
            
            # 2. Se não encontrou exato, busca com contains (case-insensitive)
            query_contains = (
                f"mimeType='application/vnd.google-apps.folder' "
                f"and name contains '{safe_name}' "
                f"and trashed=false"
            )
            
            result = (
                self.service.files()
                .list(q=query_contains, fields="files(id, name)")
                .execute()
            )
            
            folders = result.get('files', [])
            if folders:
                logger.info(f"Encontrada pasta: {folders[0]['name']} (busca por contains)")
                return folders[0]
            
            logger.warning(f"Nenhuma pasta encontrada com nome contendo '{safe_name}'")
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar pasta: {e}", exc_info=True)
            return None
    
    def list_files_in_folder(self, folder_id: str) -> List[Dict]:
        """Lista arquivos de uma pasta"""
        if not self.service:
            return []
        
        try:
            query = f"'{folder_id}' in parents and trashed=false"
            result = (
                self.service.files()
                .list(q=query, fields="files(id, name, mimeType)", pageSize=15)
                .execute()
            )
            return result.get('files', [])
        except Exception as e:
            logger.error(f"Erro ao listar arquivos: {e}")
            return []
    
    def read_file_content(self, file_id: str, mime_type: str, max_length: int = 4000) -> str:
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
            return f"[Erro ao ler arquivo: {str(e)}]"