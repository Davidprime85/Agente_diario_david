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

load_dotenv()
app = FastAPI(title="Jarvis Anti-Parrot", version="9.5.0")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

# --- BASE ---
class GoogleServiceBase:
    def __init__(self):
        self.creds = None
        scopes = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/drive.readonly', 'https://www.googleapis.com/auth/datastore']
        env = os.environ.get("FIREBASE_CREDENTIALS")
        if env: self.creds = service_account.Credentials.from_service_account_info(json.loads(env), scopes=scopes)
        elif os.path.exists("firebase-key.json"): self.creds = service_account.Credentials.from_service_account_file("firebase-key.json", scopes=scopes)
    def get_db(self): return firestore.Client(credentials=self.creds) if self.creds else None

# --- HELPERS ---
def format_currency(v): return f"{v:.2f}".replace('.', ',')
def get_month_name(m): return {1:'Jan',2:'Fev',3:'Mar',4:'Abr',5:'Mai',6:'Jun',7:'Jul',8:'Ago',9:'Set',10:'Out',11:'Nov',12:'Dez'}.get(m,'Mês')
def send_telegram(chat_id, text):
    if TELEGRAM_TOKEN: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})
def download_voice(fid):
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={fid}")
    path = r.json().get("result", {}).get("file_path")
    if not path: return None
    data = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}").content
    with open("/tmp/voice.ogg", "wb") as f: f.write(data)
    return "/tmp/voice.ogg"

# --- MEMORIA & PROTECAO ---
def check_processed(chat_id, mid):
    db = GoogleServiceBase().get_db()
    if not db: return False
    ref = db.collection('chats').document(str(chat_id)).collection('processed_ids').document(str(mid))
    if ref.get().exists: return True
    ref.set({"ts": datetime.now()}); return False

def reset_memory(chat_id):
    db = GoogleServiceBase().get_db()
    if not db: return
    # Deleta as ultimas 20 mensagens para garantir limpeza total
    msgs = db.collection('chats').document(str(chat_id)).collection('mensagens').limit(20).stream()
    for m in msgs: m.reference.delete()

def save_msg(chat_id, role, content):
    db = GoogleServiceBase().get_db()
    if db:
        db.collection('chats').document(str(chat_id)).set({"last": datetime.now()}, merge=True)
        db.collection('chats').document(str(chat_id)).collection('mensagens').add({"role": role, "content": content, "ts": datetime.now()})

def get_history(chat_id):
    db = GoogleServiceBase().get_db()
    if not db: return ""
    docs = db.collection('chats').document(str(chat_id)).collection('mensagens').order_by('ts', direction=firestore.Query.DESCENDING).limit(5).stream()
    return "\n".join(reversed([f"{d.to_dict()['role']}: {d.to_dict()['content']}" for d in docs]))

# --- SERVICES ---
class CalendarService(GoogleServiceBase):
    def __init__(self): super().__init__(); self.cid = CALENDAR_ID
    def execute(self, act, d):
        if not self.creds: return None
        svc = build('calendar', 'v3', credentials=self.creds)
        if act=="create": 
            svc.events().insert(calendarId=self.cid, body={'summary':d['title'],'description':d.get('description',''),'start':{'dateTime':d['start_iso']},'end':{'dateTime':d['end_iso']}}).execute(); return True
        if act=="list":
            tmin, tmax = d['time_min'], d['time_max']
            if not tmin.endswith("Z"): tmin+="-03:00"
            if not tmax.endswith("Z"): tmax+="-03:00"
            return svc.events().list(calendarId=self.cid, timeMin=tmin, timeMax=tmax, singleEvents=True, orderBy='startTime').execute().get('items', [])

class TaskService(GoogleServiceBase):
    def __init__(self, cid): super().__init__(); self.db=self.get_db(); self.cid=str(cid)
    def add(self, i): self.db.collection('chats').document(self.cid).collection('tasks').add({"item":i,"status":"pendente"})
    def list(self): 
        docs = self.db.collection('chats').document(self.cid).collection('tasks').where(filter=firestore.FieldFilter("status","==","pendente")).stream()
        ls = [d.to_dict()['item'] for d in docs]; return "📝 " + ", ".join(ls) if ls else "✅ Nada pendente."
    def complete(self, i):
        docs = self.db.collection('chats').document(self.cid).collection('tasks').where(filter=firestore.FieldFilter("status","==","pendente")).stream()
        for d in docs: 
            if i.lower() in d.to_dict()['item'].lower(): d.reference.update({"status":"concluido"}); return True
        return False

class FinanceService(GoogleServiceBase):
    def __init__(self, cid): super().__init__(); self.db=self.get_db(); self.cid=str(cid)
    def add(self, a, c, i): self.db.collection('chats').document(self.cid).collection('expenses').add({"amount":float(a),"category":c,"item":i,"timestamp":datetime.now()})
    def report(self):
        now=datetime.now(); start=datetime(now.year,now.month,1); docs=self.db.collection('chats').document(self.cid).collection('expenses').where(filter=firestore.FieldFilter("timestamp",">=",start)).stream()
        tot=0; txt=""
        for d in docs: dat=d.to_dict(); tot+=dat['amount']; txt+=f"• R$ {format_currency(dat['amount'])} ({dat['item']})\n"
        return f"📊 Total: R$ {format_currency(tot)}\n{txt}" if txt else "💸 Nada gasto."

class DriveService(GoogleServiceBase):
    def list(self, folder):
        if not self.creds: return []
        svc = build('drive', 'v3', credentials=self.creds)
        res = svc.files().list(q=f"mimeType='application/vnd.google-apps.folder' and name='{folder}'", fields="files(id)").execute()
        if not res.get('files'): return None
        fid = res['files'][0]['id']
        return svc.files().list(q=f"'{fid}' in parents", fields="files(id, name, mimeType)").execute().get('files', [])
    def read(self, fid, mime):
        try:
            svc = build('drive', 'v3', credentials=self.creds)
            req = svc.files().export_media(fileId=fid, mimeType='text/plain') if "google" in mime else svc.files().get_media(fileId=fid)
            fh = io.BytesIO(); dl = MediaIoBaseDownload(fh, req); done=False
            while not done: _, done = dl.next_chunk()
            return fh.getvalue().decode('utf-8', errors='ignore')[:2000]
        except: return ""

# --- GEMINI & PROMPT CORRIGIDO ---
if GEMINI_KEY: genai.configure(api_key=GEMINI_KEY)

def analyze_drive(folder):
    drv = DriveService(); files = drv.list(folder)
    if not files: return "Pasta vazia."
    txt = ""; 
    for f in files[:1]: txt += f"\nFILE {f['name']}: {drv.read(f['id'], f['mimeType'])}"
    return genai.GenerativeModel("gemini-2.0-flash").generate_content(f"Analise: {txt}").text

def ask_gemini(text, chat_id, is_audio=False):
    hist = get_history(chat_id)
    now = datetime.now()
    user_p = "[Audio Enviado]" if is_audio else text
    
    # PROMPT ANTIPAPAGAIO: Instrução explícita para não repetir
    sys = f"""SYSTEM: Jarvis Assistente. Data: {now.strftime('%d/%m %H:%M')}.
    
    IMPORTANTE:
    1. Você é um assistente útil.
    2. NÃO REPITA o texto do usuário. Se ele disser "Oi", responda "Olá, como ajudo?".
    3. Se o usuário pedir algo, execute.
    
    JSON INTENTS:
    - agendar, consultar_agenda, add_task, list_tasks, complete_task
    - add_expense, finance_report, analyze_project
    - conversa (Use para responder saudações ou perguntas gerais)
    
    HISTÓRICO:
    {hist}
    
    COMANDO DO USUÁRIO AGORA:
    "{user_p}"
    """
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        content = [text, sys] if is_audio else sys
        resp = json.loads(model.generate_content(content, generation_config={"response_mime_type": "application/json"}).text)
        
        # --- FILTRO ANTI-PAPAGAIO (PYTHON) ---
        # Se a IA devolveu a mesma coisa que o usuário escreveu, forçamos uma resposta padrão
        if resp.get("intent") == "conversa":
            ai_text = resp.get("response", "").strip().lower()
            user_text_clean = text.strip().lower()
            # Se for igual ou muito parecido (papagaio), mude a resposta
            if ai_text == user_text_clean:
                resp["response"] = "Olá! Entendi. Como posso ajudar com sua agenda ou projetos hoje?"
        # -------------------------------------
        
        return resp
    except Exception as e: 
        print(e)
        return {"intent": "conversa", "response": "Erro interno na IA."}

def morning_msg(ev, tk):
    return genai.GenerativeModel("gemini-2.0-flash").generate_content(f"Bom dia motivacional. Agenda: {ev}. Tasks: {tk}").text

# --- ROTAS ---
@app.post("/telegram/webhook")
async def webhook(request: Request):
    try: data = await request.json()
    except: return "error"
    if "message" not in data: return "ok"
    
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    msg_id = msg["message_id"]
    text = msg.get("text", "")

    # RESET MANUAL
    if text == "/reset":
        reset_memory(chat_id)
        send_telegram(chat_id, "🧠 Memória Resetada e Cache Limpo!")
        return {"status": "reset"}

    # ANTI LOOP
    if check_processed(chat_id, msg_id): return {"status": "ignored"}

    ai_resp = None
    if "text" in msg:
        save_msg(chat_id, "user", text)
        ai_resp = ask_gemini(text, chat_id)
    elif "voice" in msg:
        save_msg(chat_id, "user", "[Audio]")
        path = download_voice(msg["voice"]["file_id"])
        if path:
            send_telegram(chat_id, "🎧 Ouvindo...")
            myfile = genai.upload_file(path, mime_type="audio/ogg")
            ai_resp = ask_gemini(myfile, chat_id, is_audio=True)

    if ai_resp:
        intent = ai_resp.get("intent")
        cal=CalendarService(); tsk=TaskService(chat_id); fin=FinanceService(chat_id)
        resp = ""
        
        if intent == "conversa": resp = ai_resp["response"]
        elif intent == "agendar": 
            if cal.execute("create", ai_resp): resp = f"✅ Agendado: {ai_resp['title']}"
        elif intent == "consultar_agenda":
            evs = cal.execute("list", ai_resp)
            resp = "📅 " + "\n".join([f"{e['start'].get('dateTime')[11:16]} {e['summary']}" for e in evs]) if evs else "📅 Agenda vazia."
        elif intent == "add_task": tsk.add(ai_resp["item"]); resp = f"📝 Add: {ai_resp['item']}"
        elif intent == "list_tasks": resp = tsk.list()
        elif intent == "complete_task": resp = "✅ Feito." if tsk.complete(ai_resp["item"]) else "🔍 Não achei."
        elif intent == "add_expense": fin.add(ai_resp["amount"], ai_resp["category"], ai_resp["item"]); resp = f"💸 Add R${ai_resp['amount']}"
        elif intent == "finance_report": resp = fin.report()
        elif intent == "analyze_project":
            send_telegram(chat_id, f"📂 Lendo: {ai_resp['folder']}...")
            resp = analyze_drive(ai_resp['folder'])

        if resp:
            send_telegram(chat_id, resp)
            if "consultar" not in intent and "list" not in intent: save_msg(chat_id, "model", resp)

    return {"status": "ok"}

@app.get("/")
def home(): return {"status": "Jarvis Anti-Parrot Ativo"}

@app.get("/dashboard", response_class=HTMLResponse)
def dash():
    # Dashboard Simplificado para caber no codigo
    base=GoogleServiceBase(); db=base.get_db()
    if not db: return "Erro DB"
    docs = db.collection('chats').stream()
    html="<html><body><h1>Dashboard</h1>"
    for d in docs: html+=f"<p>User {d.id}</p><hr>"
    return html+"</body></html>"

@app.get("/cron/bom-dia")
def cron():
    base=GoogleServiceBase(); db=base.get_db()
    if not db: return {"err":"db"}
    docs=db.collection('chats').stream(); count=0
    now=datetime.now(); tmin=now.strftime("%Y-%m-%dT00:00:00"); tmax=now.strftime("%Y-%m-%dT23:59:59")
    for d in docs:
        cid=d.id; cal=CalendarService(); tsk=TaskService(cid)
        evs=cal.execute("list",{"time_min":tmin,"time_max":tmax})
        ev_t = ", ".join([e['summary'] for e in evs]) if evs else "Nada"
        tk_t = tsk.list()
        msg = morning_msg(ev_t, tk_t)
        send_telegram(cid, msg); count+=1
    return {"sent":count}