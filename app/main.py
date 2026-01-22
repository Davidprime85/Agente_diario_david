import os
import json
import requests
import io
import logging
from datetime import datetime
from typing import Optional
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
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Jarvis V13 Explorer", version="13.0.0")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

# --- CONEXÃO GOOGLE ---
def get_creds():
    scopes = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/drive.readonly', 'https://www.googleapis.com/auth/datastore']
    env = os.environ.get("FIREBASE_CREDENTIALS")
    if env: return service_account.Credentials.from_service_account_info(json.loads(env), scopes=scopes)
    if os.path.exists("firebase-key.json"): return service_account.Credentials.from_service_account_file("firebase-key.json", scopes=scopes)
    return None

def get_db():
    creds = get_creds()
    return firestore.Client(credentials=creds) if creds else None

# --- HELPERS ---
def format_currency(val): return f"{val:.2f}".replace('.', ',')
def send_telegram(chat_id, text):
    if TELEGRAM_TOKEN: 
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})
        except: pass

def download_voice(fid):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={fid}")
        path = r.json().get("result", {}).get("file_path")
        if not path: return None
        content = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}").content
        with open("/tmp/voice.ogg", "wb") as f: f.write(content)
        return "/tmp/voice.ogg"
    except: return None

# --- MEMÓRIA ---
def check_is_processed(chat_id, msg_id):
    db = get_db()
    if not db: return False
    ref = db.collection('chats').document(str(chat_id)).collection('processed_ids').document(str(msg_id))
    if ref.get().exists: return True
    ref.set({"ts": datetime.now()})
    return False

def reset_memory(chat_id):
    db = get_db()
    if not db: return
    msgs = db.collection('chats').document(str(chat_id)).collection('mensagens').limit(50).stream()
    for m in msgs: m.reference.delete()

def save_msg(chat_id, role, content):
    db = get_db()
    if db:
        cid = str(chat_id)
        db.collection('chats').document(cid).set({"last_active": datetime.now()}, merge=True)
        db.collection('chats').document(cid).collection('mensagens').add({"role": role, "content": content, "timestamp": datetime.now()})

def get_history(chat_id):
    db = get_db()
    if not db: return ""
    docs = db.collection('chats').document(str(chat_id)).collection('mensagens').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(6).stream()
    return "\n".join(reversed([f"{d.to_dict()['role']}: {d.to_dict()['content']}" for d in docs]))

# --- SERVIÇOS ---
class DriveService:
    def __init__(self): 
        creds = get_creds()
        self.service = build('drive', 'v3', credentials=creds) if creds else None

    def search_folder(self, name_query):
        """Busca pasta de forma inteligente (contém nome, ignora maiúsculas)"""
        if not self.service: return None
        # Busca pasta que NÃO está na lixeira e cujo nome CONTÉM o texto buscado
        q = f"mimeType='application/vnd.google-apps.folder' and name contains '{name_query}' and trashed=false"
        res = self.service.files().list(q=q, fields="files(id, name)").execute()
        folders = res.get('files', [])
        return folders[0] if folders else None # Retorna a primeira encontrada

    def list_files_in_folder(self, folder_id):
        if not self.service: return []
        q = f"'{folder_id}' in parents and trashed=false"
        res = self.service.files().list(q=q, fields="files(id, name, mimeType)").execute()
        return res.get('files', [])

    def read_file(self, fid, mime):
        try:
            req = self.service.files().export_media(fileId=fid, mimeType='text/plain') if "google" in mime else self.service.files().get_media(fileId=fid)
            fh = io.BytesIO(); dl = MediaIoBaseDownload(fh, req); done = False
            while not done: _, done = dl.next_chunk()
            return fh.getvalue().decode('utf-8', errors='ignore')[:4000]
        except: return ""

class CalendarService:
    def __init__(self): creds=get_creds(); self.service=build('calendar','v3',credentials=creds) if creds else None; self.cid=CALENDAR_ID
    def execute(self, act, d):
        if not self.service: return None
        if act=="create": self.service.events().insert(calendarId=self.cid, body={'summary':d['title'],'start':{'dateTime':d['start_iso']},'end':{'dateTime':d['end_iso']}}).execute(); return True
        if act=="list": return self.service.events().list(calendarId=self.cid, timeMin=d['time_min'], timeMax=d['time_max'], singleEvents=True, orderBy='startTime').execute().get('items', [])

class TaskService:
    def __init__(self, cid): self.db=get_db(); self.cid=str(cid)
    def add(self, i): self.db.collection('chats').document(self.cid).collection('tasks').add({"item":i,"status":"pendente"})
    def list_fmt(self):
        docs=self.db.collection('chats').document(self.cid).collection('tasks').where(filter=firestore.FieldFilter("status","==","pendente")).stream()
        ls=[d.to_dict()['item'] for d in docs]; return "📝 \n"+"\n".join([f"- {t}" for t in ls]) if ls else "✅ Nada."
    def complete(self, i):
        docs=self.db.collection('chats').document(self.cid).collection('tasks').where(filter=firestore.FieldFilter("status","==","pendente")).stream()
        for d in docs: 
            if i.lower() in d.to_dict()['item'].lower(): d.reference.update({"status":"concluido"}); return True
        return False

class FinanceService:
    def __init__(self, cid): self.db=get_db(); self.cid=str(cid)
    def add(self, a, c, i): self.db.collection('chats').document(self.cid).collection('expenses').add({"amount":float(a),"category":c,"item":i,"timestamp":datetime.now()})
    def report(self):
        now=datetime.now(); start=datetime(now.year,now.month,1); docs=self.db.collection('chats').document(self.cid).collection('expenses').where(filter=firestore.FieldFilter("timestamp",">=",start)).stream()
        tot=0; txt=""
        for d in docs: dt=d.to_dict(); tot+=dt['amount']; txt+=f"• R$ {format_currency(dt['amount'])} - {dt.get('item')}\n"
        return f"📊 Total: R$ {format_currency(tot)}\n{txt}" if txt else "💸 Nada."

# --- AI ---
if GEMINI_KEY: genai.configure(api_key=GEMINI_KEY)

def ask_gemini(text, chat_id, is_audio=False):
    hist = get_history(chat_id); now = datetime.now()
    user_p = "[Audio]" if is_audio else text
    
    sys = f"""SYSTEM: Jarvis. Data: {now.strftime('%d/%m %H:%M')}.
    1. Não repita o usuário.
    2. JSON Intents: 
       - agendar, consultar_agenda, add_task, list_tasks, complete_task, add_expense, finance_report
       - analyze_project (Use isso se o usuario pedir para ler/resumir arquivos de uma pasta JÁ listada ou nova)
       - conversa
    HISTÓRICO: {hist}
    USUÁRIO: "{user_p}"
    """
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        content = [text, sys] if is_audio else sys
        resp = json.loads(model.generate_content(content, generation_config={"response_mime_type": "application/json"}).text)
        if resp.get("intent") == "conversa" and resp.get("response","").strip().lower() == text.strip().lower():
            resp["response"] = "Entendi. O que deseja?"
        return resp
    except: return {"intent": "conversa", "response": "Erro IA."}

def analyze_folder_content(folder_name):
    drv = DriveService()
    # 1. Busca Pasta
    folder = drv.search_folder(folder_name)
    if not folder: return f"❌ Não encontrei nenhuma pasta com o nome '{folder_name}'. Verifique se compartilhou comigo."
    
    # 2. Lista Arquivos
    files = drv.list_files_in_folder(folder['id'])
    if not files: return f"📂 A pasta '{folder['name']}' está vazia."
    
    # 3. Lê (Lê até 2 arquivos de texto/pdf para análise profunda)
    txt_content = ""
    file_list_str = ""
    count = 0
    for f in files:
        file_list_str += f"- {f['name']}\n"
        if "folder" not in f['mimeType'] and count < 2: 
            content = drv.read_file(f['id'], f['mimeType'])
            if content: 
                txt_content += f"\n--- CONTEÚDO DE '{f['name']}' ---\n{content}\n"
                count += 1
    
    # 4. Envia para IA resumir
    prompt = f"O usuário abriu a pasta '{folder['name']}'.\nArquivos disponíveis:\n{file_list_str}\n\nConteúdo extraído:\n{txt_content}\n\nResuma o que tem nessa pasta e diga que está pronto para perguntas."
    return genai.GenerativeModel("gemini-2.0-flash").generate_content(prompt).text

# --- ROTAS ---
@app.get("/")
def home(): return {"status": "Jarvis V13 Online"}

@app.post("/telegram/webhook")
async def webhook(request: Request):
    try: data = await request.json()
    except: return "error"
    if "message" not in data: return "ok"
    
    msg = data["message"]
    chat_id = str(msg["chat"]["id"])
    msg_id = msg["message_id"]
    text = msg.get("text", "")

    # --- COMANDOS ESPECIAIS ---
    if text == "/reset":
        reset_memory(chat_id)
        send_telegram(chat_id, "🧠 Memória limpa.")
        return {"status": "reset"}

    # NOVO COMANDO: /pasta [nome]
    if text.startswith("/pasta") or text.startswith("/arquivos"):
        parts = text.split(" ", 1)
        if len(parts) < 2:
            send_telegram(chat_id, "📂 Qual pasta? Digite ex: /pasta Projeto Beta")
            return {"status": "ask_name"}
        
        folder_query = parts[1]
        drv = DriveService()
        folder = drv.search_folder(folder_query)
        
        if not folder:
            send_telegram(chat_id, f"❌ Não achei a pasta contendo '{folder_query}'.")
        else:
            files = drv.list_files_in_folder(folder['id'])
            if not files:
                send_telegram(chat_id, f"📂 A pasta '{folder['name']}' está vazia.")
            else:
                names = "\n".join([f"📄 {f['name']}" for f in files[:10]])
                resp_text = f"📂 **Pasta: {folder['name']}**\n\n{names}\n\n🔎 **O que você quer saber sobre esses arquivos?**"
                send_telegram(chat_id, resp_text)
                # Salva no histórico para a IA saber o contexto na próxima mensagem
                save_msg(chat_id, "model", f"Listei os arquivos da pasta {folder['name']}: {names}")
        
        return {"status": "folder_listed"}

    # --------------------------

    if check_is_processed(chat_id, msg_id): return {"status": "ignored"}

    ai_resp = None
    if "text" in msg:
        save_msg(chat_id, "user", text)
        ai_resp = ask_gemini(text, chat_id)
    elif "voice" in msg:
        save_msg(chat_id, "user", "[Audio]")
        path = download_voice(msg["voice"]["file_id"])
        if path:
            send_telegram(chat_id, "🎧...")
            myfile = genai.upload_file(path, mime_type="audio/ogg")
            ai_resp = ask_gemini(myfile, chat_id, is_audio=True)

    if ai_resp:
        intent = ai_resp.get("intent")
        cal=CalendarService(); tsk=TaskService(chat_id); fin=FinanceService(chat_id); resp=""

        if intent == "conversa": resp = ai_resp.get("response", "")
        elif intent == "agendar": resp = "✅ Agendado." if cal.execute("create", ai_resp) else "❌ Erro."
        elif intent == "consultar_agenda": ev=cal.execute("list", ai_resp); resp="📅 "+"\n".join([f"{e['summary']}" for e in ev]) if ev else "📅 Vazia."
        elif intent == "add_task": tsk.add(ai_resp["item"]); resp=f"📝 Add: {ai_resp['item']}"
        elif intent == "list_tasks": resp=tsk.list_fmt()
        elif intent == "complete_task": resp="✅ Feito." if tsk.complete(ai_resp["item"]) else "🔍 Não achei."
        
        elif intent == "add_expense":
            try:
                val = float(str(ai_resp["amount"]).replace(',', '.'))
                fin.add(val, ai_resp["category"], ai_resp["item"])
                resp = f"💸 Gasto: R$ {format_currency(val)}"
            except: resp = "❌ Erro valor."

        elif intent == "finance_report": resp=fin.report()
        
        elif intent == "analyze_project": 
            # Se a IA detectou intenção de analisar, usa o nome da pasta que ela extraiu
            folder_name = ai_resp.get("folder", "")
            if folder_name:
                send_telegram(chat_id, f"📂 Analisando '{folder_name}'...")
                resp = analyze_folder_content(folder_name)
            else:
                resp = "Qual pasta você quer analisar?"

        if resp:
            send_telegram(chat_id, resp)
            if intent not in ["consultar_agenda", "list_tasks", "analyze_project"]:
                save_msg(chat_id, "model", resp)

    return {"status": "ok"}