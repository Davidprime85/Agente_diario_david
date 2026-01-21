import os
import json
import requests
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import firestore
import google.generativeai as genai
from dotenv import load_dotenv

# --- CONFIGURAÇÕES ---
load_dotenv()
app = FastAPI(title="Agente Jarvis", version="3.0.0 (Finance)")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

# --- CONEXÃO COM BANCO DE DADOS (FIRESTORE) ---
def get_firestore_client():
    firebase_env = os.environ.get("FIREBASE_CREDENTIALS")
    creds = None
    if firebase_env:
        cred_info = json.loads(firebase_env)
        creds = service_account.Credentials.from_service_account_info(cred_info)
    else:
        key_path = "firebase-key.json"
        if os.path.exists(key_path):
            creds = service_account.Credentials.from_service_account_file(key_path)
    return firestore.Client(credentials=creds) if creds else None

# --- GERENCIAMENTO DE MEMÓRIA (CHAT) ---
def get_chat_history(chat_id, limit=5):
    db = get_firestore_client()
    if not db: return ""
    docs = db.collection('chats').document(str(chat_id)).collection('mensagens')\
             .order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit).stream()
    history_list = [f"{doc.to_dict()['role']}: {doc.to_dict()['content']}" for doc in docs]
    return "\n".join(reversed(history_list))

def save_chat_message(chat_id, role, content):
    db = get_firestore_client()
    if db:
        db.collection('chats').document(str(chat_id)).collection('mensagens').add({
            "role": role, "content": content, "timestamp": datetime.now()
        })

# --- SERVIÇO DE TAREFAS ---
class TaskService:
    def __init__(self, chat_id):
        self.db = get_firestore_client()
        self.chat_id = str(chat_id)

    def add_task(self, item):
        if not self.db: return False
        self.db.collection('chats').document(self.chat_id).collection('tasks').add({
            "item": item, "status": "pendente", "created_at": datetime.now()
        })
        return True

    def list_tasks(self):
        if not self.db: return "Erro no banco."
        docs = self.db.collection('chats').document(self.chat_id).collection('tasks')\
                 .where(filter=firestore.FieldFilter("status", "==", "pendente")).stream()
        tasks = [doc.to_dict()['item'] for doc in docs]
        if not tasks: return "✅ Nenhuma tarefa pendente!"
        msg = "📝 **Suas Pendências:**\n"
        for t in tasks: msg += f"• {t}\n"
        return msg

    def complete_task(self, item_name):
        if not self.db: return False
        docs = self.db.collection('chats').document(self.chat_id).collection('tasks')\
                 .where(filter=firestore.FieldFilter("status", "==", "pendente")).stream()
        found = False
        for doc in docs:
            data = doc.to_dict()
            if item_name.lower() in data['item'].lower():
                doc.reference.update({"status": "concluido"})
                found = True
        return found

# --- SERVIÇO FINANCEIRO (NOVO) ---
class FinanceService:
    def __init__(self, chat_id):
        self.db = get_firestore_client()
        self.chat_id = str(chat_id)

    def add_expense(self, amount, category, item):
        if not self.db: return False
        self.db.collection('chats').document(self.chat_id).collection('expenses').add({
            "amount": float(amount),
            "category": category,
            "item": item,
            "timestamp": datetime.now()
        })
        return True

    def get_monthly_report(self):
        if not self.db: return "Erro no banco."
        
        # Filtra do dia 1 do mês atual até agora
        now = datetime.now()
        start_date = datetime(now.year, now.month, 1)
        
        docs = self.db.collection('chats').document(self.chat_id).collection('expenses')\
                 .where(filter=firestore.FieldFilter("timestamp", ">=", start_date)).stream()
        
        total = 0.0
        details = ""
        for doc in docs:
            data = doc.to_dict()
            val = data.get('amount', 0)
            total += val
            details += f"• R$ {val:.2f} ({data.get('category')}) - {data.get('item')}\n"
            
        if total == 0: return "💸 Nenhum gasto registrado neste mês."
        
        return f"📊 **Gastos de {now.strftime('%B')}:**\n\n{details}\n💰 **TOTAL: R$ {total:.2f}**"

# --- GEMINI CÉREBRO ---
if GEMINI_KEY: genai.configure(api_key=GEMINI_KEY)

def get_system_prompt():
    now = datetime.now()
    dias = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    dia_semana = dias[now.weekday()]
    return f"""
    SYSTEM: Você é o Jarvis, assistente pessoal.
    Data: {now.strftime('%Y-%m-%d %H:%M')} ({dia_semana}).
    
    INTENÇÕES (Retorne APENAS JSON):
    
    1. AGENDAR: {{ "intent": "agendar", "title": "Titulo", "start_iso": "...", "end_iso": "...", "description": "..." }}
    2. CONSULTAR AGENDA: {{ "intent": "consultar_agenda", "time_min": "...", "time_max": "..." }}
    3. ADD TAREFA: {{ "intent": "add_task", "item": "..." }}
    4. LISTAR TAREFAS: {{ "intent": "list_tasks" }}
    5. CONCLUIR TAREFA: {{ "intent": "complete_task", "item": "..." }}
    
    6. ADD GASTO (Ex: "gastei 50 no uber", "comprei almoço por 30 reais"):
    {{ "intent": "add_expense", "amount": 50.00, "category": "Categoria (Transporte, Alimentação, Lazer, Contas)", "item": "Descrição curta" }}
    
    7. RELATORIO FINANCEIRO (Ex: "quanto gastei esse mês?", "resumo financeiro"):
    {{ "intent": "finance_report" }}
    
    8. CONVERSA: {{ "intent": "conversa", "response": "..." }}
    """

def ask_gemini(text_input, chat_id, is_audio=False):
    if not GEMINI_KEY: return None
    history = get_chat_history(chat_id)
    system_instruction = get_system_prompt()
    full_prompt = f"{system_instruction}\n\nHISTÓRICO:\n{history}\n\nUSUÁRIO:\n{text_input}"
    
    model = genai.GenerativeModel("gemini-2.0-flash")
    try:
        content = [text_input, full_prompt] if is_audio else full_prompt
        response = model.generate_content(content, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except Exception as e:
        print(f"❌ Erro IA: {e}")
        return None

# --- FERRAMENTAS ---
def download_telegram_voice(file_id):
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}")
    file_path = r.json().get("result", {}).get("file_path")
    if not file_path: return None
    file_content = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}").content
    local_path = "/tmp/voice.ogg"
    with open(local_path, "wb") as f: f.write(file_content)
    return local_path

class CalendarService:
    def __init__(self):
        self.creds = None
        self.calendar_id = CALENDAR_ID
        env_creds = os.environ.get("FIREBASE_CREDENTIALS")
        if env_creds:
            self.creds = service_account.Credentials.from_service_account_info(json.loads(env_creds), scopes=['https://www.googleapis.com/auth/calendar'])
        elif os.path.exists("firebase-key.json"):
            self.creds = service_account.Credentials.from_service_account_file("firebase-key.json", scopes=['https://www.googleapis.com/auth/calendar'])

    def execute(self, action, data):
        if not self.creds: return "Erro de credenciais"
        service = build('calendar', 'v3', credentials=self.creds)
        if action == "create":
            body = {'summary': data['title'], 'description': data.get('description', ''), 'start': {'dateTime': data['start_iso']}, 'end': {'dateTime': data['end_iso']}}
            service.events().insert(calendarId=self.calendar_id, body=body).execute()
            return True
        elif action == "list":
            tmin, tmax = data['time_min'], data['time_max']
            if not tmin.endswith("Z"): tmin += "-03:00"
            if not tmax.endswith("Z"): tmax += "-03:00"
            events = service.events().list(calendarId=self.calendar_id, timeMin=tmin, timeMax=tmax, singleEvents=True, orderBy='startTime').execute().get('items', [])
            if not events: return "📅 Nada na agenda."
            msg = "📅 **Agenda:**\n"
            for e in events:
                start = e['start'].get('dateTime', e['start'].get('date'))
                hora = start[11:16] if 'T' in start else "Dia todo"
                msg += f"• {hora} - {e['summary']}\n"
            return msg
        return False

# --- ROTAS ---
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    try: data = await request.json()
    except: return "error"
    if "message" not in data: return "ok"
    
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    ai_resp = None
    
    # ENTRADA
    if "text" in msg:
        ai_resp = ask_gemini(msg['text'], chat_id, is_audio=False)
        save_chat_message(chat_id, "user", msg['text']) # Salva input texto
    elif "voice" in msg:
        path = download_telegram_voice(msg["voice"]["file_id"])
        if path:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "🎧 Ouvindo..."})
            myfile = genai.upload_file(path, mime_type="audio/ogg")
            ai_resp = ask_gemini(myfile, chat_id, is_audio=True)
            save_chat_message(chat_id, "user", "[Audio Enviado]")

    # AÇÃO
    if ai_resp:
        intent = ai_resp.get("intent")
        cal = CalendarService()
        tasks = TaskService(chat_id)
        finance = FinanceService(chat_id) # Instancia o financeiro
        response_text = ""

        if intent == "conversa":
            response_text = ai_resp["response"]
            
        elif intent == "agendar":
            if cal.execute("create", ai_resp): response_text = f"✅ Agendado: {ai_resp['title']}"
            else: response_text = "❌ Falha ao agendar."
                
        elif intent == "consultar_agenda":
            response_text = cal.execute("list", ai_resp)
        
        elif intent == "add_task":
            if tasks.add_task(ai_resp["item"]): response_text = f"📝 Tarefa anotada: {ai_resp['item']}"
            
        elif intent == "list_tasks":
            response_text = tasks.list_tasks()
            
        elif intent == "complete_task":
            if tasks.complete_task(ai_resp["item"]): response_text = f"✅ Feito: {ai_resp['item']}"
            else: response_text = "🔍 Tarefa não encontrada."

        # --- NOVAS AÇÕES FINANCEIRAS ---
        elif intent == "add_expense":
            if finance.add_expense(ai_resp["amount"], ai_resp["category"], ai_resp["item"]):
                response_text = f"💸 Gasto anotado: R$ {ai_resp['amount']} ({ai_resp['item']})"
            else: response_text = "❌ Erro ao salvar gasto."
            
        elif intent == "finance_report":
            response_text = finance.get_monthly_report()

        # Resposta Final
        if response_text:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": response_text})
            if "consultar" not in intent and "list" not in intent and "report" not in intent:
                save_chat_message(chat_id, "model", response_text)

    return {"status": "ok"}