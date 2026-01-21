"""
Jarvis - Chatbot Pessoal Inteligente
FastAPI + Gemini 2.0 Flash + Google Services + Telegram
Versão: Production-Ready para Vercel Serverless
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path
import re

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from dotenv import load_dotenv

# Google Services
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import firestore
import google.generativeai as genai

# Telegram
import httpx

# ============================================================================
# CONFIGURAÇÃO INICIAL
# ============================================================================

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Jarvis AI Assistant")

# Variáveis de Ambiente
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")

# ============================================================================
# CLASSE BASE: AUTENTICAÇÃO GOOGLE (Service Account)
# ============================================================================

class GoogleServiceBase:
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
        """Retorna credenciais da Service Account (local ou env)"""
        if cls._credentials:
            return cls._credentials

        try:
            # Tenta carregar de variável de ambiente primeiro
            if FIREBASE_CREDENTIALS:
                creds_dict = json.loads(FIREBASE_CREDENTIALS)
                cls._credentials = service_account.Credentials.from_service_account_info(
                    creds_dict, scopes=cls.SCOPES
                )
                logger.info("✅ Credenciais carregadas via FIREBASE_CREDENTIALS")
            else:
                # Fallback para arquivo local
                key_path = Path("firebase-key.json")
                if not key_path.exists():
                    raise FileNotFoundError("firebase-key.json não encontrado")

                cls._credentials = service_account.Credentials.from_service_account_file(
                    str(key_path), scopes=cls.SCOPES
                )
                logger.info("✅ Credenciais carregadas via firebase-key.json")

            return cls._credentials

        except Exception as e:
            logger.error(f"❌ Erro ao carregar credenciais: {e}")
            raise

    @classmethod
    def get_firestore(cls):
        """Retorna cliente Firestore singleton"""
        if cls._firestore_client:
            return cls._firestore_client

        creds = cls.get_credentials()
        cls._firestore_client = firestore.Client(credentials=creds)
        return cls._firestore_client


# ============================================================================
# SERVIÇO: FIRESTORE (Banco de Dados)
# ============================================================================

class FirestoreService:
    """Gerencia todas as operações no Firestore"""

    def __init__(self):
        self.db = GoogleServiceBase.get_firestore()

    def is_message_processed(self, chat_id: str, message_id: int) -> bool:
        """VACINA ANTI-LOOP: Verifica se mensagem já foi processada"""
        doc_ref = self.db.collection('chats').document(chat_id).collection('processed_ids').document(str(message_id))
        return doc_ref.get().exists

    def mark_message_processed(self, chat_id: str, message_id: int):
        """Marca mensagem como processada"""
        doc_ref = self.db.collection('chats').document(chat_id).collection('processed_ids').document(str(message_id))
        doc_ref.set({'timestamp': firestore.SERVER_TIMESTAMP})

    def save_message(self, chat_id: str, role: str, content: str):
        """Salva mensagem no histórico"""
        self.db.collection('chats').document(chat_id).collection('mensagens').add({
            'role': role,
            'content': content,
            'timestamp': firestore.SERVER_TIMESTAMP
        })

    def get_history(self, chat_id: str, limit: int = 20) -> List[Dict]:
        """Recupera histórico de mensagens"""
        messages = self.db.collection('chats').document(chat_id).collection('mensagens')\
            .order_by('timestamp', direction=firestore.Query.DESCENDING)\
            .limit(limit)\
            .stream()

        history = []
        for msg in messages:
            data = msg.to_dict()
            history.append({'role': data['role'], 'parts': [data['content']]})

        return list(reversed(history))

    def reset_history(self, chat_id: str):
        """Apaga últimas 50 mensagens (comando /reset)"""
        messages = self.db.collection('chats').document(chat_id).collection('mensagens')\
            .order_by('timestamp', direction=firestore.Query.DESCENDING)\
            .limit(50)\
            .stream()

        batch = self.db.batch()
        count = 0
        for msg in messages:
            batch.delete(msg.reference)
            count += 1

        batch.commit()
        logger.info(f"🗑️ Reset: {count} mensagens apagadas para chat {chat_id}")

    def add_task(self, chat_id: str, task: str):
        """Adiciona tarefa"""
        self.db.collection('chats').document(chat_id).collection('tasks').add({
            'task': task,
            'completed': False,
            'created_at': firestore.SERVER_TIMESTAMP
        })

    def get_tasks(self, chat_id: str) -> List[Dict]:
        """Lista tarefas pendentes"""
        tasks = self.db.collection('chats').document(chat_id).collection('tasks')\
            .where('completed', '==', False)\
            .order_by('created_at')\
            .stream()

        return [{'id': t.id, **t.to_dict()} for t in tasks]

    def complete_task(self, chat_id: str, task_id: str):
        """Marca tarefa como concluída"""
        self.db.collection('chats').document(chat_id).collection('tasks').document(task_id).update({
            'completed': True,
            'completed_at': firestore.SERVER_TIMESTAMP
        })

    def add_expense(self, chat_id: str, amount: float, category: str, item: str):
        """Adiciona gasto (com correção de moeda)"""
        self.db.collection('chats').document(chat_id).collection('expenses').add({
            'amount': amount,
            'category': category,
            'item': item,
            'date': firestore.SERVER_TIMESTAMP
        })

    def get_monthly_expenses(self, chat_id: str) -> Dict:
        """Relatório financeiro do mês atual"""
        now = datetime.now()
        start_of_month = datetime(now.year, now.month, 1)

        expenses = self.db.collection('chats').document(chat_id).collection('expenses')\
            .where('date', '>=', start_of_month)\
            .stream()

        total = 0.0
        by_category = {}

        for exp in expenses:
            data = exp.to_dict()
            amount = data.get('amount', 0)
            category = data.get('category', 'Outros')

            total += amount
            by_category[category] = by_category.get(category, 0) + amount

        return {'total': total, 'by_category': by_category}

    def get_all_users(self) -> List[str]:
        """Retorna lista de todos os chat_ids (para cron)"""
        chats = self.db.collection('chats').stream()
        return [chat.id for chat in chats]


# ============================================================================
# SERVIÇO: GOOGLE CALENDAR
# ============================================================================

class CalendarService:
    """Gerencia operações no Google Calendar"""

    def __init__(self):
        creds = GoogleServiceBase.get_credentials()
        self.service = build('calendar', 'v3', credentials=creds)
        self.calendar_id = GOOGLE_CALENDAR_ID

    def list_events(self, days_ahead: int = 7) -> List[Dict]:
        """Lista eventos dos próximos N dias"""
        try:
            now = datetime.utcnow().isoformat() + 'Z'
            end = (datetime.utcnow() + timedelta(days=days_ahead)).isoformat() + 'Z'

            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=now,
                timeMax=end,
                maxResults=10,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])

            formatted = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                formatted.append({
                    'summary': event.get('summary', 'Sem título'),
                    'start': start
                })

            return formatted

        except Exception as e:
            logger.error(f"❌ Erro ao listar eventos: {e}")
            return []

    def create_event(self, summary: str, start_time: str, duration_hours: int = 1) -> bool:
        """Cria evento no calendário"""
        try:
            start_dt = datetime.fromisoformat(start_time)
            end_dt = start_dt + timedelta(hours=duration_hours)

            event = {
                'summary': summary,
                'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'America/Sao_Paulo'},
                'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'America/Sao_Paulo'}
            }

            self.service.events().insert(calendarId=self.calendar_id, body=event).execute()
            logger.info(f"✅ Evento criado: {summary}")
            return True

        except Exception as e:
            logger.error(f"❌ Erro ao criar evento: {e}")
            return False


# ============================================================================
# SERVIÇO: GOOGLE DRIVE
# ============================================================================

class DriveService:
    """Gerencia leitura de arquivos no Google Drive"""

    def __init__(self):
        creds = GoogleServiceBase.get_credentials()
        self.service = build('drive', 'v3', credentials=creds)

    def find_folder(self, folder_name: str) -> Optional[str]:
        """Encontra ID da pasta pelo nome"""
        try:
            results = self.service.files().list(
                q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'",
                fields="files(id, name)"
            ).execute()

            items = results.get('files', [])
            return items[0]['id'] if items else None

        except Exception as e:
            logger.error(f"❌ Erro ao buscar pasta: {e}")
            return None

    def list_files(self, folder_id: str) -> List[Dict]:
        """Lista arquivos de uma pasta"""
        try:
            results = self.service.files().list(
                q=f"'{folder_id}' in parents",
                fields="files(id, name, mimeType)",
                pageSize=10
            ).execute()

            return results.get('files', [])

        except Exception as e:
            logger.error(f"❌ Erro ao listar arquivos: {e}")
            return []

    def read_file_content(self, file_id: str, max_chars: int = 3000) -> str:
        """Lê conteúdo de arquivo (limitado para evitar timeout)"""
        try:
            request = self.service.files().get_media(fileId=file_id)
            content = request.execute()

            text = content.decode('utf-8', errors='ignore')
            return text[:max_chars]

        except Exception as e:
            logger.error(f"❌ Erro ao ler arquivo: {e}")
            return ""


# ============================================================================
# SERVIÇO: GEMINI (Cérebro da IA)
# ============================================================================

class GeminiService:
    """Gerencia interações com o Gemini 2.0 Flash"""

    SYSTEM_PROMPT = """Você é Jarvis, um assistente pessoal inteligente.

REGRAS CRÍTICAS:
1. NUNCA repita o texto do usuário de volta.
2. SEMPRE retorne JSON estruturado com "intent" e "data".
3. Seja direto e objetivo.

INTENTS DISPONÍVEIS:
- "agenda_list": Listar eventos
- "agenda_create": Criar evento (data ISO, summary)
- "task_add": Adicionar tarefa
- "task_list": Listar tarefas
- "task_complete": Concluir tarefa (task_id)
- "expense_add": Adicionar gasto (amount, category, item)
- "expense_report": Relatório mensal
- "drive_list": Listar arquivos (folder_name)
- "drive_read": Ler arquivo (folder_name)
- "chat": Conversa casual

FORMATO DE RESPOSTA:
{
  "intent": "nome_da_intent",
  "data": {...},
  "message": "Resposta amigável para o usuário"
}

Exemplo:
Usuário: "Adiciona reunião amanhã às 14h"
Você: {"intent": "agenda_create", "data": {"summary": "Reunião", "start_time": "2026-01-22T14:00:00"}, "message": "Reunião agendada para amanhã às 14h!"}
"""

    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')

    def chat(self, user_message: str, history: List[Dict]) -> str:
        """Envia mensagem para o Gemini com histórico"""
        try:
            # Adiciona system prompt no início do histórico
            full_history = [{'role': 'user', 'parts': [self.SYSTEM_PROMPT]}] + history

            chat = self.model.start_chat(history=full_history)
            response = chat.send_message(user_message)

            # TRAVA DE SEGURANÇA: Anti-Papagaio
            if response.text.strip() == user_message.strip():
                logger.warning("⚠️ IA tentou repetir mensagem do usuário. Forçando resposta padrão.")
                return json.dumps({
                    "intent": "chat",
                    "message": "Entendi, como posso ajudar?"
                })

            return response.text

        except Exception as e:
            logger.error(f"❌ Erro no Gemini: {e}")
            return json.dumps({"intent": "error", "message": "Desculpe, tive um problema técnico."})

    def transcribe_audio(self, audio_bytes: bytes) -> str:
        """Transcreve áudio usando Gemini (multimodal)"""
        try:
            # Upload do arquivo de áudio
            audio_file = genai.upload_file(audio_bytes, mime_type="audio/ogg")

            response = self.model.generate_content([
                "Transcreva este áudio em português:",
                audio_file
            ])

            return response.text

        except Exception as e:
            logger.error(f"❌ Erro ao transcrever áudio: {e}")
            return ""

    def analyze_document(self, content: str, question: str) -> str:
        """Analisa documento e responde pergunta"""
        try:
            prompt = f"""Analise o seguinte documento e responda a pergunta:

DOCUMENTO:
{content}

PERGUNTA: {question}

Seja conciso e objetivo."""

            response = self.model.generate_content(prompt)
            return response.text

        except Exception as e:
            logger.error(f"❌ Erro ao analisar documento: {e}")
            return "Não consegui analisar o documento."


# ============================================================================
# SERVIÇO: TELEGRAM
# ============================================================================

class TelegramService:
    """Gerencia envio de mensagens via Telegram"""

    def __init__(self):
        self.token = TELEGRAM_TOKEN
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    async def send_message(self, chat_id: str, text: str):
        """Envia mensagem de texto"""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
                )
        except Exception as e:
            logger.error(f"❌ Erro ao enviar mensagem: {e}")

    async def download_file(self, file_id: str) -> bytes:
        """Baixa arquivo do Telegram"""
        try:
            async with httpx.AsyncClient() as client:
                # Obter file_path
                file_info = await client.get(f"{self.base_url}/getFile?file_id={file_id}")
                file_path = file_info.json()['result']['file_path']

                # Baixar arquivo
                file_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
                response = await client.get(file_url)
                return response.content

        except Exception as e:
            logger.error(f"❌ Erro ao baixar arquivo: {e}")
            return b""


# ============================================================================
# ORQUESTRADOR PRINCIPAL
# ============================================================================

class JarvisOrchestrator:
    """Orquestra todas as funcionalidades do Jarvis"""

    def __init__(self):
        self.db = FirestoreService()
        self.calendar = CalendarService()
        self.drive = DriveService()
        self.gemini = GeminiService()
        self.telegram = TelegramService()

    def parse_amount(self, amount_str: str) -> float:
        """Converte string com vírgula para float (ex: '45,50' -> 45.50)"""
        try:
            # Remove espaços e substitui vírgula por ponto
            cleaned = amount_str.strip().replace(',', '.')
            return float(cleaned)
        except:
            return 0.0

    async def process_message(self, chat_id: str, message_id: int, user_text: str, voice_file_id: Optional[str] = None):
        """Processa mensagem do usuário (texto ou voz)"""

        # VACINA ANTI-LOOP: Verifica se já processou esta mensagem
        if self.db.is_message_processed(chat_id, message_id):
            logger.info(f"⏭️ Mensagem {message_id} já processada. Ignorando.")
            return {"status": "ignored"}

        # Marca como processada IMEDIATAMENTE
        self.db.mark_message_processed(chat_id, message_id)

        # Se for áudio, transcreve primeiro
        if voice_file_id:
            audio_bytes = await self.telegram.download_file(voice_file_id)
            user_text = self.gemini.transcribe_audio(audio_bytes)
            if not user_text:
                await self.telegram.send_message(chat_id, "Não consegui entender o áudio.")
                return {"status": "error"}

        # Comando especial: /reset
        if user_text.strip().lower() == '/reset':
            self.db.reset_history(chat_id)
            await self.telegram.send_message(chat_id, "🔄 Histórico resetado! Vamos começar do zero.")
            return {"status": "reset"}

        # Salva mensagem do usuário
        self.db.save_message(chat_id, 'user', user_text)

        # Recupera histórico
        history = self.db.get_history(chat_id)

        # Envia para o Gemini
        ai_response = self.gemini.chat(user_text, history)

        # Parse da resposta JSON
        try:
            response_data = json.loads(ai_response)
            intent = response_data.get('intent', 'chat')
            data = response_data.get('data', {})
            message = response_data.get('message', 'Entendi!')
        except:
            # Se não for JSON válido, trata como conversa casual
            intent = 'chat'
            message = ai_response
            data = {}

        # Executa ação baseada na intent
        final_message = await self._execute_intent(chat_id, intent, data, message)

        # Salva resposta da IA
        self.db.save_message(chat_id, 'model', final_message)

        # Envia resposta ao usuário
        await self.telegram.send_message(chat_id, final_message)

        return {"status": "success", "intent": intent}

    async def _execute_intent(self, chat_id: str, intent: str, data: Dict, base_message: str) -> str:
        """Executa ação específica baseada na intent"""

        try:
            if intent == 'agenda_list':
                events = self.calendar.list_events(days_ahead=data.get('days', 7))
                if not events:
                    return "📅 Nenhum evento agendado nos próximos dias."

                event_list = "\n".join([f"• {e['summary']} - {e['start']}" for e in events])
                return f"📅 *Próximos Eventos:*\n{event_list}"

            elif intent == 'agenda_create':
                summary = data.get('summary', 'Evento')
                start_time = data.get('start_time')

                if self.calendar.create_event(summary, start_time):
                    return f"✅ {base_message}"
                return "❌ Não consegui criar o evento."

            elif intent == 'task_add':
                task = data.get('task', '')
                self.db.add_task(chat_id, task)
                return f"✅ Tarefa adicionada: *{task}*"

            elif intent == 'task_list':
                tasks = self.db.get_tasks(chat_id)
                if not tasks:
                    return "📝 Nenhuma tarefa pendente!"

                task_list = "\n".join([f"• {t['task']}" for t in tasks])
                return f"📝 *Tarefas Pendentes:*\n{task_list}"

            elif intent == 'task_complete':
                task_id = data.get('task_id')
                self.db.complete_task(chat_id, task_id)
                return "✅ Tarefa concluída!"

            elif intent == 'expense_add':
                # CORREÇÃO DE MOEDA
                amount_str = str(data.get('amount', '0'))
                amount = self.parse_amount(amount_str)
                category = data.get('category', 'Outros')
                item = data.get('item', 'Item')

                self.db.add_expense(chat_id, amount, category, item)
                return f"💰 Gasto registrado: R$ {amount:.2f} em *{category}* ({item})"

            elif intent == 'expense_report':
                report = self.db.get_monthly_expenses(chat_id)
                total = report['total']

                if total == 0:
                    return "💰 Nenhum gasto registrado este mês."

                category_breakdown = "\n".join([f"• {cat}: R$ {val:.2f}" for cat, val in report['by_category'].items()])
                return f"💰 *Relatório Mensal:*\n{category_breakdown}\n\n*Total: R$ {total:.2f}*"

            elif intent == 'drive_list':
                folder_name = data.get('folder_name', '')
                folder_id = self.drive.find_folder(folder_name)

                if not folder_id:
                    return f"❌ Pasta '{folder_name}' não encontrada."

                files = self.drive.list_files(folder_id)
                if not files:
                    return f"📂 Pasta '{folder_name}' está vazia."

                file_list = "\n".join([f"• {f['name']}" for f in files])
                return f"📂 *Arquivos em '{folder_name}':*\n{file_list}"

            elif intent == 'drive_read':
                folder_name = data.get('folder_name', '')
                folder_id = self.drive.find_folder(folder_name)

                if not folder_id:
                    return f"❌ Pasta '{folder_name}' não encontrada."

                files = self.drive.list_files(folder_id)
                if not files:
                    return f"📂 Nenhum arquivo para analisar."

                # Lê primeiro arquivo (limitado a 3000 chars)
                first_file = files[0]
                content = self.drive.read_file_content(first_file['id'])

                # Envia para Gemini analisar
                analysis = self.gemini.analyze_document(content, data.get('question', 'Resuma este documento'))
                return f"📄 *Análise de '{first_file['name']}':*\n\n{analysis}"

            else:
                # Conversa casual
                return base_message

        except Exception as e:
            logger.error(f"❌ Erro ao executar intent {intent}: {e}")
            return f"❌ Erro ao processar: {str(e)}"


# ============================================================================
# ROTAS DA API
# ============================================================================

orchestrator = JarvisOrchestrator()

@app.get("/")
async def root():
    """Health check"""
    return {"status": "online", "service": "Jarvis AI Assistant"}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Recebe mensagens do Telegram via Webhook"""
    try:
        data = await request.json()

        # Extrai informações da mensagem
        message = data.get('message', {})
        chat_id = str(message.get('chat', {}).get('id', ''))
        message_id = message.get('message_id', 0)

        # Texto ou voz
        user_text = message.get('text', '')
        voice = message.get('voice')
        voice_file_id = voice.get('file_id') if voice else None

        if not chat_id:
            return JSONResponse({"status": "ignored"})

        # Processa mensagem
        result = await orchestrator.process_message(chat_id, message_id, user_text, voice_file_id)

        return JSONResponse(result)

    except Exception as e:
        logger.error(f"❌ Erro no webhook: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/cron/bom-dia")
async def cron_bom_dia():
    """Rotina matinal: Envia resumo do dia para todos os usuários"""
    try:
        users = orchestrator.db.get_all_users()

        for chat_id in users:
            # Busca eventos do dia
            events = orchestrator.calendar.list_events(days_ahead=1)
            event_text = "\n".join([f"• {e['summary']}" for e in events]) if events else "Nenhum evento agendado."

            # Busca tarefas pendentes
            tasks = orchestrator.db.get_tasks(chat_id)
            task_text = "\n".join([f"• {t['task']}" for t in tasks[:5]]) if tasks else "Nenhuma tarefa pendente."

            # Gera mensagem motivacional com Gemini
            prompt = f"""Gere uma mensagem de bom dia motivacional (máximo 3 linhas) incluindo:

Eventos de hoje:
{event_text}

Tarefas pendentes:
{task_text}

Seja positivo e energizante!"""

            morning_message = orchestrator.gemini.model.generate_content(prompt).text

            # Envia mensagem
            await orchestrator.telegram.send_message(chat_id, f"☀️ *Bom dia!*\n\n{morning_message}")

        return {"status": "success", "users_notified": len(users)}

    except Exception as e:
        logger.error(f"❌ Erro no cron bom-dia: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Dashboard web com histórico financeiro"""
    try:
        users = orchestrator.db.get_all_users()

        html_rows = ""
        for chat_id in users:
            report = orchestrator.db.get_monthly_expenses(chat_id)
            total = report['total']

            categories = ", ".join([f"{cat}: R$ {val:.2f}" for cat, val in report['by_category'].items()])

            html_rows += f"""
            <tr>
                <td>{chat_id}</td>
                <td>R$ {total:.2f}</td>
                <td>{categories or 'Nenhum gasto'}</td>
            </tr>
            """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Jarvis Dashboard</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 40px;
                    background: #f5f5f5;
                }}
                h1 {{
                    color: #333;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    background: white;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
                th, td {{
                    padding: 12px;
                    text-align: left;
                    border-bottom: 1px solid #ddd;
                }}
                th {{
                    background: #4CAF50;
                    color: white;
                }}
                tr:hover {{
                    background: #f1f1f1;
                }}
            </style>
        </head>
        <body>
            <h1>💰 Jarvis - Dashboard Financeiro</h1>
            <table>
                <thead>
                    <tr>
                        <th>Usuário (Chat ID)</th>
                        <th>Total Mensal</th>
                        <th>Categorias</th>
                    </tr>
                </thead>
                <tbody>
                    {html_rows}
                </tbody>
            </table>
        </body>
        </html>
        """

        return HTMLResponse(content=html)

    except Exception as e:
        logger.error(f"❌ Erro no dashboard: {e}")
        return HTMLResponse(content=f"<h1>Erro: {str(e)}</h1>", status_code=500)


# ============================================================================
# INICIALIZAÇÃO (Para Vercel)
# ============================================================================

# Vercel espera uma variável chamada "app" ou "handler"
handler = app
