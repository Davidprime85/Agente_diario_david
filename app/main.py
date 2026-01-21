import os
import json
import logging
import requests
import io
from datetime import datetime
from typing import Optional, Dict, List, Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from dotenv import load_dotenv

# Google Services
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.cloud import firestore
import google.generativeai as genai

# ============================================================================
# CONFIGURAÇÃO INICIAL
# ============================================================================

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Jarvis AI Assistant", version="12.0.0 (OOP Refactored)")

# Variáveis de Ambiente
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")

# ============================================================================
# CLASSE BASE: AUTENTICAÇÃO GOOGLE
# ============================================================================

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
    def get_credentials(cls):
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
            logger.error(f"❌ Erro Auth: {e}")
            return None
    
    @classmethod
    def get_firestore_client(cls):
        """Retorna cliente Firestore (singleton)"""
        if cls._firestore_client:
            return cls._firestore_client
        
        creds = cls.get_credentials()
        if creds:
            cls._firestore_client = firestore.Client(credentials=creds)
        return cls._firestore_client

# ============================================================================
# SERVIÇOS ESPECÍFICOS
# ============================================================================

class FirestoreService:
    """Serviço de persistência no Firestore"""
    
    def __init__(self):
        self.db = GoogleAuth.get_firestore_client()
    
    def _ensure_string_id(self, chat_id: Any) -> str:
        """BUG FIX: Garante que chat_id seja sempre string"""
        return str(chat_id)
    
    def is_message_processed(self, chat_id: Any, message_id: int) -> bool:
        """ANTI-LOOP: Verifica se mensagem já foi processada"""
        if not self.db:
            return False
        
        chat_id_str = self._ensure_string_id(chat_id)
        doc_ref = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('processed_ids')
            .document(str(message_id))
        )
        
        if doc_ref.get().exists:
            return True
        
        doc_ref.set({'timestamp': datetime.now()})
        return False
    
    def save_message(self, chat_id: Any, role: str, content: str):
        """Salva mensagem no histórico"""
        if not self.db:
            return
        
        chat_id_str = self._ensure_string_id(chat_id)
        self.db.collection('chats').document(chat_id_str).set(
            {"last_active": datetime.now()}, merge=True
        )
        self.db.collection('chats').document(chat_id_str).collection('mensagens').add({
            'role': role,
            'content': content,
            'timestamp': datetime.now()
        })
    
    def get_history(self, chat_id: Any, limit: int = 5) -> str:
        """Retorna histórico de mensagens"""
        if not self.db:
            return ""
        
        chat_id_str = self._ensure_string_id(chat_id)
        docs = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('mensagens')
            .order_by('timestamp', direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        
        messages = []
        for doc in docs:
            data = doc.to_dict()
            messages.append(f"{data['role']}: {data['content']}")
        
        return "\n".join(reversed(messages))
    
    def reset_history(self, chat_id: Any):
        """Limpa histórico de mensagens"""
        if not self.db:
            return
        
        chat_id_str = self._ensure_string_id(chat_id)
        msgs = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('mensagens')
            .limit(50)
            .stream()
        )
        for msg in msgs:
            msg.reference.delete()
    
    # --- TAREFAS ---
    def add_task(self, chat_id: Any, item: str):
        """Adiciona tarefa"""
        if not self.db:
            return
        
        chat_id_str = self._ensure_string_id(chat_id)
        self.db.collection('chats').document(chat_id_str).collection('tasks').add({
            'item': item,
            'status': 'pendente',
            'created_at': datetime.now()
        })
    
    def list_tasks(self, chat_id: Any) -> str:
        """Lista tarefas pendentes"""
        if not self.db:
            return "✅ Nenhuma tarefa pendente."
        
        chat_id_str = self._ensure_string_id(chat_id)
        docs = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('tasks')
            .where(filter=firestore.FieldFilter('status', '==', 'pendente'))
            .stream()
        )
        
        tasks = [doc.to_dict()['item'] for doc in docs]
        return "\n".join([f"• {t}" for t in tasks]) if tasks else "✅ Nenhuma tarefa pendente."
    
    def complete_task(self, chat_id: Any, item: str) -> bool:
        """Marca tarefa como concluída"""
        if not self.db:
            return False
        
        chat_id_str = self._ensure_string_id(chat_id)
        docs = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('tasks')
            .where(filter=firestore.FieldFilter('status', '==', 'pendente'))
            .stream()
        )
        
        for doc in docs:
            if item.lower() in doc.to_dict()['item'].lower():
                doc.reference.update({'status': 'concluido'})
                return True
        return False
    
    # --- FINANCEIRO ---
    def add_expense(self, chat_id: Any, amount: float, category: str, item: str):
        """Adiciona gasto financeiro"""
        if not self.db:
            return
        
        chat_id_str = self._ensure_string_id(chat_id)
        self.db.collection('chats').document(chat_id_str).collection('expenses').add({
            'amount': amount,
            'category': category,
            'item': item,
            'timestamp': datetime.now()
        })
    
    def get_report(self, chat_id: Any) -> str:
        """Gera relatório mensal de gastos"""
        if not self.db:
            return "💸 Sem gastos."
        
        chat_id_str = self._ensure_string_id(chat_id)
        now = datetime.now()
        start = datetime(now.year, now.month, 1)
        
        docs = (
            self.db.collection('chats')
            .document(chat_id_str)
            .collection('expenses')
            .where(filter=firestore.FieldFilter('timestamp', '>=', start))
            .stream()
        )
        
        total = 0
        txt = ""
        for doc in docs:
            data = doc.to_dict()
            total += data['amount']
            txt += f"• R$ {data['amount']:.2f} ({data.get('category')}) - {data.get('item')}\n"
        
        if txt:
            # Converte ponto para vírgula no formato BR
            txt_br = txt.replace('.', ',')
            total_br = f"{total:.2f}".replace('.', ',')
            return f"📊 **Gastos de {now.strftime('%B')}:**\n\n{txt_br}\n💰 **TOTAL: R$ {total_br}**"
        return "💸 Sem gastos."


class CalendarService:
    """Serviço de integração com Google Calendar"""
    
    def __init__(self):
        creds = GoogleAuth.get_credentials()
        self.service = build('calendar', 'v3', credentials=creds) if creds else None
        self.calendar_id = GOOGLE_CALENDAR_ID
    
    def create_event(self, title: str, description: str, start_iso: str, end_iso: str) -> bool:
        """Cria evento no calendário"""
        if not self.service:
            return False
        
        try:
            body = {
                'summary': title,
                'description': description,
                'start': {'dateTime': start_iso},
                'end': {'dateTime': end_iso}
            }
            self.service.events().insert(calendarId=self.calendar_id, body=body).execute()
            return True
        except Exception as e:
            logger.error(f"Erro ao criar evento: {e}")
            return False
    
    def list_events(self, time_min: str, time_max: str) -> List[Dict]:
        """Lista eventos do calendário"""
        if not self.service:
            return []
        
        try:
            # Ajusta timezone se necessário
            if not time_min.endswith('Z') and '-' not in time_min[-6:]:
                time_min += '-03:00'
            if not time_max.endswith('Z') and '-' not in time_max[-6:]:
                time_max += '-03:00'
            
            result = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy='startTime'
                )
                .execute()
            )
            return result.get('items', [])
        except Exception as e:
            logger.error(f"Erro ao listar eventos: {e}")
            return []


class DriveService:
    """Serviço de integração com Google Drive"""
    
    def __init__(self):
        creds = GoogleAuth.get_credentials()
        self.service = build('drive', 'v3', credentials=creds) if creds else None
    
    def list_files(self, folder_name: str) -> Optional[List[Dict]]:
        """Lista arquivos de uma pasta específica"""
        if not self.service:
            return None
        
        try:
            # Encontra a pasta
            result = (
                self.service.files()
                .list(
                    q=f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false",
                    fields="files(id)"
                )
                .execute()
            )
            
            folders = result.get('files', [])
            if not folders:
                return None
            
            folder_id = folders[0]['id']
            
            # Lista arquivos da pasta
            files_result = (
                self.service.files()
                .list(
                    q=f"'{folder_id}' in parents",
                    fields="files(id, name, mimeType)"
                )
                .execute()
            )
            
            return files_result.get('files', [])
        except Exception as e:
            logger.error(f"Erro ao listar arquivos: {e}")
            return None
    
    def read_content(self, file_id: str, mime_type: str) -> str:
        """Lê conteúdo de um arquivo"""
        if not self.service:
            return "[Erro: Serviço não disponível]"
        
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
            
            # Decodifica com tratamento de erros para evitar crash
            content = file_handle.getvalue().decode('utf-8', errors='ignore')
            return content[:3000]  # Limita tamanho
        except Exception as e:
            return f"[Erro leitura: {str(e)}]"


class GeminiService:
    """Serviço de integração com Google Gemini AI"""
    
    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-2.0-flash')
    
    def chat(self, text: str, history_str: str, is_audio: bool = False) -> Dict[str, Any]:
        """Processa mensagem com IA"""
        now = datetime.now()
        user_prompt = "[Audio Enviado]" if is_audio else text
        
        system_prompt = f"""
        SYSTEM: Você é o Jarvis. Data: {now.strftime('%d/%m/%Y %H:%M')}.
        
        REGRAS:
        1. NÃO REPITA o que o usuário disse. Responda a pergunta ou execute a ação.
        2. Retorne APENAS JSON válido.
        
        INTENTS:
        - agenda_create, agenda_list
        - task_add, task_list, task_complete
        - expense_add (amount, category, item), expense_report
        - drive_read (folder_name)
        - chat (conversa casual)
        
        HISTÓRICO:
        {history_str}
        
        USUÁRIO: "{user_prompt}"
        """
        
        try:
            content = [text, system_prompt] if is_audio else system_prompt
            response = self.model.generate_content(
                content,
                generation_config={"response_mime_type": "application/json"}
            )
            data = json.loads(response.text)
            
            # ANTI-PAPAGAIO: Previne repetição da mensagem do usuário
            if data.get("intent") == "chat":
                ai_response = data.get("response", "").strip().lower()
                user_text_lower = text.strip().lower()
                
                if ai_response == user_text_lower or not ai_response:
                    data["response"] = "Entendi. Como posso ajudar com seus projetos hoje?"
            
            return data
        except Exception as e:
            logger.error(f"Erro na IA: {e}")
            return {"intent": "chat", "response": "Erro interno na IA."}
    
    def generate_morning_msg(self, agenda_txt: str, tasks_txt: str) -> str:
        """Gera mensagem matinal motivacional"""
        try:
            prompt = f"Crie um Bom Dia motivacional curto. Agenda: {agenda_txt}. Tarefas: {tasks_txt}"
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Erro ao gerar mensagem matinal: {e}")
            return "Bom dia! Tenha um ótimo dia hoje! 🌅"


class TelegramService:
    """Serviço de integração com Telegram"""
    
    def __init__(self):
        self.token = TELEGRAM_TOKEN
        self.base_url = f"https://api.telegram.org/bot{self.token}" if self.token else None
    
    def send_message(self, chat_id: Any, text: str) -> bool:
        """Envia mensagem via Telegram"""
        if not self.base_url:
            return False
        
        try:
            # BUG FIX: Garante que chat_id seja string
            chat_id_str = str(chat_id)
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": chat_id_str, "text": text},
                timeout=5
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")
            return False
    
    def download_voice(self, file_id: str) -> Optional[str]:
        """Baixa arquivo de áudio do Telegram"""
        if not self.base_url:
            return None
        
        try:
            # Obtém caminho do arquivo
            response = requests.get(
                f"{self.base_url}/getFile?file_id={file_id}",
                timeout=5
            )
            file_path = response.json().get("result", {}).get("file_path")
            
            if not file_path:
                return None
            
            # Baixa conteúdo
            content = requests.get(
                f"https://api.telegram.org/file/bot{self.token}/{file_path}",
                timeout=10
            ).content
            
            # Salva temporariamente
            temp_path = "/tmp/voice.ogg"
            with open(temp_path, "wb") as f:
                f.write(content)
            
            return temp_path
        except Exception as e:
            logger.error(f"Erro ao baixar áudio: {e}")
            return None

# ============================================================================
# ORQUESTRADOR E ROTAS
# ============================================================================

# Instâncias dos serviços (singletons)
db = FirestoreService()
calendar = CalendarService()
drive = DriveService()
ai = GeminiService()
telegram = TelegramService()


@app.get("/")
def root():
    return {"status": "Jarvis V12.0 OOP Online 🟢"}


@app.post("/telegram/webhook")
async def webhook(request: Request):
    """Endpoint principal do webhook do Telegram"""
    try:
        data = await request.json()
        
        if "message" not in data:
            return {"status": "ok"}
        
        msg = data["message"]
        
        # BUG FIX: Garante que chat_id seja string desde o início
        chat_id = str(msg["chat"]["id"])
        msg_id = msg.get("message_id")
        text = msg.get("text", "")
        
        # 1. RESET
        if text == "/reset":
            db.reset_history(chat_id)
            telegram.send_message(chat_id, "🧠 Memória limpa!")
            return {"status": "reset"}
        
        # 2. ANTI-LOOP: Verifica se mensagem já foi processada
        if msg_id and db.is_message_processed(chat_id, msg_id):
            logger.info(f"Mensagem {msg_id} já processada, ignorando...")
            return {"status": "ignored"}
        
        # 3. PROCESSAMENTO
        ai_response = None
        
        if "text" in msg:
            db.save_message(chat_id, "user", text)
            history = db.get_history(chat_id)
            ai_response = ai.chat(text, history)
        
        elif "voice" in msg:
            db.save_message(chat_id, "user", "[Audio]")
            voice_path = telegram.download_voice(msg["voice"]["file_id"])
            
            if voice_path:
                telegram.send_message(chat_id, "🎧 Ouvindo...")
                audio_file = genai.upload_file(voice_path, mime_type="audio/ogg")
                history = db.get_history(chat_id)
                ai_response = ai.chat(audio_file, history, is_audio=True)
        
        # 4. EXECUÇÃO DE AÇÕES
        if ai_response:
            intent = ai_response.get("intent")
            response_text = ""
            
            if intent == "chat":
                response_text = ai_response.get("response", "")
            
            elif intent == "agenda_create":
                success = calendar.create_event(
                    title=ai_response.get("title", ""),
                    description=ai_response.get("description", ""),
                    start_iso=ai_response.get("start_iso", ""),
                    end_iso=ai_response.get("end_iso", "")
                )
                response_text = "✅ Agendado." if success else "❌ Erro ao agendar."
            
            elif intent == "agenda_list":
                events = calendar.list_events(
                    time_min=ai_response.get("time_min", ""),
                    time_max=ai_response.get("time_max", "")
                )
                if events:
                    event_list = []
                    for event in events:
                        start_time = event['start'].get('dateTime', '')[:16]
                        summary = event.get('summary', 'Sem título')
                        event_list.append(f"{start_time[11:16]} {summary}")
                    response_text = "📅 " + "\n".join(event_list)
                else:
                    response_text = "📅 Agenda vazia."
            
            elif intent == "task_add":
                task_item = ai_response.get("item", "")
                db.add_task(chat_id, task_item)
                response_text = f"📝 Adicionado: {task_item}"
            
            elif intent == "task_list":
                response_text = db.list_tasks(chat_id)
            
            elif intent == "task_complete":
                task_item = ai_response.get("item", "")
                success = db.complete_task(chat_id, task_item)
                response_text = "✅ Tarefa concluída." if success else "🔍 Tarefa não encontrada."
            
            elif intent == "expense_add":
                # BUG FIX: Tratamento de vírgula para ponto (locale BR)
                try:
                    amount_str = str(ai_response.get("amount", "0"))
                    amount_float = float(amount_str.replace(',', '.'))
                    
                    db.add_expense(
                        chat_id=chat_id,
                        amount=amount_float,
                        category=ai_response.get("category", "outros"),
                        item=ai_response.get("item", "")
                    )
                    amount_br = f"{amount_float:.2f}".replace('.', ',')
                    response_text = f"💸 Gasto registrado: R$ {amount_br}"
                except (ValueError, TypeError) as e:
                    logger.error(f"Erro ao processar valor financeiro: {e}")
                    response_text = "❌ Valor inválido. Use formato: 45,50 ou 45.50"
            
            elif intent == "expense_report":
                response_text = db.get_report(chat_id)
            
            elif intent == "drive_read":
                folder_name = ai_response.get("folder_name", "")
                telegram.send_message(chat_id, f"📂 Lendo pasta: {folder_name}...")
                
                files = drive.list_files(folder_name)
                if not files:
                    response_text = "📂 Pasta vazia ou não encontrada."
                else:
                    # Lê conteúdo do primeiro arquivo
                    first_file = files[0]
                    content = drive.read_content(first_file['id'], first_file['mimeType'])
                    
                    # Gera resumo com IA
                    analysis_prompt = f"Analise e resuma o seguinte conteúdo:\n\n{content}"
                    analysis = ai.model.generate_content(analysis_prompt)
                    response_text = analysis.text
            
            # Envia resposta
            if response_text:
                telegram.send_message(chat_id, response_text)
                if intent == "chat":
                    db.save_message(chat_id, "model", response_text)
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"ERRO CRÍTICO NO WEBHOOK: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.get("/cron/bom-dia")
def cron_bom_dia():
    """Cron job para enviar mensagem matinal automática"""
    if not db.db:
        return {"sent": 0, "error": "Firestore não disponível"}
    
    try:
        all_docs = db.db.collection('chats').stream()
        count = 0
        now = datetime.now()
        time_min = now.strftime("%Y-%m-%dT00:00:00")
        time_max = now.strftime("%Y-%m-%dT23:59:59")
        
        for doc in all_docs:
            chat_id = doc.id
            
            # Busca eventos do dia
            events = calendar.list_events(time_min, time_max)
            events_text = ", ".join([e.get('summary', '') for e in events]) if events else "Nada"
            
            # Busca tarefas
            tasks_text = db.list_tasks(chat_id)
            
            # Gera mensagem matinal
            morning_msg = ai.generate_morning_msg(events_text, tasks_text)
            
            # Envia
            if telegram.send_message(chat_id, morning_msg):
                count += 1
        
        return {"sent": count}
    
    except Exception as e:
        logger.error(f"Erro no cron: {e}")
        return {"sent": 0, "error": str(e)}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Dashboard HTML com relatório financeiro"""
    if not db.db:
        return HTMLResponse("<html><body><h1>Erro: Firestore não disponível</h1></body></html>")
    
    html = """<html>
    <head>
        <title>Jarvis Dashboard</title>
        <style>
            body { font-family: sans-serif; padding: 20px; background: #f0f2f5; }
            .card { background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 10px; border-bottom: 1px solid #ddd; text-align: left; }
            th { background: #007bff; color: white; }
            .total { color: green; font-weight: bold; text-align: right; margin-top: 10px; }
        </style>
    </head>
    <body>
        <h1>📊 Dashboard Financeiro</h1>
    """
    
    try:
        all_docs = db.db.collection('chats').stream()
        
        for doc in all_docs:
            chat_id = doc.id
            now = datetime.now()
            start = datetime(now.year, now.month, 1)
            
            expenses = (
                db.db.collection('chats')
                .document(chat_id)
                .collection('expenses')
                .where(filter=firestore.FieldFilter("timestamp", ">=", start))
                .stream()
            )
            
            rows = ""
            total = 0
            
            for expense in expenses:
                data = expense.to_dict()
                total += data.get('amount', 0)
                rows += f"""
                    <tr>
                        <td>{data['timestamp'].strftime('%d/%m')}</td>
                        <td>{data.get('item', 'N/A')}</td>
                        <td>R$ {data.get('amount', 0):.2f}</td>
                    </tr>
                """
            
            if rows:
                html += f"""
                    <div class='card'>
                        <h2>User: {chat_id}</h2>
                        <table>
                            <tr>
                                <th>Data</th>
                                <th>Item</th>
                                <th>Valor</th>
                            </tr>
                            {rows}
                        </table>
                        <div class='total'>Total: R$ {total:.2f}</div>
                    </div>
                """
        
        html += "</body></html>"
        return HTMLResponse(html)
    
    except Exception as e:
        logger.error(f"Erro no dashboard: {e}")
        return HTMLResponse(f"<html><body><h1>Erro: {str(e)}</h1></body></html>")
