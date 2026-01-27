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
        Inclui pastas compartilhadas e do Meu Drive.
        """
        if not self.service:
            logger.error("Drive service não disponível - verifique credenciais")
            return None
        
        # Limpa aspas e caracteres especiais para evitar erro de sintaxe
        safe_name = name_query.replace("'", "").replace('"', '').strip()
        
        try:
            # Lista TODAS as pastas acessíveis (incluindo compartilhadas)
            # Não filtra por nome primeiro, depois filtra no código
            query_all_folders = (
                "mimeType='application/vnd.google-apps.folder' "
                "and trashed=false"
            )
            
            all_folders = []
            page_token = None
            
            # Busca paginada para pegar todas as pastas
            while True:
                result = (
                    self.service.files()
                    .list(
                        q=query_all_folders,
                        fields="nextPageToken, files(id, name, shared)",
                        pageSize=100,
                        pageToken=page_token
                    )
                    .execute()
                )
                
                folders = result.get('files', [])
                all_folders.extend(folders)
                
                page_token = result.get('nextPageToken')
                if not page_token:
                    break
            
            logger.info(f"Total de pastas encontradas: {len(all_folders)}")
            
            # Normaliza o nome da busca (lowercase, sem espaços extras)
            search_name_lower = safe_name.lower().strip()
            
            # 1. Busca exata (case-insensitive)
            for folder in all_folders:
                if folder['name'].lower().strip() == search_name_lower:
                    logger.info(f"✅ Pasta encontrada (exata): {folder['name']} (ID: {folder['id']})")
                    return folder
            
            # 2. Busca contains (case-insensitive)
            for folder in all_folders:
                if search_name_lower in folder['name'].lower():
                    logger.info(f"✅ Pasta encontrada (contains): {folder['name']} (ID: {folder['id']})")
                    return folder
            
            # 3. Debug: lista primeiras 10 pastas para diagnóstico
            logger.warning(f"Nenhuma pasta encontrada com nome '{safe_name}'")
            logger.info(f"Primeiras 10 pastas disponíveis:")
            for folder in all_folders[:10]:
                shared_status = "compartilhada" if folder.get('shared') else "minha"
                logger.info(f"  - {folder['name']} ({shared_status})")
            
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
            # Google Docs/Sheets/Slides
            if "google-apps.document" in mime_type:
                request = self.service.files().export_media(fileId=file_id, mimeType='text/plain')
            elif "google-apps.spreadsheet" in mime_type:
                request = self.service.files().export_media(fileId=file_id, mimeType='text/csv')
            elif "google-apps.presentation" in mime_type:
                request = self.service.files().export_media(fileId=file_id, mimeType='text/plain')
            # PDFs - tenta múltiplas abordagens
            elif "pdf" in mime_type or "application/pdf" in mime_type:
                # Abordagem 1: Tenta exportar como texto (funciona para PDFs com texto)
                try:
                    logger.info(f"Tentando exportar PDF {file_id} como texto...")
                    request = self.service.files().export_media(fileId=file_id, mimeType='text/plain')
                except Exception as e1:
                    logger.warning(f"Export como texto falhou: {e1}, tentando HTML...")
                    # Abordagem 2: Tenta exportar como HTML (pode ter mais sucesso)
                    try:
                        request = self.service.files().export_media(fileId=file_id, mimeType='text/html')
                    except Exception as e2:
                        logger.warning(f"Export como HTML falhou: {e2}, tentando download direto...")
                        # Abordagem 3: Baixa o PDF direto (último recurso)
                        try:
                            request = self.service.files().get_media(fileId=file_id)
                        except Exception as e3:
                            logger.error(f"Todas as tentativas de ler PDF falharam: {e1}, {e2}, {e3}")
                            return ""
            # Texto simples
            elif "text" in mime_type or "plain" in mime_type:
                request = self.service.files().get_media(fileId=file_id)
            else:
                # Para outros tipos, tenta baixar direto
                logger.warning(f"Tipo de arquivo não suportado diretamente: {mime_type}, tentando download direto")
                request = self.service.files().get_media(fileId=file_id)
            
            file_handle = io.BytesIO()
            downloader = MediaIoBaseDownload(file_handle, request)
            done = False
            
            while not done:
                _, done = downloader.next_chunk()
            
            # Decodifica com tratamento de erros
            content_bytes = file_handle.getvalue()
            
            # Para PDFs baixados diretamente, tenta extrair texto usando PyPDF2 se disponível
            if "pdf" in mime_type and len(content_bytes) > 0:
                try:
                    import PyPDF2
                    from io import BytesIO
                    pdf_reader = PyPDF2.PdfReader(BytesIO(content_bytes))
                    text_content = ""
                    for page in pdf_reader.pages[:3]:  # Primeiras 3 páginas
                        text_content += page.extract_text() + "\n"
                    if text_content.strip():
                        logger.info(f"Texto extraído do PDF usando PyPDF2: {len(text_content)} chars")
                        return text_content[:max_length]
                except ImportError:
                    logger.warning("PyPDF2 não disponível, tentando decodificação direta")
                except Exception as e:
                    logger.warning(f"PyPDF2 falhou: {e}, tentando decodificação direta")
            
            # Decodificação padrão
            try:
                content = content_bytes.decode('utf-8', errors='ignore')
            except:
                # Tenta latin-1 se UTF-8 falhar
                try:
                    content = content_bytes.decode('latin-1', errors='ignore')
                except:
                    content = ""
            
            # Se o conteúdo parece binário ou vazio, retorna mensagem
            if len(content.strip()) < 50:
                logger.warning(f"Conteúdo extraído muito curto ({len(content)} chars), pode ser binário ou PDF escaneado")
                return ""
            
            return content[:max_length]
        except Exception as e:
            logger.error(f"Erro ao ler arquivo {file_id}: {e}", exc_info=True)
            return ""