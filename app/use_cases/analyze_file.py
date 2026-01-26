"""
Analyze File Use Case
"""
import logging
from typing import Optional
from app.services.drive_service import DriveService
from app.services.gemini_service import GeminiService

logger = logging.getLogger(__name__)


class AnalyzeFileUseCase:
    """Use case para analisar arquivos de uma pasta"""
    
    def __init__(self):
        self.drive = DriveService()
        self.ai = GeminiService()
    
    def execute(self, folder_name: str, file_name: Optional[str] = None) -> dict:
        """
        Analisa conteúdo de uma pasta do Drive ou arquivo específico
        
        Args:
            folder_name: Nome da pasta
            file_name: (Opcional) Nome do arquivo específico para analisar
        
        Returns:
            dict: {"status": "ok" | "not_found" | "empty", "summary": str, "files": List}
        """
        # REGRA 5: Busca case-insensitive
        folder = self.drive.search_folder(folder_name)
        
        if not folder:
            return {
                "status": "not_found",
                "summary": f"❌ Não encontrei nenhuma pasta com o nome '{folder_name}'."
            }
        
        files = self.drive.list_files_in_folder(folder['id'])
        
        if not files:
            return {
                "status": "empty",
                "summary": f"📂 A pasta '{folder['name']}' está vazia."
            }
        
        # Se o usuário especificou um arquivo, tenta encontrá-lo
        target_file = None
        if file_name:
            file_name_lower = file_name.lower().strip()
            for f in files:
                if file_name_lower in f['name'].lower():
                    target_file = f
                    break
        
        # Lê conteúdo (primeiros 3000 chars)
        file_list_str = ""
        txt_content = ""
        count = 0
        
        # Se tem arquivo específico, analisa só ele; senão, analisa os primeiros 2
        files_to_analyze = [target_file] if target_file else files[:2]
        
        for f in files:
            file_list_str += f"- {f['name']}\n"
        
        for f in files_to_analyze:
            if f and "folder" not in f.get('mimeType', ''):
                logger.info(f"Lendo arquivo: {f['name']} (tipo: {f.get('mimeType', 'desconhecido')})")
                content = self.drive.read_file_content(f['id'], f['mimeType'], max_length=4000)
                if content:
                    logger.info(f"Conteúdo lido: {len(content)} caracteres")
                    txt_content += f"\n--- CONTEÚDO DE '{f['name']}' ---\n{content}\n"
                    count += 1
                else:
                    logger.warning(f"Não foi possível ler conteúdo do arquivo: {f['name']}")
        
        if not txt_content:
            logger.warning("Nenhum conteúdo foi extraído dos arquivos")
            return {
                "status": "ok",
                "summary": f"📄 Encontrei o arquivo mas não consegui extrair o conteúdo. O arquivo pode ser uma imagem, PDF complexo ou formato não suportado.",
                "files": [{"name": f['name'], "id": f['id']} for f in files],
                "folder_name": folder['name']
            }
        
        # Gera resumo com IA
        if target_file:
            prompt = (
                f"O usuário pediu para analisar o arquivo '{target_file['name']}' da pasta '{folder['name']}'.\n\n"
                f"Conteúdo do arquivo:\n{txt_content}\n\n"
                f"Faça um resumo detalhado sobre o que trata esse arquivo, principais pontos e informações relevantes."
            )
        else:
            prompt = (
                f"O usuário abriu a pasta '{folder['name']}'.\n"
                f"Arquivos disponíveis:\n{file_list_str}\n\n"
                f"Conteúdo extraído dos primeiros arquivos:\n{txt_content}\n\n"
                f"Resuma o que tem nessa pasta e diga que está pronto para perguntas."
            )
        
        summary = self.ai.generate_content(prompt)
        
        return {
            "status": "ok",
            "summary": summary,
            "files": [{"name": f['name'], "id": f['id']} for f in files],
            "folder_name": folder['name']
        }
