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

# --- CONFIGURAÇÕES GERAIS ---
load_dotenv()

app = FastAPI(title="Jarvis Full System", version="10.0.0 (Restored + Fix)")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

# --- GERENCIAMENTO DE CONEXÕES (Google) ---
class GoogleServiceBase:
    """
    Classe central para autenticação.
    Gerencia credenciais do Drive, Calendar e Firestore.
    """
    def __init__(self):
        self.creds = None
        # Escopos completos restaurados
        scopes = [
            'https://www.googleapis.com/auth/calendar',
            'https://www.googleapis.com/auth/drive.readonly',
            'https://www.googleapis.com/auth/datastore'
        ]
        
        env_creds = os.environ.get("FIREBASE_CREDENTIALS")
        if env_creds:
            self.creds = service_account.Credentials.from_service_account_info(
                json.loads(env_creds), scopes=scopes
            )
        elif os.path.exists("firebase-key.json"):
            self.creds = service_account.Credentials.from_service_account_file(
                "firebase-key.json", scopes=scopes
            )

    def get_firestore_client(self):
        return firestore.Client(credentials=self.creds) if self.creds else None

# --- AUXILIARES (Utils) ---
def format_currency(value):
    return f"{value:.2f}".replace('.', ',')

def get_month_name(month_int):
    meses = {1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril', 5: 'Maio', 6: 'Junho',
             7: 'Julho', 8: 'Agosto', 9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'}
    return meses.get(month_int, 'Mês Atual')

def send_telegram(chat_id, text):
    if TELEGRAM_TOKEN:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})

def download_telegram_voice(file_id):
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}")
    path_info = r.json().get("result", {}).get("file_path")
    if not path_info: return None
    
    file_content = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path_info}").content
    local_path = "/tmp/voice.ogg"
    with open(local_path, "wb") as f: f.write(file_content)
    return local_path

# --- MEMÓRIA, SEGURANÇA E PROTEÇÃO ---
def check_is_processed(chat_id, message_id):
    """
    VACINA ANTI-LOOP: Verifica se a mensagem já foi processada antes.
    Retorna True se for repetida (deve ser ignorada).
    """
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if not db: return False
    
    doc_ref = db.collection('chats').document(str(chat_id)).collection('processed_ids').document(str(message_id))
    
    if doc_ref.get().exists:
        return True # Já processamos!
    
    # Marca como processada
    doc_ref.set({"timestamp": datetime.now()})
    return False

def reset_memory(chat_id):
    """Limpa histórico bugado (Comando /reset)"""
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if not db: return
    # Deleta as últimas 50 mensagens para garantir
    msgs = db.collection('chats').document(str(chat_id)).collection('mensagens').limit(50).stream()
    for m in msgs: m.reference.delete()

def save_chat_message(chat_id, role, content):
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if db:
        # Garante que usuário existe (Fix Fantasma)
        db.collection('chats').document(str(chat_id)).set({"last_active": datetime.now()}, merge=True)
        # Salva mensagem
        db.collection('chats').document(str(chat_id)).collection('mensagens').add({
            "role": role, "content": content, "timestamp": datetime.now()
        })

def get_chat_history(chat_id, limit=5):
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if not db: return ""
    docs = db.collection('chats').document(str(chat_id)).collection('mensagens')\
             .order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit).stream()
    
    # Monta histórico invertido
    history = []
    for doc in docs:
        d = doc.to_dict()
        history.append(f"{d['role']}: {d['content']}")
    return "\n".join(reversed(history))

# --- SERVIÇOS RESTAURADOS (COMPLETOS) ---

class CalendarService(GoogleServiceBase):
    def __init__(self):
        super().__init__()
        self.calendar_id = CALENDAR_ID

    def execute(self, action, data):
        if not self.creds: return None
        service = build('calendar', 'v3', credentials=self.creds)
        
        if action == "create":
            body = {'summary': data['title'], 'description': data.get('description', ''), 
                    'start': {'dateTime': data['start_iso']}, 'end': {'dateTime': data['end_iso']}}
            service.events().insert(calendarId=self.calendar_id, body=body).execute()
            return True
        elif action == "list":
            tmin, tmax = data['time_min'], data['time_max']
            if not tmin.endswith("Z"): tmin += "-03:00"
            if not tmax.endswith("Z"): tmax += "-03:00"
            return service.events().list(calendarId=self.calendar_id, timeMin=tmin, timeMax=tmax, 
                                       singleEvents=True, orderBy='startTime').execute().get('items', [])
        return None

class DriveService(GoogleServiceBase):
    def list_files_in_folder(self, folder_name):
        if not self.creds: return []
        service = build('drive', 'v3', credentials=self.creds)
        
        # Busca pasta
        res = service.files().list(q=f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false", fields="files(id)").execute()
        folders = res.get('files', [])
        if not folders: return None
        folder_id = folders[0]['id']
        
        # Busca arquivos dentro
        res_files = service.files().list(q=f"'{folder_id}' in parents", fields="files(id, name, mimeType)").execute()
        return res_files.get('files', [])

    def read_file_content(self, file_id, mime_type):
        try:
            service = build('drive', 'v3', credentials=self.creds)
            if "google-apps.document" in mime_type:
                req = service.files().export_media(fileId=file_id, mimeType='text/plain')
            else:
                req = service.files().get_media(fileId=file_id)
            
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, req)
            done = False
            while not done: _, done = downloader.next_chunk()
            return fh.getvalue().decode('utf-8', errors='ignore')[:3000] # Limite aumentado
        except Exception as e: return f"[Erro: {str(e)}]"

class TaskService(GoogleServiceBase):
    def __init__(self, chat_id):
        super().__init__()
        self.db = self.get_firestore_client()
        self.chat_id = str(chat_id)
        if self.db: self.db.collection('chats').document(self.chat_id).set({"last_active": datetime.now()}, merge=True)

    def add_task(self, item):
        if not self.db: return False
        self.db.collection('chats').document(self.chat_id).collection('tasks').add({"item": item, "status": "pendente"})
        return True

    def list_tasks_formatted(self):
        if not self.db: return ""
        docs = self.db.collection('chats').document(self.chat_id).collection('tasks').where(filter=firestore.FieldFilter("status", "==", "pendente")).stream()
        tasks = [d.to_dict()['item'] for d in docs]
        return "📝 **Pendências:**\n" + "\n".join([f"• {t}" for t in tasks]) if tasks else "✅ Nada pendente."

    def complete_task(self, item):
        if not self.db: return False
        docs = self.db.collection('chats').document(self.chat_id).collection('tasks').where(filter=firestore.FieldFilter("status", "==", "pendente")).stream()
        for d in docs:
            if item.lower() in d.to_dict()['item'].lower():
                d.reference.update({"status": "concluido"})
                return True
        return False

class FinanceService(GoogleServiceBase):
    def __init__(self, chat_id):
        super().__init__()
        self.db = self.get_firestore_client()
        self.chat_id = str(chat_id)
        if self.db: self.db.collection('chats').document(self.chat_id).set({"last_active": datetime.now()}, merge=True)

    def add_expense(self, amount, category, item):
        if not self.db: return False
        self.db.collection('chats').document(self.chat_id).collection('expenses').add({
            "amount": float(amount), "category": category, "item": item, "timestamp": datetime.now()
        })
        return True

    def get_monthly_report(self):
        if not self.db: return "Erro DB"
        now = datetime.now()
        start = datetime(now.year, now.month, 1)
        docs = self.db.collection('chats').document(self.chat_id).collection('expenses').where(filter=firestore.FieldFilter("timestamp", ">=", start)).stream()
        
        total = 0.0
        txt = ""
        for d in docs:
            data = d.to_dict()
            total += data['amount']
            txt += f"• R$ {format_currency(data['amount'])} ({data.get('category')}) - {data.get('item')}\n"
        
        return f"📊 **Gastos de {get_month_name(now.month)}:**\n\n{txt}\n💰 **TOTAL: R$ {format_currency(total)}**" if txt else "💸 Sem gastos."

# --- INTELIGÊNCIA (GEMINI - CÉREBRO) ---
if GEMINI_KEY: genai.configure(api_key=GEMINI_KEY)

def analyze_project_folder(folder_name):
    drive = DriveService()
    files = drive.list_files_in_folder(folder_name)
    if not files: return "📂 Pasta vazia ou não encontrada."
    
    # Lê conteúdo (apenas 1 arquivo por vez para evitar timeout do Vercel)
    content = ""
    for f in files[:1]:
        text = drive.read_file_content(f['id'], f['mimeType'])
        content += f"\nARQUIVO: {f['name']}\nCONTEÚDO: {text[:2000]}...\n"

    prompt = f"Analise este projeto da pasta '{folder_name}':\n{content}\nResuma status e próximos passos."
    return genai.GenerativeModel("gemini-2.0-flash").generate_content(prompt).text

def ask_gemini(text_input, chat_id, is_audio=False):
    history = get_chat_history(chat_id)
    now = datetime.now()
    dia = ['Seg','Ter','Qua','Qui','Sex','Sab','Dom'][now.weekday()]
    
    # Se for áudio, substituímos o texto por placeholder para a IA saber
    user_prompt_clean = "[Audio Enviado]" if is_audio else text_input

    # PROMPT COMPLETO E ROBUSTO (RESTAURADO)
    system_prompt = f"""
    SYSTEM: Você é o Jarvis, um assistente pessoal inteligente e prestativo.
    Data: {now.strftime('%d/%m/%Y %H:%M')} ({dia}).
    
    IMPORTANTE:
    1. NÃO REPITA o que o usuário disse. Se ele disser "Oi", responda "Olá! Como ajudo?".
    2. Se o usuário perguntar "Quem sou eu?", verifique se sabe o nome dele pelo histórico ou responda que ainda não sabe.
    3. Analise a intenção e retorne APENAS JSON.
    
    INTENÇÕES DISPONÍVEIS:
    - agendar, consultar_agenda, add_task, list_tasks, complete_task
    - add_expense, finance_report
    - analyze_project (Para ler Drive)
    - conversa (Para papo furado ou perguntas gerais)
    
    HISTÓRICO RECENTE:
    {history}
    
    USUÁRIO DISSE:
    "{user_prompt_clean}"
    """
    
    model = genai.GenerativeModel("gemini-2.0-flash")
    
    try:
        # Envia Prompt
        parts = [text_input, system_prompt] if is_audio else system_prompt
        resp = model.generate_content(parts, generation_config={"response_mime_type": "application/json"})
        result = json.loads(resp.text)
        
        # --- TRAVA DE SEGURANÇA (ANTI-PAPAGAIO NO CÓDIGO) ---
        # Se a IA alucinar e devolver o mesmo texto que o usuário mandou, nós mudamos na força bruta.
        if result.get("intent") == "conversa":
            ai_resp = result.get("response", "").strip().lower()
            user_resp = text_input.strip().lower()
            # Se forem iguais (papagaio), força resposta padrão
            if ai_resp == user_resp or not ai_resp:
                result["response"] = "Entendi. Como posso ajudar com sua agenda ou projetos hoje?"
        # -----------------------------------------------------
        
        return result
    except Exception as e:
        print(f"Erro IA: {e}")
        return {"intent": "conversa", "response": "Tive um erro interno, pode repetir?"}

def generate_morning_message(evs, tasks):
    prompt = f"Escreva um Bom Dia motivacional. Agenda: {evs}. Tarefas: {tasks}."
    return genai.GenerativeModel("gemini-2.0-flash").generate_content(prompt).text

# --- ROTAS (ENDPOINTS) ---

@app.get("/")
def home(): return {"status": "Jarvis Online 🟢", "version": "10.0.0"}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    # Dashboard Visual Completo Restaurado
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if not db: return "Erro DB"
    docs = db.collection('chats').stream()
    
    html = """<html><head><title>Jarvis Dashboard</title>
    <style>body{font-family:sans-serif;padding:20px;background:#f0f2f5} .card{background:white;padding:20px;margin-bottom:20px;border-radius:10px;box-shadow:0 2px 4px rgba(0,0,0,0.1)} 
    table{width:100%;border-collapse:collapse;margin-top:10px} th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left} th{background:#007bff;color:white} 
    .total{color:green;font-weight:bold;margin-top:10px;text-align:right}</style></head><body><h1>📊 Painel Financeiro</h1>"""
    
    for doc in docs:
        cid = doc.id
        now = datetime.now()
        start = datetime(now.year, now.month, 1)
        exps = db.collection('chats').document(cid).collection('expenses').where(filter=firestore.FieldFilter("timestamp", ">=", start)).stream()
        
        total = 0; rows = ""; has = False
        for e in exps:
            d = e.to_dict(); total += d['amount']; has = True
            rows += f"<tr><td>{d['timestamp'].strftime('%d/%m')}</td><td>{d.get('item')}</td><td>{d.get('category')}</td><td>R$ {format_currency(d['amount'])}</td></tr>"
        
        if has: html += f"<div class='card'><h2>User: {cid}</h2><table><tr><th>Data</th><th>Item</th><th>Categ</th><th>Valor</th></tr>{rows}</table><div class='total'>Total: R$ {format_currency(total)}</div></div>"
    
    return html + "</body></html>"

@app.get("/cron/bom-dia")
def cron_bom_dia():
    base = GoogleServiceBase(); db = base.get_firestore_client()
    if not db: return {"err": "db"}
    docs = db.collection('chats').stream(); count = 0
    now = datetime.now()
    tmin = now.strftime("%Y-%m-%dT00:00:00"); tmax = now.strftime("%Y-%m-%dT23:59:59")
    
    cal = CalendarService()
    for doc in docs:
        cid = doc.id
        evs = cal.execute("list", {"time_min": tmin, "time_max": tmax})
        ev_txt = ", ".join([e['summary'] for e in evs]) if evs else "Nada"
        
        tsk = TaskService(cid)
        tk_txt = tsk.list_tasks_formatted()
        
        msg = generate_morning_message(ev_txt, tk_txt)
        send_telegram(cid, msg); count += 1
    return {"sent": count}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    try: data = await request.json()
    except: return "error"
    if "message" not in data: return "ok"
    
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    msg_id = msg["message_id"]
    text = msg.get("text", "")

    # 1. COMANDO DE RESET (EMERGÊNCIA)
    if text == "/reset":
        reset_memory(chat_id)
        send_telegram(chat_id, "🧠 Memória COMPLETAMENTE limpa! Tente falar agora.")
        return {"status": "reset"}

    # 2. VACINA ANTI-LOOP
    if check_is_processed(chat_id, msg_id):
        return {"status": "ignored"}

    ai_resp = None
    
    # Processa Entrada
    if "text" in msg:
        save_chat_message(chat_id, "user", text)
        ai_resp = ask_gemini(text, chat_id)
        
    elif "voice" in msg:
        save_chat_message(chat_id, "user", "[Audio]")
        path = download_telegram_voice(msg["voice"]["file_id"])
        if path:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "🎧 Ouvindo..."})
            myfile = genai.upload_file(path, mime_type="audio/ogg")
            ai_resp = ask_gemini(myfile, chat_id, is_audio=True)

    # Executa Ação
    if ai_resp:
        intent = ai_resp.get("intent")
        cal = CalendarService()
        tsk = TaskService(chat_id)
        fin = FinanceService(chat_id)
        resp = ""
        
        if intent == "conversa": resp = ai_resp["response"]
        elif intent == "agendar":
            if cal.execute("create", ai_resp): resp = f"✅ Agendado: {ai_resp['title']}"
            else: resp = "❌ Erro ao agendar."
        elif intent == "consultar_agenda":
            events = cal.execute("list", ai_resp)
            resp = "📅 " + "\n".join([f"{e['start'].get('dateTime')[11:16]} {e['summary']}" for e in events]) if events else "📅 Agenda vazia."
        elif intent == "add_task":
            tsk.add_task(ai_resp["item"]); resp = f"📝 Anotado: {ai_resp['item']}"
        elif intent == "list_tasks": resp = tsk.list_tasks_formatted()
        elif intent == "complete_task":
            resp = "✅ Feito." if tsk.complete_task(ai_resp["item"]) else "🔍 Tarefa não encontrada."
        elif intent == "add_expense":
            fin.add_expense(ai_resp["amount"], ai_resp["category"], ai_resp["item"])
            resp = f"💸 Gasto: R$ {format_currency(ai_resp['amount'])}"
        elif intent == "finance_report": resp = fin.get_monthly_report()
        elif intent == "analyze_project":
            send_telegram(chat_id, f"📂 Lendo Drive: {ai_resp['folder']}...")
            resp = analyze_project_folder(ai_resp["folder"])

        if resp:
            send_telegram(chat_id, resp)
            if intent not in ["consultar_agenda", "list_tasks", "finance_report", "analyze_project"]:
                save_chat_message(chat_id, "model", resp)

    return {"status": "ok"}