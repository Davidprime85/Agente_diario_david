import os
import json
import requests
import io
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.cloud import firestore
import google.generativeai as genai
from dotenv import load_dotenv

# --- CONFIGURAÇÕES ---
load_dotenv()
app = FastAPI(title="Jarvis Full System", version="10.1.0 (Fix Moeda)")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

# --- SERVIÇO BASE ---
class GoogleServiceBase:
    def __init__(self):
        self.creds = None
        scopes = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/drive.readonly', 'https://www.googleapis.com/auth/datastore']
        env = os.environ.get("FIREBASE_CREDENTIALS")
        if env: self.creds = service_account.Credentials.from_service_account_info(json.loads(env), scopes=scopes)
        elif os.path.exists("firebase-key.json"): self.creds = service_account.Credentials.from_service_account_file("firebase-key.json", scopes=scopes)
    def get_firestore_client(self): return firestore.Client(credentials=self.creds) if self.creds else None

# --- HELPERS ---
def format_currency(value): return f"{value:.2f}".replace('.', ',')
def get_month_name(m): return {1:'Janeiro',2:'Fevereiro',3:'Março',4:'Abril',5:'Maio',6:'Junho',7:'Julho',8:'Agosto',9:'Setembro',10:'Outubro',11:'Novembro',12:'Dezembro'}.get(m,'Mês')

def send_telegram(chat_id, text):
    if TELEGRAM_TOKEN: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})

def download_telegram_voice(file_id):
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}")
    path = r.json().get("result", {}).get("file_path")
    if not path: return None
    data = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}").content
    with open("/tmp/voice.ogg", "wb") as f: f.write(data)
    return "/tmp/voice.ogg"

# --- MEMÓRIA & PROTEÇÃO ---
def check_is_processed(chat_id, msg_id):
    base = GoogleServiceBase(); db = base.get_firestore_client()
    if not db: return False
    ref = db.collection('chats').document(str(chat_id)).collection('processed_ids').document(str(msg_id))
    if ref.get().exists: return True
    ref.set({"ts": datetime.now()}); return False

def reset_memory(chat_id):
    base = GoogleServiceBase(); db = base.get_firestore_client()
    if not db: return
    msgs = db.collection('chats').document(str(chat_id)).collection('mensagens').limit(50).stream()
    for m in msgs: m.reference.delete()

def save_chat_message(chat_id, role, content):
    base = GoogleServiceBase(); db = base.get_firestore_client()
    if db:
        db.collection('chats').document(str(chat_id)).set({"last_active": datetime.now()}, merge=True)
        db.collection('chats').document(str(chat_id)).collection('mensagens').add({"role": role, "content": content, "timestamp": datetime.now()})

def get_chat_history(chat_id, limit=5):
    base = GoogleServiceBase(); db = base.get_firestore_client()
    if not db: return ""
    docs = db.collection('chats').document(str(chat_id)).collection('mensagens').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit).stream()
    return "\n".join(reversed([f"{d.to_dict()['role']}: {d.to_dict()['content']}" for d in docs]))

# --- SERVIÇOS ---
class CalendarService(GoogleServiceBase):
    def __init__(self): super().__init__(); self.calendar_id = CALENDAR_ID
    def execute(self, action, data):
        if not self.creds: return None
        svc = build('calendar', 'v3', credentials=self.creds)
        if action == "create":
            svc.events().insert(calendarId=self.calendar_id, body={'summary': data['title'], 'description': data.get('description', ''), 'start': {'dateTime': data['start_iso']}, 'end': {'dateTime': data['end_iso']}}).execute(); return True
        elif action == "list":
            tmin, tmax = data['time_min'], data['time_max']
            if not tmin.endswith("Z"): tmin += "-03:00"
            if not tmax.endswith("Z"): tmax += "-03:00"
            return svc.events().list(calendarId=self.calendar_id, timeMin=tmin, timeMax=tmax, singleEvents=True, orderBy='startTime').execute().get('items', [])

class DriveService(GoogleServiceBase):
    def list_files_in_folder(self, folder_name):
        if not self.creds: return []
        svc = build('drive', 'v3', credentials=self.creds)
        res = svc.files().list(q=f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false", fields="files(id)").execute()
        if not res.get('files'): return None
        fid = res.get('files')[0]['id']
        return svc.files().list(q=f"'{fid}' in parents", fields="files(id, name, mimeType)").execute().get('files', [])
    def read_file_content(self, file_id, mime_type):
        try:
            svc = build('drive', 'v3', credentials=self.creds)
            req = svc.files().export_media(fileId=file_id, mimeType='text/plain') if "google" in mime_type else svc.files().get_media(fileId=file_id)
            fh = io.BytesIO(); dl = MediaIoBaseDownload(fh, req); done = False
            while not done: _, done = dl.next_chunk()
            return fh.getvalue().decode('utf-8', errors='ignore')[:3000]
        except Exception as e: return f"[Erro: {str(e)}]"

class TaskService(GoogleServiceBase):
    def __init__(self, chat_id): super().__init__(); self.db = self.get_firestore_client(); self.chat_id = str(chat_id)
    def add_task(self, item): self.db.collection('chats').document(self.chat_id).collection('tasks').add({"item": item, "status": "pendente"}); return True
    def list_tasks_formatted(self):
        docs = self.db.collection('chats').document(self.chat_id).collection('tasks').where(filter=firestore.FieldFilter("status", "==", "pendente")).stream()
        ls = [d.to_dict()['item'] for d in docs]; return "📝 **Pendências:**\n" + "\n".join([f"• {t}" for t in ls]) if ls else "✅ Nada pendente."
    def complete_task(self, item):
        docs = self.db.collection('chats').document(self.chat_id).collection('tasks').where(filter=firestore.FieldFilter("status", "==", "pendente")).stream()
        for d in docs:
            if item.lower() in d.to_dict()['item'].lower(): d.reference.update({"status": "concluido"}); return True
        return False

class FinanceService(GoogleServiceBase):
    def __init__(self, chat_id): super().__init__(); self.db = self.get_firestore_client(); self.chat_id = str(chat_id)
    def add_expense(self, amount, category, item):
        if not self.db: return False
        self.db.collection('chats').document(self.chat_id).collection('expenses').add({"amount": float(amount), "category": category, "item": item, "timestamp": datetime.now()}); return True
    def get_monthly_report(self):
        now = datetime.now(); start = datetime(now.year, now.month, 1)
        docs = self.db.collection('chats').document(self.chat_id).collection('expenses').where(filter=firestore.FieldFilter("timestamp", ">=", start)).stream()
        tot = 0; txt = ""
        for d in docs: dt = d.to_dict(); tot += dt['amount']; txt += f"• R$ {format_currency(dt['amount'])} ({dt.get('category')}) - {dt.get('item')}\n"
        return f"📊 **Gastos de {get_month_name(now.month)}:**\n\n{txt}\n💰 **TOTAL: R$ {format_currency(tot)}**" if txt else "💸 Sem gastos."

# --- AI ---
if GEMINI_KEY: genai.configure(api_key=GEMINI_KEY)

def analyze_project_folder(folder):
    drv = DriveService(); files = drv.list_files_in_folder(folder)
    if not files: return "📂 Pasta vazia ou não achada."
    txt = ""
    for f in files[:1]: txt += f"\nFILE {f['name']}: {drv.read_file_content(f['id'], f['mimeType'])}"
    return genai.GenerativeModel("gemini-2.0-flash").generate_content(f"Analise: {txt}").text

def ask_gemini(text, chat_id, is_audio=False):
    hist = get_chat_history(chat_id); now = datetime.now()
    user_p = "[Audio Enviado]" if is_audio else text
    sys = f"""SYSTEM: Jarvis. Data: {now.strftime('%d/%m %H:%M')}.
    1. NÃO REPITA o usuário.
    2. JSON Intents: agendar, consultar_agenda, add_task, list_tasks, complete_task, add_expense, finance_report, analyze_project, conversa.
    HISTÓRICO: {hist}
    USUÁRIO: "{user_p}"
    """
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        content = [text, sys] if is_audio else sys
        res = json.loads(model.generate_content(content, generation_config={"response_mime_type": "application/json"}).text)
        if res.get("intent") == "conversa":
            if res.get("response", "").strip().lower() == text.strip().lower(): res["response"] = "Entendi. Como ajudo?"
        return res
    except: return {"intent": "conversa", "response": "Erro interno."}

def generate_morning_message(ev, tk): return genai.GenerativeModel("gemini-2.0-flash").generate_content(f"Bom dia. Agenda: {ev}. Tasks: {tk}").text

# --- ROTAS ---
@app.get("/")
def home(): return {"status": "Jarvis V10.1 Online"}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    base = GoogleServiceBase(); db = base.get_firestore_client()
    if not db: return "Erro DB"
    docs = db.collection('chats').stream()
    html = "<html><body><h1>Dashboard</h1>"
    for doc in docs:
        cid = doc.id; now = datetime.now(); start = datetime(now.year, now.month, 1)
        exps = db.collection('chats').document(cid).collection('expenses').where(filter=firestore.FieldFilter("timestamp", ">=", start)).stream()
        tot = 0; rows = ""; has = False
        for e in exps: d = e.to_dict(); tot += d['amount']; rows += f"<p>{d['timestamp'].strftime('%d/%m')} - {d['item']}: R$ {format_currency(d['amount'])}</p>"; has = True
        if has: html += f"<h3>User: {cid}</h3>{rows}<p><b>Total: R$ {format_currency(tot)}</b></p><hr>"
    return html + "</body></html>"

@app.get("/cron/bom-dia")
def cron():
    base = GoogleServiceBase(); db = base.get_firestore_client()
    if not db: return {"err": "db"}
    docs = db.collection('chats').stream(); count = 0
    now = datetime.now(); tmin = now.strftime("%Y-%m-%dT00:00:00"); tmax = now.strftime("%Y-%m-%dT23:59:59")
    for d in docs:
        cid = d.id; cal = CalendarService(); tsk = TaskService(cid)
        ev = cal.execute("list", {"time_min": tmin, "time_max": tmax}); ev_txt = ", ".join([e['summary'] for e in ev]) if ev else "Nada"
        tk_txt = tsk.list_tasks_formatted()
        send_telegram(cid, generate_morning_message(ev_txt, tk_txt)); count += 1
    return {"sent": count}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    try: data = await request.json()
    except: return "error"
    if "message" not in data: return "ok"
    
    msg = data["message"]; chat_id = msg["chat"]["id"]; msg_id = msg["message_id"]; text = msg.get("text", "")

    if text == "/reset": reset_memory(chat_id); send_telegram(chat_id, "🧠 Memória Resetada!"); return {"status": "reset"}
    if check_is_processed(chat_id, msg_id): return {"status": "ignored"}

    ai_resp = None
    if "text" in msg: save_chat_message(chat_id, "user", text); ai_resp = ask_gemini(text, chat_id)
    elif "voice" in msg:
        save_chat_message(chat_id, "user", "[Audio]"); path = download_telegram_voice(msg["voice"]["file_id"])
        if path: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "🎧..."}); myfile = genai.upload_file(path, mime_type="audio/ogg"); ai_resp = ask_gemini(myfile, chat_id, is_audio=True)

    if ai_resp:
        intent = ai_resp.get("intent")
        cal = CalendarService(); tsk = TaskService(chat_id); fin = FinanceService(chat_id); resp = ""
        
        if intent == "conversa": resp = ai_resp["response"]
        elif intent == "agendar": resp = f"✅ Agendado: {ai_resp['title']}" if cal.execute("create", ai_resp) else "❌ Erro."
        elif intent == "consultar_agenda": ev = cal.execute("list", ai_resp); resp = "📅 " + "\n".join([f"{e['start'].get('dateTime')[11:16]} {e['summary']}" for e in ev]) if ev else "📅 Vazia."
        elif intent == "add_task": tsk.add_task(ai_resp["item"]); resp = f"📝 Add: {ai_resp['item']}"
        elif intent == "list_tasks": resp = tsk.list_tasks_formatted()
        elif intent == "complete_task": resp = "✅ Feito." if tsk.complete_task(ai_resp["item"]) else "🔍 Nao achei."
        
        elif intent == "add_expense":
            # --- CORREÇÃO DE VÍRGULA/FLOAT ---
            try:
                # Troca vírgula por ponto antes de converter
                raw_val = str(ai_resp["amount"]).replace(',', '.')
                val = float(raw_val)
                fin.add_expense(val, ai_resp["category"], ai_resp["item"])
                resp = f"💸 Gasto: R$ {format_currency(val)}"
            except Exception as e:
                resp = f"❌ Erro de valor. Tente dizer '45 reais' sem centavos. (Erro: {str(e)})"
            # ---------------------------------

        elif intent == "finance_report": resp = fin.get_monthly_report()
        elif intent == "analyze_project": send_telegram(chat_id, f"📂 Lendo: {ai_resp['folder']}..."); resp = analyze_project_folder(ai_resp["folder"])

        if resp: send_telegram(chat_id, resp); save_chat_message(chat_id, "model", resp) if "consultar" not in intent else None

    return {"status": "ok"}