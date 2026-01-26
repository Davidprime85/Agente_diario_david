"""
Script de teste para listar pastas do Google Drive
Execute: python test_drive_folders.py
"""
import os
import sys

# Adiciona o diretório atual ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.drive_service import DriveService

def main():
    print("Testando acesso ao Google Drive...\n")
    
    drive = DriveService()
    
    if not drive.service:
        print("ERRO: Drive service nao disponivel")
        print("Verifique se as credenciais estao configuradas corretamente.")
        return
    
    email = drive.get_bot_email()
    print(f"Email da conta de servico: {email}\n")
    
    print("Listando todas as pastas acessiveis...\n")
    
    try:
        # Busca todas as pastas
        query = "mimeType='application/vnd.google-apps.folder' and trashed=false"
        all_folders = []
        page_token = None
        
        while True:
            result = (
                drive.service.files()
                .list(
                    q=query,
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
        
        print(f"OK - Total de pastas encontradas: {len(all_folders)}\n")
        
        if all_folders:
            print("Lista de pastas:")
            print("-" * 60)
            for folder in all_folders:
                shared_status = "[COMPARTILHADA]" if folder.get('shared') else "[MINHA]"
                print(f"{shared_status} {folder['name']} (ID: {folder['id']})")
            print("-" * 60)
        else:
            print("AVISO: Nenhuma pasta encontrada.")
            print("\nDica: Compartilhe uma pasta com o email acima e tente novamente.")
    
    except Exception as e:
        print(f"ERRO ao listar pastas: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
