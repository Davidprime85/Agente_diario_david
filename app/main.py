import os
import json
import requests
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import FastAPI, Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import firestore
import google.generativeai as genai
from dotenv import load_dotenv

# --- CONFIGURAÇÕES ---
load_dotenv()
app = FastAPI(title="Agente Jarvis", version="4.0.0 (Cron+FinanceBR)")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

# --- CONEXÃO FIREBASE ---
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

# --- AUXILIARES ---
def format_currency(value):
    """Transforma 10.5 em 10,50"""
    return f"{value:.2f}".replace('.', ',')

def get_month_name(month_int):
    meses = {1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril', 5: 'Maio', 6: 'Junho',
             7: 'Julho', 8: 'Agosto', 9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'}
    return meses.get(month_int, 'Mês Atual')

def send_telegram(chat_id, text):
    if TELEGRAM_TOKEN:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})

# --- SERVIÇOS ---
class FinanceService:
    def __init__(self, chat_id):
        self.db = get_firestore_client()
        self.chat_id = str(chat_id)

    def add_expense(self, amount, category, item):
        if not self.db: return False
        self.db.collection('chats').document(self.chat_id).collection('expenses').add({
            "amount": float(amount), "category": category, "item": item, "timestamp": datetime.now()
        })
        return True

    def get_monthly_report(self):
        if not self.db: return "Erro no banco."
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
            # Correção BR: Vírgula
            details += f"• R$ {format_currency(val)} ({data.get('category')}) - {data.get('item')}\n"
            
        if total == 0: return "💸 Nenhum gasto registrado neste mês."
        
        # Correção BR: Nome do Mês
        nome_mes = get_month_name(now.month)
        return f"📊 **Gastos de {nome_mes}:**\n\n{details}\n💰 **TOTAL: R$ {format_currency(total)}**"

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

    def list_tasks_raw(self):
        """Retorna lista pura para uso interno"""
        if not self.db: return []
        docs = self.db.collection('chats').document(self.chat_id).collection('tasks')\
                 .where(filter=firestore.FieldFilter("status", "==", "pendente")).stream()
        return [doc.to_dict()['item'] for doc in docs]

    def list_tasks(self):
        tasks = self.list_tasks_raw()
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
            if item_name.lower() in doc.to_dict()['item'].lower():
                doc.reference.update({"status": "concluido"})
                found = True
        return found

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
            return events # Retorna objeto puro para formatar depois
        return None

# --- MEMÓRIA ---
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

# --- GEMINI ---
if GEMINI_KEY: genai.configure(api_key=GEMINI_KEY)

def ask_gemini(text_input, chat_id, is_audio=False):
    # Prompt padrão de conversa
    history = get_chat_history(chat_id)
    now = datetime.now()
    dias = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    
    system = f"""
    SYSTEM: Jarvis Assistente. Data: {now.strftime('%Y-%m-%d %H:%M')} ({dias[now.weekday()]}).
    INTENÇÕES JSON:
    1. AGENDAR: {{ "intent": "agendar", "title": "...", "start_iso": "...", "end_iso": "..." }}
    2. LER AGENDA: {{ "intent": "consultar_agenda", "time_min": "...", "time_max": "..." }}
    3. ADD TAREFA: {{ "intent": "add_task", "item": "..." }}
    4. LISTAR TAREFAS: {{ "intent": "list_tasks" }}
    5. CONCLUIR TAREFA: {{ "intent": "complete_task", "item": "..." }}
    6. ADD GASTO: {{ "intent": "add_expense", "amount": 10.50, "category": "...", "item": "..." }}
    7. RELATORIO: {{ "intent": "finance_report" }}
    8. CONVERSA: {{ "intent": "conversa", "response": "..." }}
    """
    
    model = genai.GenerativeModel("gemini-2.0-flash")
    full_prompt = f"{system}\nHISTÓRICO:\n{history}\nUSER:\n{text_input}"
    
    try:
        content = [text_input, full_prompt] if is_audio else full_prompt
        resp = model.generate_content(content, generation_config={"response_mime_type": "application/json"})
        return json.loads(resp.text)
    except: return None

def generate_morning_message(events, tasks):
    # Prompt Específico para o Bom Dia
    prompt = f"""
    Crie um texto de "Bom dia" curto, motivacional e útil para o David.
    
    Agenda de Hoje:
    {events}
    
    Tarefas Pendentes:
    {tasks}
    
    Estrutura:
    1. Saudação animada.
    2. Resumo rápido do dia.
    3. Frase curta de impacto.
    """
    model = genai.GenerativeModel("gemini-2.0-flash")
    return model.generate_content(prompt).text

# --- ROTAS & CRON ---
@app.get("/cron/bom-dia")
def cron_bom_dia():
    """Rota que o Vercel chama todo dia"""
    print("☀️ Iniciando Rotina de Bom Dia...")
    db = get_firestore_client()
    if not db: return {"status": "error", "msg": "Sem banco"}
    
    # 1. Pega todos os chats que conversaram com o bot
    # (Itera sobre a coleção 'chats' para achar o ID do David)
    docs = db.collection('chats').stream()
    
    cal = CalendarService()
    now = datetime.now()
    # Agenda de hoje (00:00 até 23:59)
    time_min = now.strftime("%Y-%m-%dT00:00:00")
    time_max = now.strftime("%Y-%m-%dT23:59:59")
    cal_data = {"time_min": time_min, "time_max": time_max}
    
    count = 0
    for doc in docs:
        chat_id = doc.id
        
        # 2. Busca dados do usuário
        tasks_service = TaskService(chat_id)
        
        events_list = cal.execute("list", cal_data)
        tasks_list = tasks_service.list_tasks_raw()
        
        # Formata para a IA ler
        events_txt = "Nada na agenda."
        if isinstance(events_list, list) and events_list:
            events_txt = "\n".join([f"- {e['summary']} às {e['start'].get('dateTime')[11:16]}" for e in events_list])
            
        tasks_txt = "Nenhuma pendência."
        if tasks_list:
            tasks_txt = "\n".join([f"- {t}" for t in tasks_list])
            
        # 3. Gera texto e envia
        msg_final = generate_morning_message(events_txt, tasks_txt)
        send_telegram(chat_id, msg_final)
        count += 1
        
    return {"status": "ok", "enviados": count}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    try: data = await request.json()
    except: return "error"
    if "message" not in data: return "ok"
    
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    ai_resp = None
    
    # DOWNLOAD VOICE helper
    def get_voice(fid):
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={fid}")
        path = r.json().get("result", {}).get("file_path")
        content = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}").content
        with open("/tmp/voice.ogg", "wb") as f: f.write(content)
        return "/tmp/voice.ogg"

    if "text" in msg:
        save_chat_message(chat_id, "user", msg['text'])
        ai_resp = ask_gemini(msg['text'], chat_id)
    elif "voice" in msg:
        save_chat_message(chat_id, "user", "[Audio]")
        path = get_voice(msg["voice"]["file_id"])
        myfile = genai.upload_file(path, mime_type="audio/ogg")
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "🎧..."})
        ai_resp = ask_gemini(myfile, chat_id, is_audio=True)

    if ai_resp:
        intent = ai_resp.get("intent")
        cal = CalendarService()
        tasks = TaskService(chat_id)
        fin = FinanceService(chat_id)
        resp = ""
        
        if intent == "conversa": resp = ai_resp["response"]
        elif intent == "agendar":
            if cal.execute("create", ai_resp): resp = f"✅ Agendado: {ai_resp['title']}"
        elif intent == "consultar_agenda":
            raw = cal.execute("list", ai_resp)
            if not raw: resp = "📅 Nada na agenda."
            else:
                resp = "📅 **Agenda:**\n" + "\n".join([f"• {e['start'].get('dateTime')[11:16]} - {e['summary']}" for e in raw])
        elif intent == "add_task":
            if tasks.add_task(ai_resp["item"]): resp = f"📝 Anotado: {ai_resp['item']}"
        elif intent == "list_tasks": resp = tasks.list_tasks()
        elif intent == "complete_task":
            if tasks.complete_task(ai_resp["item"]): resp = f"✅ Feito: {ai_resp['item']}"
        
        # --- FINANCEIRO CORRIGIDO ---
        elif intent == "add_expense":
            val = float(ai_resp["amount"])
            if fin.add_expense(val, ai_resp["category"], ai_resp["item"]):
                resp = f"💸 Gasto: R$ {format_currency(val)} ({ai_resp['item']})"
        elif intent == "finance_report":
            resp = fin.get_monthly_report()

        if resp:
            send_telegram(chat_id, resp)
            if intent not in ["consultar_agenda", "list_tasks", "finance_report"]:
                save_chat_message(chat_id, "model", resp)

    return {"status": "ok"}