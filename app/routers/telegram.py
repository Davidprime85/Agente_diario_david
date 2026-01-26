"""
Telegram Router - Webhook endpoint
"""
import logging
import requests
from fastapi import APIRouter, Request
import google.generativeai as genai

from app.services.firestore_service import FirestoreService
from app.services.gemini_service import GeminiService
from app.services.drive_service import DriveService  # Added import
# TelegramService não usado diretamente aqui, usando requests diretamente
from app.use_cases.create_task import CreateTaskUseCase
from app.use_cases.list_tasks import ListTasksUseCase
from app.use_cases.complete_task import CompleteTaskUseCase
from app.use_cases.create_event import CreateEventUseCase
from app.use_cases.list_events import ListEventsUseCase
from app.use_cases.add_expense import AddExpenseUseCase
from app.use_cases.monthly_report import MonthlyReportUseCase
from app.use_cases.analyze_file import AnalyzeFileUseCase
from app.core.utils import ensure_string_id
from app.core.config import TELEGRAM_TOKEN

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])

# Instâncias dos serviços e use cases
db = FirestoreService()
ai = GeminiService()

# Use cases
create_task_uc = CreateTaskUseCase()
list_tasks_uc = ListTasksUseCase()
complete_task_uc = CompleteTaskUseCase()
create_event_uc = CreateEventUseCase()
list_events_uc = ListEventsUseCase()
add_expense_uc = AddExpenseUseCase()
monthly_report_uc = MonthlyReportUseCase()
analyze_file_uc = AnalyzeFileUseCase()


def send_telegram_message(chat_id: str, text: str):
    """Helper para enviar mensagem via Telegram"""
    if TELEGRAM_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=5
            )
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")


def send_quick_reply(chat_id: str, text: str, options: list):
    """Envia mensagem com quick reply buttons"""
    keyboard = {
        "keyboard": [[{"text": opt} for opt in options]],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }
    
    if TELEGRAM_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "reply_markup": keyboard
                },
                timeout=5
            )
        except Exception as e:
            logger.error(f"Erro ao enviar quick reply: {e}")


def download_voice(file_id: str) -> str:
    """Baixa arquivo de áudio do Telegram"""
    if not TELEGRAM_TOKEN:
        return None
    
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}",
            timeout=5
        )
        path = r.json().get("result", {}).get("file_path")
        if not path:
            return None
        
        content = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}",
            timeout=10
        ).content
        
        temp_path = "/tmp/voice.ogg"
        with open(temp_path, "wb") as f:
            f.write(content)
        
        return temp_path
    except Exception as e:
        logger.error(f"Erro ao baixar áudio: {e}")
        return None


@router.post("/webhook")
async def webhook(request: Request):
    """Endpoint principal do webhook do Telegram"""
    try:
        data = await request.json()
        
        # Tratamento de callback_query (botões inline)
        if "callback_query" in data:
            callback = data["callback_query"]
            chat_id = ensure_string_id(callback["message"]["chat"]["id"])
            callback_data = callback.get("data", "")
            
            # Se clicou em "Resumo" ou similar após listar arquivos
            if callback_data in ["resumo", "analyze"]:
                context = db.get_last_folder_context(chat_id)
                if context:
                    send_telegram_message(chat_id, f"📂 Analisando '{context['folder_name']}'...")
                    result = analyze_file_uc.execute(context['folder_name'])
                    if result["status"] == "ok":
                        send_telegram_message(chat_id, result.get("summary", "Erro ao analisar."))
                    else:
                        send_telegram_message(chat_id, result.get("summary", "Erro ao analisar."))
                else:
                    send_telegram_message(chat_id, "📂 Use /pasta <nome> para listar arquivos primeiro.")
            
            # Responde ao callback para remover o "loading" do botão
            if TELEGRAM_TOKEN:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": callback["id"]},
                    timeout=5
                )
            
            return {"status": "callback_processed"}
        
        if "message" not in data:
            return {"status": "ok"}
        
        msg = data["message"]
        
        # REGRA 1: Chat ID sempre string
        chat_id = ensure_string_id(msg["chat"]["id"])
        msg_id = msg.get("message_id")
        text = msg.get("text", "")
        
        # --- COMANDOS ESPECIAIS ---
        if text == "/reset":
            db.reset_history(chat_id, limit=50)
            send_telegram_message(chat_id, "🧠 Memória limpa.")
            return {"status": "reset"}
        
        if text == "/menu":
            send_inline_keyboard(chat_id, "🤖 **Menu Principal**\n\nEscolha uma opção:")
            return {"status": "menu"}
        
        if text == "/resumo":
            # Resumo do dia
            from datetime import datetime
            now = datetime.now()
            time_min = now.strftime("%Y-%m-%dT00:00:00-03:00")
            time_max = now.strftime("%Y-%m-%dT23:59:59-03:00")
            
            events_result = list_events_uc.execute(time_min, time_max)
            tasks_result = list_tasks_uc.execute(chat_id)
            finance_result = monthly_report_uc.execute(chat_id)
            
            resumo = f"📊 **Resumo do Dia**\n\n"
            resumo += f"📅 Eventos: {events_result.get('count', 0)}\n"
            resumo += f"✅ Tarefas: {tasks_result}\n"
            resumo += f"💰 {finance_result.get('formatted', 'Nada')}"
            
            send_telegram_message(chat_id, resumo)
            return {"status": "resumo"}
        
        # COMANDO PASTA COM DIAGNÓSTICO
        if text.startswith("/pasta") or text.startswith("/arquivos"):
            parts = text.split(" ", 1)
            if len(parts) < 2:
                send_telegram_message(chat_id, "📂 Qual pasta? Digite ex: /pasta Projeto Beta")
                return {"status": "ask_name"}
            
            folder_query = parts[1]
            send_telegram_message(chat_id, f"🔍 Procurando pasta '{folder_query}'...")
            
            # Executa o Use Case
            result = analyze_file_uc.execute(folder_query)
            
            if result["status"] == "not_found":
                # --- DIAGNÓSTICO DE EMAIL ---
                drive_svc = DriveService()
                bot_email = drive_svc.get_bot_email()
                
                # Lista algumas pastas disponíveis para debug
                try:
                    query_all = "mimeType='application/vnd.google-apps.folder' and trashed=false"
                    folders_result = drive_svc.service.files().list(
                        q=query_all,
                        fields="files(id, name, shared)",
                        pageSize=10
                    ).execute()
                    available_folders = folders_result.get('files', [])
                    
                    folders_list = "\n".join([
                        f"  • {f['name']} {'(compartilhada)' if f.get('shared') else ''}"
                        for f in available_folders[:5]
                    ])
                    
                    msg_erro = (
                        f"❌ Não encontrei a pasta '{folder_query}'.\n\n"
                        f"🕵️ **Diagnóstico:**\n"
                        f"Email do bot: `{bot_email}`\n\n"
                        f"📋 **Pastas que eu consigo ver ({len(available_folders)}):**\n{folders_list}\n\n"
                        f"👉 **Solução:**\n"
                        f"1. Vá no Google Drive\n"
                        f"2. Clique na pasta '{folder_query}' com botão direito\n"
                        f"3. Compartilhar > Cole o email acima como **Editor**\n"
                        f"4. Aguarde alguns segundos e tente novamente"
                    )
                except Exception as e:
                    logger.error(f"Erro ao listar pastas: {e}")
                    msg_erro = (
                        f"❌ Não encontrei a pasta '{folder_query}'.\n\n"
                        f"🕵️ **Diagnóstico:**\n"
                        f"Email do bot: `{bot_email}`\n\n"
                        f"👉 Compartilhe a pasta com esse email no Google Drive."
                    )
                
                send_telegram_message(chat_id, msg_erro)
                
            elif result["status"] == "empty":
                send_telegram_message(chat_id, result["summary"])
            else:
                # Lista arquivos
                files_text = "\n".join([f"📄 {f['name']}" for f in result["files"][:10]])
                resp_text = f"📂 **Pasta: {result['folder_name']}**\n\n{files_text}\n\n🔎 **O que você quer saber sobre esses arquivos?**"
                
                # Envia com botões de ação rápida
                send_quick_reply(chat_id, resp_text, ["📝 Resumo", "📊 Analisar"])
                
                # NOVO: Salva contexto da pasta para análise posterior
                db.save_last_folder_context(chat_id, result['folder_name'], result["files"])
                
                # Salva no histórico
                db.save_message(chat_id, "model", f"Listei os arquivos da pasta {result['folder_name']}: {files_text}")
            
            return {"status": "folder_listed"}
        
        # REGRA 3: Anti-Loop - Verifica se mensagem já foi processada
        if msg_id and db.is_message_processed(chat_id, msg_id):
            logger.info(f"Mensagem {msg_id} já processada, ignorando...")
            return {"status": "ignored"}
        
        # PROCESSAMENTO
        ai_response = None
        
        if "text" in msg:
            db.save_message(chat_id, "user", text)
            
            # Verificação rápida: se o usuário pediu resumo/análise e há contexto de pasta salvo
            text_lower = text.lower().strip()
            text_original = text.strip()
            
            # Palavras-chave que indicam análise/resumo
            analysis_keywords = ["resumo", "analise", "analisar", "leia", "o que trata", "explique", "resuma", "analisa"]
            
            # Verifica se há pedido de análise
            is_analysis_request = any(keyword in text_lower for keyword in analysis_keywords)
            
            if is_analysis_request:
                context = db.get_last_folder_context(chat_id)
                if context:
                    logger.info(f"Detectado pedido de análise. Contexto: {context.get('folder_name')}")
                    
                    # Tenta extrair nome do arquivo se mencionado
                    file_name = None
                    context_files = context.get('files', [])
                    
                    # Procura se o usuário mencionou algum arquivo da lista
                    for file_info in context_files:
                        file_display_name = file_info.get('name', '')
                        file_name_lower = file_display_name.lower()
                        
                        # Verifica se o nome completo do arquivo está no texto
                        if file_name_lower in text_lower:
                            file_name = file_display_name
                            logger.info(f"Arquivo específico detectado: {file_name}")
                            break
                        
                        # Verifica palavras-chave do nome do arquivo
                        file_keywords = [w for w in file_name_lower.replace('.pdf', '').replace('.doc', '').split('_') if len(w) > 3]
                        if any(keyword in text_lower for keyword in file_keywords):
                            file_name = file_display_name
                            logger.info(f"Arquivo detectado por palavras-chave: {file_name}")
                            break
                    
                    # Se não encontrou arquivo específico mas há apenas 1 arquivo, usa ele
                    if not file_name and len(context_files) == 1:
                        file_name = context_files[0].get('name')
                        logger.info(f"Usando único arquivo disponível: {file_name}")
                    
                    # Processa diretamente sem passar pela IA primeiro
                    folder_name = context['folder_name']
                    if file_name:
                        send_telegram_message(chat_id, f"📄 Analisando arquivo '{file_name}'...")
                    else:
                        send_telegram_message(chat_id, f"📂 Analisando pasta '{folder_name}'...")
                    
                    try:
                        result = analyze_file_uc.execute(folder_name, file_name)
                        
                        if result["status"] == "ok":
                            summary = result.get("summary", "")
                            if summary:
                                send_telegram_message(chat_id, summary)
                            else:
                                send_telegram_message(chat_id, "❌ Não consegui gerar o resumo. Tente novamente.")
                        elif result["status"] == "not_found":
                            send_telegram_message(chat_id, f"❌ Não encontrei a pasta '{folder_name}'. Use /pasta <nome> para listar.")
                        else:
                            send_telegram_message(chat_id, result.get("summary", "Erro ao analisar."))
                        
                        # Salva no histórico
                        db.save_message(chat_id, "model", f"Analisei {'arquivo' if file_name else 'pasta'}: {file_name or folder_name}")
                    except Exception as e:
                        logger.error(f"Erro ao analisar arquivo: {e}", exc_info=True)
                        send_telegram_message(chat_id, f"❌ Erro ao analisar: {str(e)}")
                    
                    return {"status": "analyzed"}
            
            history = db.get_history(chat_id)
            ai_response = ai.chat(text, history)
        
        elif "voice" in msg:
            db.save_message(chat_id, "user", "[Audio]")
            voice_path = download_voice(msg["voice"]["file_id"])
            
            if voice_path:
                send_telegram_message(chat_id, "🎧...")
                audio_file = genai.upload_file(voice_path, mime_type="audio/ogg")
                history = db.get_history(chat_id)
                ai_response = ai.chat(audio_file, history, is_audio=True)
        
        # EXECUÇÃO DE AÇÕES via Use Cases
        if ai_response:
            intent = ai_response.get("intent")
            response_text = ""

            # Fallback: IA falhou mas o texto parece add_expense — extrair valor direto do texto
            _erro_ia = ai_response.get("response") or ""
            _is_erro = _erro_ia in ("Erro IA.", "Desculpe, não consegui processar. Tente de novo.", "Desculpe, tive um problema. Tente em instantes.")
            if _is_erro and text:
                from app.core.utils import to_float
                import re
                amt = to_float(text)
                if amt > 0 and any(w in text.lower() for w in ["gasto", "despesa", "adicionar", "gastei"]):
                    m = re.search(r'[\d.,]+\s*(.+)', text)
                    item = (m.group(1).strip() if (m and m.group(1).strip()) else "gasto")
                    result = add_expense_uc.execute(chat_id, text, "outros", item)
                    if result["status"] == "created":
                        from app.core.utils import format_currency_br
                        response_text = f"💸 Gasto: R$ {format_currency_br(result['amount'])} - {result.get('item', '')}"

            if not response_text:
                if intent == "conversa":
                    response_text = ai_response.get("response", "")

                elif intent == "agendar":
                    title = ai_response.get("title", "")
                    start_iso = ai_response.get("start_iso", "")
                    end_iso = ai_response.get("end_iso", "")
                    description = ai_response.get("description", "")
                    
                    if not title or not start_iso:
                        response_text = "❌ Não consegui entender a data/hora. Ex: 'Lembrar amanhã 8h colocar comida'"
                    else:
                        result = create_event_uc.execute(title, start_iso, end_iso, description)
                        if result["status"] == "created":
                            # Formata data/hora para mostrar ao usuário
                            try:
                                from datetime import datetime
                                dt = datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
                                hora_formatada = dt.strftime('%d/%m às %H:%M')
                                response_text = f"✅ Lembrete agendado!\n\n📅 {title}\n🕐 {hora_formatada}"
                                if description:
                                    response_text += f"\n📝 {description}"
                            except:
                                response_text = f"✅ Lembrete agendado: {title}"
                        else:
                            response_text = f"❌ Erro ao agendar. Verifique a data/hora informada."

                elif intent == "consultar_agenda":
                    result = list_events_uc.execute(
                        time_min=ai_response.get("time_min", ""),
                        time_max=ai_response.get("time_max", "")
                    )
                    if result["events"]:
                        event_list = [e.get('summary', 'Sem título') for e in result["events"]]
                        response_text = "📅 " + "\n".join(event_list)
                    else:
                        response_text = "📅 Vazia."

                elif intent == "add_task":
                    result = create_task_uc.execute(chat_id, ai_response.get("item", ""))
                    response_text = f"📝 Add: {result['item']}"

                elif intent == "list_tasks":
                    response_text = list_tasks_uc.execute(chat_id)

                elif intent == "complete_task":
                    result = complete_task_uc.execute(chat_id, ai_response.get("item", ""))
                    response_text = "✅ Feito." if result["status"] == "completed" else "🔍 Não achei."

                elif intent == "add_expense":
                    result = add_expense_uc.execute(
                        chat_id=chat_id,
                        amount_str=text,
                        category=ai_response.get("category", "outros"),
                        item=ai_response.get("item", "")
                    )
                    if result["status"] == "created":
                        from app.core.utils import format_currency_br
                        response_text = f"💸 Gasto: R$ {format_currency_br(result['amount'])} - {result.get('item', '')}"
                    else:
                        response_text = f"❌ {result.get('message', 'Valor inválido')}"

                elif intent == "finance_report":
                    result = monthly_report_uc.execute(chat_id)
                    response_text = result.get("formatted", "💸 Nada.")

                elif intent == "analyze_project":
                    # Tenta usar o nome da pasta da resposta da IA
                    folder_name = ai_response.get("folder", "")
                    file_name = ai_response.get("file", "")  # Nome do arquivo específico, se mencionado
                    
                    # Se não tiver nome na resposta, tenta recuperar do contexto salvo
                    if not folder_name:
                        context = db.get_last_folder_context(chat_id)
                        if context:
                            folder_name = context['folder_name']
                            
                            # Se não tem file_name na resposta da IA, tenta extrair do texto do usuário
                            if not file_name:
                                text_lower = text.lower()
                                context_files = context.get('files', [])
                                for file_info in context_files:
                                    file_display_name = file_info.get('name', '')
                                    if file_display_name.lower() in text_lower:
                                        file_name = file_display_name
                                        break
                    
                    if folder_name:
                        if file_name:
                            send_telegram_message(chat_id, f"📄 Analisando arquivo '{file_name}'...")
                        else:
                            send_telegram_message(chat_id, f"📂 Analisando pasta '{folder_name}'...")
                        
                        result = analyze_file_uc.execute(folder_name, file_name if file_name else None)
                        
                        if result["status"] == "ok":
                            response_text = result.get("summary", "Erro ao analisar.")
                        elif result["status"] == "not_found":
                            response_text = f"❌ Não encontrei a pasta '{folder_name}'. Use /pasta <nome> para listar."
                        else:
                            response_text = result.get("summary", "Erro ao analisar.")
                    else:
                        # Se não tem contexto e não tem nome na resposta, pergunta
                        response_text = "📂 Qual pasta você quer analisar? Use /pasta <nome> para listar primeiro."
            
            # Envia resposta
            if response_text:
                send_telegram_message(chat_id, response_text)
                if intent not in ["consultar_agenda", "list_tasks", "analyze_project"]:
                    db.save_message(chat_id, "model", response_text)
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"ERRO CRÍTICO NO WEBHOOK: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}