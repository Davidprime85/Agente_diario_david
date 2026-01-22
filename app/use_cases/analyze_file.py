"""
Analyze File Use Case
"""
from app.services.drive_service import DriveService
from app.services.gemini_service import GeminiService


class AnalyzeFileUseCase:
    """Use case para analisar arquivos de uma pasta"""
    
    def __init__(self):
        self.drive = DriveService()
        self.ai = GeminiService()
    
    def execute(self, folder_name: str) -> dict:
        """
        Analisa conteúdo de uma pasta do Drive
        
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
        
        # Lê conteúdo (primeiros 3000 chars)
        file_list_str = ""
        txt_content = ""
        count = 0
        
        for f in files:
            file_list_str += f"- {f['name']}\n"
            if "folder" not in f['mimeType'] and count < 2:
                content = self.drive.read_file_content(f['id'], f['mimeType'], max_length=3000)
                if content:
                    txt_content += f"\n--- CONTEÚDO DE '{f['name']}' ---\n{content}\n"
                    count += 1
        
        # Gera resumo com IA
        prompt = (
            f"O usuário abriu a pasta '{folder['name']}'.\n"
            f"Arquivos disponíveis:\n{file_list_str}\n\n"
            f"Conteúdo extraído:\n{txt_content}\n\n"
            f"Resuma o que tem nessa pasta e diga que está pronto para perguntas."
        )
        
        summary = self.ai.generate_content(prompt)
        
        return {
            "status": "ok",
            "summary": summary,
            "files": [{"name": f['name'], "id": f['id']} for f in files],
            "folder_name": folder['name']
        }
