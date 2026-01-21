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

# --- CONFIGURAÇÕES E VARIÁVEIS DE AMBIENTE ---
load_dotenv()

app = FastAPI(title="Jarvis Full System", version="9.0.0 (Ultimate + Fix)")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

# --- GERENCIADOR DE CREDENCIAIS (UNIFICADO) ---
class GoogleServiceBase:
    """
    Classe base para autenticar todos os serviços do Google (Drive, Calendar, Firestore).
    Lida tanto com ambiente local (arquivo json) quanto nuvem (variável de ambiente).
    """
    def __init__(self):
        self.creds = None
        # Escopos necessários: Calendar, Drive (Leitura), Firestore (Datastore)
        scopes = [
            'https://www.googleapis.com/auth/calendar',
            'https://www.googleapis.com/auth/drive.readonly',
            'https://www.googleapis.com/auth/datastore'
        ]
        
        env_creds = os.environ.get("FIREBASE_CREDENTIALS")
        
        if env_creds:
            # Caso esteja rodando na Nuvem (Vercel)
            cred_info = json.loads(env_creds)
            self.creds = service_account.Credentials.from_service_account_info(
                cred_info, scopes=scopes
            )
        elif os.path.exists("firebase-key.json"):
            # Caso esteja rodando no PC (Local)
            self.creds = service_account.Credentials.from_service_account_file(
                "firebase-key.json", scopes=scopes
            )

    def get_firestore_client(self):
        """Retorna o cliente do Banco de Dados"""
        return firestore.Client(credentials=self.creds) if self.creds else None

# --- FUNÇÕES AUXILIARES (HELPERS) ---
def format_currency(value):
    """Formata float 10.5 para string '10,50'"""
    return f"{value:.2f}".replace('.', ',')

def get_month_name(month_int):
    """Converte número do mês em Nome (PT-BR)"""
    meses = {
        1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril', 5: 'Maio', 6: 'Junho',
        7: 'Julho', 8: 'Agosto', 9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
    }
    return meses.get(month_int, 'Mês Atual')

def send_telegram(chat_id, text):
    """Envia mensagem de texto para o Telegram"""
    if TELEGRAM_TOKEN:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        requests.post(url, json=payload)

def download_telegram_voice(file_id):
    """Baixa o áudio do Telegram e salva temporariamente"""
    # 1. Pega o caminho do arquivo
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}")
    file_path_info = r.json().get("result", {}).get("file_path")
    
    if not file_path_info:
        return None
    
    # 2. Baixa o conteúdo binário
    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path_info}"
    file_content = requests.get(download_url).content
    
    # 3. Salva no /tmp
    local_path = "/tmp/voice.ogg"
    with open(local_path, "wb") as f:
        f.write(file_content)
        
    return local_path

# --- MEMÓRIA, HISTÓRICO E PROTEÇÃO (ANTI-LOOP) ---

def check_is_processed(chat_id, message_id):
    """
    [CRÍTICO] Vacina Anti-Loop.
    Verifica no banco de dados se esta mensagem específica (ID) já foi respondida.
    Se já foi, retorna True para o bot ignorar e não responder de novo.
    """
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if not db: return False
    
    # Salva o ID da mensagem numa subcoleção separada 'processed_ids'
    doc_ref = db.collection('chats').document(str(chat_id)).collection('processed_ids').document(str(message_id))
    
    if doc_ref.get().exists:
        # Mensagem já existe no banco = Já processamos antes
        return True
    
    # Se não existe, cria agora (marca como processada)
    doc_ref.set({"timestamp": datetime.now()})
    return False

def reset_memory(chat_id):
    """
    Função de Emergência: Limpa o histórico de conversa do usuário
    para caso o bot fique 'preso' repetindo coisas.
    """
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if not db: return
    
    # Busca todas as mensagens e deleta
    msgs = db.collection('chats').document(str(chat_id)).collection('mensagens').stream()
    for m in msgs:
        m.reference.delete()

def save_chat_message(chat_id, role, content):
    """Salva a conversa no Firestore"""
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if db:
        # Garante que o usuário existe na coleção principal (Correção do Bug Fantasma)
        user_ref = db.collection('chats').document(str(chat_id))
        user_ref.set({"last_active": datetime.now()}, merge=True)
        
        # Salva a mensagem na subcoleção
        user_ref.collection('mensagens').add({
            "role": role, 
            "content": content, 
            "timestamp": datetime.now()
        })

def get_chat_history(chat_id, limit=5):
    """Recupera as últimas mensagens para dar contexto à IA"""
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if not db: return ""
    
    docs = db.collection('chats').document(str(chat_id)).collection('mensagens')\
             .order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit).stream()
    
    history_list = []
    for doc in docs:
        data = doc.to_dict()
        history_list.append(f"{data['role']}: {data['content']}")
    
    # Inverte para ordem cronológica (Antiga -> Nova)
    return "\n".join(reversed(history_list))

# --- SERVIÇOS ESPECÍFICOS (CALENDAR, DRIVE, TASKS, FINANCE) ---

class CalendarService(GoogleServiceBase):
    def __init__(self):
        super().__init__()
        self.calendar_id = CALENDAR_ID

    def execute(self, action, data):
        if not self.creds: return None
        service = build('calendar', 'v3', credentials=self.creds)
        
        if action == "create":
            body = {
                'summary': data['title'], 
                'description': data.get('description', ''), 
                'start': {'dateTime': data['start_iso']}, 
                'end': {'dateTime': data['end_iso']}
            }
            service.events().insert(calendarId=self.calendar_id, body=body).execute()
            return True
            
        elif action == "list":
            tmin, tmax = data['time_min'], data['time_max']
            # Ajuste de fuso horário simples se não vier com Z
            if not tmin.endswith("Z"): tmin += "-03:00"
            if not tmax.endswith("Z"): tmax += "-03:00"
            
            events_result = service.events().list(
                calendarId=self.calendar_id, 
                timeMin=tmin, timeMax=tmax, 
                singleEvents=True, orderBy='startTime'
            ).execute()
            
            return events_result.get('items', [])
        return None

class DriveService(GoogleServiceBase):
    """Serviço para ler arquivos do Google Drive"""
    def list_files_in_folder(self, folder_name):
        if not self.creds: return []
        service = build('drive', 'v3', credentials=self.creds)
        
        # 1. Busca Pasta pelo nome
        results = service.files().list(
            q=f"mimeType='application/vnd.google-apps.folder' and name = '{folder_name}' and trashed = false",
            fields="files(id, name)"
        ).execute()
        folders = results.get('files', [])
        
        if not folders: return None # Pasta não encontrada
        folder_id = folders[0]['id']
        
        # 2. Lista arquivos de texto/pdf dentro dela
        files_res = service.files().list(
            q=f"'{folder_id}' in parents and (mimeType contains 'text/' or mimeType = 'application/pdf' or mimeType = 'application/vnd.google-apps.document')",
            fields="files(id, name, mimeType)"
        ).execute()
        
        return files_res.get('files', [])

    def read_file_content(self, file_id, mime_type):
        service = build('drive', 'v3', credentials=self.creds)
        content = ""
        try:
            if "google-apps.document" in mime_type:
                request = service.files().export_media(fileId=file_id, mimeType='text/plain')
            else:
                request = service.files().get_media(fileId=file_id)
                
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False: 
                status, done = downloader.next_chunk()
                
            content = fh.getvalue().decode('utf-8', errors='ignore')
            # Limita tamanho para não estourar memória
            return content[:2500] 
        except Exception as e:
            return f"[Erro ao ler arquivo: {str(e)}]"

class TaskService(GoogleServiceBase):
    def __init__(self, chat_id):
        super().__init__()
        self.db = self.get_firestore_client()
        self.chat_id = str(chat_id)
        # Garante registro
        if self.db: self.db.collection('chats').document(self.chat_id).set({"last_active": datetime.now()}, merge=True)

    def add_task(self, item):
        if not self.db: return False
        self.db.collection('chats').document(self.chat_id).collection('tasks').add({
            "item": item, "status": "pendente", "created_at": datetime.now()
        })
        return True

    def list_tasks_raw(self):
        if not self.db: return []
        docs = self.db.collection('chats').document(self.chat_id).collection('tasks')\
                 .where(filter=firestore.FieldFilter("status", "==", "pendente")).stream()
        return [doc.to_dict()['item'] for doc in docs]
    
    def list_tasks_formatted(self):
        tasks = self.list_tasks_raw()
        if not tasks: return "✅ Nenhuma pendência."
        return "📝 **Pendências:**\n" + "\n".join([f"• {t}" for t in tasks])

    def complete_task(self, item_name):
        if not self.db: return False
        docs = self.db.collection('chats').document(self.chat_id).collection('tasks')\
                 .where(filter=firestore.FieldFilter("status", "==", "pendente")).stream()
        found = False
        for doc in docs:
            # Busca parcial (ex: "luz" encontra "pagar conta de luz")
            if item_name.lower() in doc.to_dict()['item'].lower():
                doc.reference.update({"status": "concluido"})
                found = True
        return found

class FinanceService(GoogleServiceBase):
    def __init__(self, chat_id):
        super().__init__()
        self.db = self.get_firestore_client()
        self.chat_id = str(chat_id)
        if self.db: self.db.collection('chats').document(self.chat_id).set({"last_active": datetime.now()}, merge=True)

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
        if not self.db: return "Erro Banco"
        now = datetime.now()
        start_date = datetime(now.year, now.month, 1)
        
        docs = self.db.collection('chats').document(self.chat_id).collection('expenses')\
                 .where(filter=firestore.FieldFilter("timestamp", ">=", start_date)).stream()
        
        total = 0.0
        details = ""
        for doc in docs:
            d = doc.to_dict()
            val = d.get('amount', 0)
            total += val
            details += f"• R$ {format_currency(val)} ({d.get('category')}) - {d.get('item')}\n"
            
        if total == 0: return "💸 Sem gastos este mês."
        
        month_name = get_month_name(now.month)
        return f"📊 **Gastos de {month_name}:**\n\n{details}\n💰 **TOTAL: R$ {format_currency(total)}**"

# --- INTELIGÊNCIA ARTIFICIAL (GEMINI) ---
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

def analyze_project_folder(folder_name):
    """Função que orquestra a leitura do Drive + Análise da IA"""
    drive = DriveService()
    files = drive.list_files_in_folder(folder_name)
    
    if files is None: 
        return f"❌ Pasta '{folder_name}' não encontrada. Verifique o compartilhamento."
    if not files: 
        return f"📂 A pasta '{folder_name}' está vazia."
    
    docs_content = ""
    # Lê apenas 1 arquivo por vez nesta versão segura para evitar timeout
    for f in files[:1]:
        content = drive.read_file_content(f['id'], f['mimeType'])
        docs_content += f"\n--- ARQUIVO: {f['name']} ---\n{content}\n"
        
    prompt = f"""
    Atue como Gerente de Projetos. Analise os documentos da pasta '{folder_name}':
    {docs_content}
    
    SAÍDA ESPERADA:
    1. Resumo do status atual.
    2. Próximos passos críticos.
    3. Sugestão de agenda.
    """
    model = genai.GenerativeModel("gemini-2.0-flash")
    return model.generate_content(prompt).text

def ask_gemini(text_input, chat_id, is_audio=False):
    history = get_chat_history(chat_id)
    now = datetime.now()
    dia_sem = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sab', 'Dom'][now.weekday()]
    
    # Se for áudio, substituímos o texto do usuário por um placeholder para não enviar binário no prompt textual
    user_prompt_text = "[Audio Enviado]" if is_audio else text_input
    
    system_prompt = f"""
    SYSTEM: Jarvis (Assistente Pessoal). Data: {now.strftime('%Y-%m-%d %H:%M')} ({dia_sem}).
    
    INTENÇÕES JSON (Escolha a melhor):
    1. AGENDAR: {{ "intent": "agendar", "title": "Titulo", "start_iso": "YYYY-MM-DDTHH:MM:SS", "end_iso": "..." }}
    2. LER AGENDA: {{ "intent": "consultar_agenda", "time_min": "...", "time_max": "..." }}
    3. ADD TAREFA: {{ "intent": "add_task", "item": "Descrição" }}
    4. LISTAR TAREFAS: {{ "intent": "list_tasks" }}
    5. CONCLUIR TAREFA: {{ "intent": "complete_task", "item": "Trecho do nome" }}
    6. ADD GASTO: {{ "intent": "add_expense", "amount": 10.50, "category": "Alimentacao/Transporte/Contas", "item": "Descricao" }}
    7. RELATORIO FINANCEIRO: {{ "intent": "finance_report" }}
    8. ANALISAR PROJETO (Drive): {{ "intent": "analyze_project", "folder": "Nome Exato da Pasta" }}
    9. CONVERSA GERAL: {{ "intent": "conversa", "response": "Texto" }}
    
    HISTÓRICO RECENTE:
    {history}
    
    USUÁRIO ATUAL:
    {user_prompt_text}
    """
    
    model = genai.GenerativeModel("gemini-2.0-flash")
    
    try:
        # Se for áudio, a lista de conteúdo deve ter o arquivo de áudio + o texto do prompt
        content = [text_input, system_prompt] if is_audio else system_prompt
        
        resp = model.generate_content(content, generation_config={"response_mime_type": "application/json"})
        return json.loads(resp.text)
    except Exception as e:
        print(f"Erro Gemini: {e}")
        return None

def generate_morning_message(events_txt, tasks_txt):
    prompt = f"""
    Escreva um "Bom dia" motivacional para o David.
    Agenda: {events_txt}
    Pendências: {tasks_txt}
    Seja breve, animado e útil.
    """
    return genai.GenerativeModel("gemini-2.0-flash").generate_content(prompt).text

# --- ROTAS WEB (ENDPOINTS) ---

@app.get("/")
def home():
    return {"status": "Jarvis Online 🟢", "version": "9.0.0 Full"}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if not db: return "<h1>Erro Banco de Dados</h1>"
    
    docs = db.collection('chats').stream()
    
    html = """
    <html>
        <head>
            <title>Jarvis Dashboard</title>
            <style>
                body { font-family: sans-serif; padding: 20px; background: #f0f2f5; } 
                .card { background: white; padding: 20px; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); } 
                table { width: 100%; border-collapse: collapse; margin-top: 10px; } 
                th, td { padding: 10px; border-bottom: 1px solid #ddd; text-align: left; } 
                th { background: #007bff; color: white; } 
                .total { color: green; font-weight: bold; margin-top: 10px; text-align: right; font-size: 1.2em; }
                h1 { color: #333; }
            </style>
        </head>
        <body>
            <h1>📊 Painel Financeiro</h1>
    """
    
    for doc in docs:
        chat_id = doc.id
        now = datetime.now()
        start_date = datetime(now.year, now.month, 1)
        expenses = db.collection('chats').document(chat_id).collection('expenses').where(filter=firestore.FieldFilter("timestamp", ">=", start_date)).stream()
        
        total = 0.0
        rows = ""
        has_data = False
        
        for e in expenses:
            d = e.to_dict()
            val = d.get('amount', 0)
            total += val
            date_str = d['timestamp'].strftime('%d/%m')
            item = d.get('item', '')
            cat = d.get('category', '')
            rows += f"<tr><td>{date_str}</td><td>{item}</td><td>{cat}</td><td>R$ {format_currency(val)}</td></tr>"
            has_data = True
            
        if has_data:
            html += f"""
            <div class='card'>
                <h2>👤 Usuário: {chat_id}</h2>
                <table>
                    <tr><th>Data</th><th>Item</th><th>Categoria</th><th>Valor</th></tr>
                    {rows}
                </table>
                <div class='total'>Total Mês: R$ {format_currency(total)}</div>
            </div>
            """
            
    html += "</body></html>"
    return html

@app.get("/cron/bom-dia")
def cron_bom_dia():
    print("☀️ Bom dia Cron Iniciado")
    base = GoogleServiceBase()
    db = base.get_firestore_client()
    if not db: return {"error": "No DB"}
    
    # Itera sobre usuários ativos
    docs = db.collection('chats').stream()
    cal = CalendarService()
    now = datetime.now()
    tmin = now.strftime("%Y-%m-%dT00:00:00")
    tmax = now.strftime("%Y-%m-%dT23:59:59")
    
    count = 0
    for doc in docs:
        chat_id = doc.id
        
        # 1. Pega Agenda
        events = cal.execute("list", {"time_min": tmin, "time_max": tmax})
        ev_txt = "Nada."
        if isinstance(events, list) and events:
            ev_txt = "\n".join([f"- {e['summary']} ({e['start'].get('dateTime')[11:16]})" for e in events])
            
        # 2. Pega Tarefas
        task_service = TaskService(chat_id)
        tasks = task_service.list_tasks_raw()
        tk_txt = "Nada."
        if tasks: tk_txt = ", ".join(tasks)
        
        # 3. Gera texto e envia
        msg = generate_morning_message(ev_txt, tk_txt)
        send_telegram(chat_id, msg)
        count += 1
        
    return {"status": "ok", "sent": count}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except:
        return {"status": "error"}

    if "message" not in data:
        return {"status": "ok"}
    
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    message_id = msg["message_id"] # ID único da mensagem
    
    # ---------------------------------------------------------
    # 1. VACINA ANTI-LOOP (CRUCIAL)
    # Verifica se já processamos essa mensagem. Se sim, ignora.
    if check_is_processed(chat_id, message_id):
        print(f"🔄 Mensagem {message_id} duplicada ignorada.")
        return {"status": "ignored"}
    # ---------------------------------------------------------

    ai_resp = None
    
    # DOWNLOAD VOICE helper
    def get_voice_path(fid):
        return download_telegram_voice(fid)

    # 2. ENTRADA DE DADOS
    if "text" in msg:
        text = msg['text']
        
        # --- COMANDO DE EMERGÊNCIA ---
        if text == "/reset":
            reset_memory(chat_id)
            send_telegram(chat_id, "🧠 Memória limpa com sucesso! O loop deve parar.")
            return {"status": "reset"}
        # -----------------------------
        
        save_chat_message(chat_id, "user", text)
        ai_resp = ask_gemini(text, chat_id)
        
    elif "voice" in msg:
        save_chat_message(chat_id, "user", "[Audio]")
        path = get_voice_path(msg["voice"]["file_id"])
        if path:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "🎧 Ouvindo..."})
            myfile = genai.upload_file(path, mime_type="audio/ogg")
            ai_resp = ask_gemini(myfile, chat_id, is_audio=True)

    # 3. EXECUÇÃO DA INTENÇÃO
    if ai_resp:
        intent = ai_resp.get("intent")
        cal = CalendarService()
        tasks = TaskService(chat_id)
        fin = FinanceService(chat_id)
        resp_text = ""
        
        if intent == "conversa":
            resp_text = ai_resp["response"]
            
        elif intent == "agendar":
            if cal.execute("create", ai_resp): 
                resp_text = f"✅ Agendado: {ai_resp['title']}"
            else: 
                resp_text = "❌ Erro ao agendar."
            
        elif intent == "consultar_agenda":
            events = cal.execute("list", ai_resp)
            if not events: 
                resp_text = "📅 Nada na agenda."
            else:
                resp_text = "📅 **Agenda:**\n" + "\n".join([f"• {e['start'].get('dateTime')[11:16]} - {e['summary']}" for e in events])
                
        elif intent == "add_task":
            if tasks.add_task(ai_resp["item"]): 
                resp_text = f"📝 Anotado: {ai_resp['item']}"
            
        elif intent == "list_tasks":
            resp_text = tasks.list_tasks_formatted()
            
        elif intent == "complete_task":
            if tasks.complete_task(ai_resp["item"]): 
                resp_text = f"✅ Feito: {ai_resp['item']}"
            else: 
                resp_text = "🔍 Tarefa não encontrada."
            
        elif intent == "add_expense":
            val = float(ai_resp["amount"])
            if fin.add_expense(val, ai_resp["category"], ai_resp["item"]):
                resp_text = f"💸 Gasto: R$ {format_currency(val)} ({ai_resp['item']})"
                
        elif intent == "finance_report":
            resp_text = fin.get_monthly_report()
            
        elif intent == "analyze_project":
            folder = ai_resp.get("folder")
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": f"📂 Acessando Drive: '{folder}'..."})
            resp_text = analyze_project_folder(folder)

        # Envia Resposta
        if resp_text:
            send_telegram(chat_id, resp_text)
            
            # Não salvamos respostas de leitura de dados para não poluir o histórico com textos gigantes
            if intent not in ["consultar_agenda", "list_tasks", "finance_report", "analyze_project"]:
                save_chat_message(chat_id, "model", resp_text)

    return {"status": "ok"}