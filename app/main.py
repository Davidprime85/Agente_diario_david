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

app = FastAPI(title="Jarvis AI Assistant", version="11.1.0 (Fix ID Type)")

# Variáveis de Ambiente
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")

# ============================================================================
# CLASSE BASE: AUTENTICAÇÃO GOOGLE
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
        if cls._credentials: return cls._credentials
        try:
            if FIREBASE_CREDENTIALS:
                creds_dict = json.loads(FIREBASE_CREDENTIALS)
                cls._credentials = service_account.Credentials.from_service_account_info(creds_dict, scopes=cls.SCOPES)
            elif os.path.exists("firebase-key.json"):
                cls._credentials = service_account.Credentials.from_service_account_file("firebase-key.json", scopes=cls.SCOPES)
            return cls._credentials
        except Exception as e:
            logger.error(f"❌ Erro Auth: {e}")
            return None

    @classmethod
    def get_firestore(cls):
        if cls._firestore_client: return cls._firestore_client
        creds = cls.get_credentials()
        if creds: cls._firestore_client = firestore.Client(credentials=creds)
        return cls._firestore_client

# ============================================================================
# SERVIÇOS ESPECÍFICOS
# ============================================================================

class FirestoreService:
    def __init__(self):
        self.db = GoogleServiceBase.get_firestore()

    def is_message_processed(self, chat_id: str, message_id: int) -> bool:
        if not self.db: return False
        # FIX: Garante que chat_id seja string
        doc_ref = self.db.collection('chats').document(str(chat_id)).collection('processed_ids').document(str(message_id))
        if doc_ref.get().exists: return True
        doc_ref.set({'timestamp': datetime.now()})
        return False

    def save_message(self, chat_id: str, role: str, content: str):
        if not self.db: return
        cid = str(chat_id)
        self.db.collection('chats').document(cid).set({"last_active": datetime.now()}, merge=True)
        self.db.collection('chats').document(cid).collection('mensagens').add({
            'role': role, 'content': content, 'timestamp': datetime.now()
        })

    def get_history(self, chat_id: str, limit: int = 5) -> str:
        if not self.db: return ""
        docs = self.db.collection('chats').document(str(chat_id)).collection('mensagens').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit).stream()
        return "\n".join(reversed([f"{d.to_dict()['role']}: {d.to_dict()['content']}" for d in docs]))

    def reset_history(self, chat_id: str):
        if not self.db: return
        msgs = self.db.collection('chats').document(str(chat_id)).collection('mensagens').limit(50).stream()
        for m in msgs: m.reference.delete()

    # --- TAREFAS ---
    def add_task(self, chat_id: str, item: str):
        self.db.collection('chats').document(str(chat_id)).collection('tasks').add({'item': item, 'status': 'pendente', 'created_at': datetime.now()})
    
    def list_tasks(self, chat_id: str) -> str:
        docs = self.db.collection('chats').document(str(chat_id)).collection('tasks').where(filter=firestore.FieldFilter('status', '==', 'pendente')).stream()
        tasks = [d.to_dict()['item'] for d in docs]
        return "\n".join([f"• {t}" for t in tasks]) if tasks else "✅ Nenhuma tarefa pendente."

    def complete_task(self, chat_id: str, item: str) -> bool:
        docs = self.db.collection('chats').document(str(chat_id)).collection('tasks').where(filter=firestore.FieldFilter('status', '==', 'pendente')).stream()
        for d in docs:
            if item.lower() in d.to_dict()['item'].lower():
                d.reference.update({'status': 'concluido'})
                return True
        return False

    # --- FINANCEIRO ---
    def add_expense(self, chat_id: str, amount: float, category: str, item: str):
        self.db.collection('chats').document(str(chat_id)).collection('expenses').add({
            'amount': amount, 'category': category, 'item': item, 'timestamp': datetime.now()
        })

    def get_report(self, chat_id: str):
        now = datetime.now(); start = datetime(now.year, now.month, 1)
        docs = self.db.collection('chats').document(str(chat_id)).collection('expenses').where(filter=firestore.FieldFilter('timestamp', '>=', start)).stream()
        total = 0; txt = ""
        for d in docs:
            data = d.to_dict(); total += data['amount']
            txt += f"• R$ {data['amount']:.2f} ({data.get('category')}) - {data.get('item')}\n".replace('.', ',')
        return f"📊 **Gastos de {now.strftime('%B')}:**\n\n{txt}\n💰 **TOTAL: R$ {total:.2f}**".replace('.', ',') if txt else "💸 Sem gastos."

class CalendarService:
    def __init__(self):
        creds = GoogleServiceBase.get_credentials()
        self.service = build('calendar', 'v3', credentials=creds) if creds else None
        self.calendar_id = GOOGLE_CALENDAR_ID

    def execute(self, action, data):
        if not self.service: return None
        if action == "create":
            body = {'summary': data['title'], 'description': data.get('description',''), 'start':{'dateTime':data['start_iso']}, 'end':{'dateTime':data['end_iso']}}
            self.service.events().insert(calendarId=self.calendar_id, body=body).execute()
            return True
        if action == "list":
            tmin = data['time_min'] if data['time_min'].endswith('Z') else data['time_min'] + '-03:00'
            tmax = data['time_max'] if data['time_max'].endswith('Z') else data['time_max'] + '-03:00'
            return self.service.events().list(calendarId=self.calendar_id, timeMin=tmin, timeMax=tmax, singleEvents=True, orderBy='startTime').execute().get('items', [])

class DriveService:
    def __init__(self):
        creds = GoogleServiceBase.get_credentials()
        self.service = build('drive', 'v3', credentials=creds) if creds else None

    def list_files(self, folder_name):
        if not self.service: return []
        # Acha a pasta
        res = self.service.files().list(q=f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false", fields="files(id)").execute()
        folders = res.get('files', [])
        if not folders: return None
        # Lista arquivos
        fid = folders[0]['id']
        return self.service.files().list(q=f"'{fid}' in parents", fields="files(id, name, mimeType)").execute().get('files', [])

    def read_content(self, file_id, mime_type):
        try:
            if "google-apps.document" in mime_type:
                req = self.service.files().export_media(fileId=file_id, mimeType='text/plain')
            else:
                req = self.service.files().get_media(fileId=file_id)
            
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, req)
            done = False
            while not done: _, done = downloader.next_chunk()
            # Decodifica seguro para evitar crash com PDF/Imagem
            return fh.getvalue().decode('utf-8', errors='ignore')[:3000]
        except Exception as e:
            return f"[Erro leitura: {str(e)}]"

class GeminiService:
    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-2.0-flash')

    def chat(self, text, history_str, is_audio=False):
        now = datetime.now()
        user_p = "[Audio Enviado]" if is_audio else text
        
        system_prompt = f"""
        SYSTEM: Você é o Jarvis. Data: {now.strftime('%d/%m/%Y %H:%M')}.
        
        REGRAS:
        1. NÃO REPITA o que o usuário disse. Responda a pergunta ou execute a ação.
        2. Retorne APENAS JSON.
        
        INTENTS:
        - agenda_create, agenda_list
        - task_add, task_list, task_complete
        - expense_add (amount, category, item), expense_report
        - drive_read (folder_name)
        - chat (conversa casual)
        
        HISTÓRICO:
        {history_str}
        
        USUÁRIO: "{user_p}"
        """
        try:
            content = [text, system_prompt] if is_audio else system_prompt
            resp = self.model.generate_content(content, generation_config={"response_mime_type": "application/json"})
            data = json.loads(resp.text)
            
            # --- TRAVA ANTI-PAPAGAIO (PYTHON) ---
            if data.get("intent") == "chat":
                ai_msg = data.get("response", "").strip().lower()
                if ai_msg == text.strip().lower() or not ai_msg:
                    data["response"] = "Entendi. Como posso ajudar com seus projetos hoje?"
            # ------------------------------------
            return data
        except:
            return {"intent": "chat", "response": "Erro interno na IA."}

    def generate_morning_msg(self, agenda_txt, tasks_txt):
        return self.model.generate_content(f"Crie um Bom Dia motivacional curto. Agenda: {agenda_txt}. Tarefas: {tasks_txt}").text

class TelegramService:
    def send(self, chat_id, text):
        if TELEGRAM_TOKEN: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})

    def download_voice(self, file_id):
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}")
        path = r.json().get("result", {}).get("file_path")
        if not path: return None
        content = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}").content
        with open("/tmp/voice.ogg", "wb") as f: f.write(content)
        return "/tmp/voice.ogg"

# ============================================================================
# ORQUESTRADOR E ROTAS
# ============================================================================

db = FirestoreService()
cal = CalendarService()
drv = DriveService()
ai = GeminiService()
tg = TelegramService()

@app.get("/")
def root(): return {"status": "Jarvis V11.1 Online 🟢"}

@app.post("/telegram/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        if "message" not in data: return "ok"
        
        msg = data["message"]
        chat_id = str(msg["chat"]["id"]) # CORREÇÃO CRÍTICA: Garante que é String
        msg_id = msg["message_id"]
        text = msg.get("text", "")

        # 1. RESET
        if text == "/reset":
            db.reset_history(chat_id)
            tg.send(chat_id, "🧠 Memória limpa!")
            return {"status": "reset"}

        # 2. ANTI-LOOP
        if db.is_message_processed(chat_id, msg_id): return {"status": "ignored"}

        # 3. PROCESSAMENTO
        ai_resp = None
        if "text" in msg:
            db.save_message(chat_id, "user", text)
            hist = db.get_history(chat_id)
            ai_resp = ai.chat(text, hist)
        elif "voice" in msg:
            db.save_message(chat_id, "user", "[Audio]")
            path = tg.download_voice(msg["voice"]["file_id"])
            if path:
                tg.send(chat_id, "🎧 Ouvindo...")
                myfile = genai.upload_file(path, mime_type="audio/ogg")
                hist = db.get_history(chat_id)
                ai_resp = ai.chat(myfile, hist, is_audio=True)

        # 4. AÇÃO
        if ai_resp:
            intent = ai_resp.get("intent")
            resp = ""
            
            if intent == "chat": resp = ai_resp.get("response", "")
            elif intent == "agenda_create": resp = "✅ Agendado." if cal.execute("create", ai_resp) else "❌ Erro."
            elif intent == "agenda_list": 
                evs = cal.execute("list", ai_resp)
                resp = "📅 " + "\n".join([f"{e['start'].get('dateTime')[11:16]} {e['summary']}" for e in evs]) if evs else "📅 Agenda vazia."
            elif intent == "task_add": db.add_task(chat_id, ai_resp["item"]); resp = f"📝 Add: {ai_resp['item']}"
            elif intent == "task_list": resp = db.list_tasks(chat_id)
            elif intent == "task_complete": resp = "✅ Feito." if db.complete_task(chat_id, ai_resp["item"]) else "🔍 Não achei."
            
            elif intent == "expense_add":
                # Correção de vírgula para ponto
                try:
                    val = float(str(ai_resp["amount"]).replace(',', '.'))
                    db.add_expense(chat_id, val, ai_resp["category"], ai_resp["item"])
                    resp = f"💸 Gasto: R$ {val:.2f}".replace('.', ',')
                except: resp = "❌ Valor inválido."
                
            elif intent == "expense_report": resp = db.get_report(chat_id)
            
            elif intent == "drive_read":
                tg.send(chat_id, f"📂 Lendo: {ai_resp['folder_name']}...")
                files = drv.list_files(ai_resp['folder_name'])
                if not files: resp = "Pasta vazia."
                else:
                    # Lê conteúdo do primeiro arquivo
                    content = drv.read_content(files[0]['id'], files[0]['mimeType'])
                    resp = ai.model.generate_content(f"Analise: {content}").text

            if resp:
                tg.send(chat_id, resp)
                if intent == "chat": db.save_message(chat_id, "model", resp)

        return {"status": "ok"}
    except Exception as e:
        print(f"ERRO CRÍTICO NO WEBHOOK: {e}")
        return {"status": "error"}

@app.get("/cron/bom-dia")
def cron():
    # Simplificado para pegar todos chats ativos
    all_docs = db.db.collection('chats').stream()
    count = 0
    now = datetime.now()
    tmin = now.strftime("%Y-%m-%dT00:00:00")
    tmax = now.strftime("%Y-%m-%dT23:59:59")
    
    for doc in all_docs:
        cid = doc.id
        evs = cal.execute("list", {"time_min": tmin, "time_max": tmax})
        ev_txt = ", ".join([e['summary'] for e in evs]) if evs else "Nada"
        tk_txt = db.list_tasks(cid)
        msg = ai.generate_morning_msg(ev_txt, tk_txt)
        tg.send(cid, msg)
        count += 1
    return {"sent": count}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    all_docs = db.db.collection('chats').stream()
    html = """<html><head><title>Jarvis Dash</title><style>
    body{font-family:sans-serif;padding:20px;background:#f0f2f5}
    .card{background:white;padding:20px;margin:20px 0;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1)}
    table{width:100%;border-collapse:collapse} th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}
    th{background:#007bff;color:white} .total{color:green;font-weight:bold;text-align:right;margin-top:10px}
    </style></head><body><h1>📊 Dashboard Financeiro</h1>"""
    
    for doc in all_docs:
        cid = doc.id
        # Extrai linhas para tabela (simplificado)
        now = datetime.now(); start = datetime(now.year, now.month, 1)
        exps = db.db.collection('chats').document(cid).collection('expenses').where(filter=firestore.FieldFilter("timestamp", ">=", start)).stream()
        rows = ""; tot = 0
        for e in exps:
            d = e.to_dict(); tot += d['amount']
            rows += f"<tr><td>{d['timestamp'].strftime('%d/%m')}</td><td>{d.get('item')}</td><td>R$ {d['amount']:.2f}</td></tr>"
        
        if rows:
            html += f"<div class='card'><h2>User: {cid}</h2><table><tr><th>Data</th><th>Item</th><th>Valor</th></tr>{rows}</table><div class='total'>Total: R$ {tot:.2f}</div></div>"
    
    return html + "</body></html>"