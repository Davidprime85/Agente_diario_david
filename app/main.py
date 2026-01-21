import os
import json
import requests
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import firestore # <--- Nova biblioteca
import google.generativeai as genai
from dotenv import load_dotenv

# --- CONFIGURAÇÕES ---
load_dotenv()
app = FastAPI(title="Agente Jarvis", version="1.0.0 (Memory)")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

# --- CONEXÃO COM BANCO DE DADOS (FIRESTORE) ---
def get_firestore_client():
    firebase_env = os.environ.get("FIREBASE_CREDENTIALS")
    creds = None
    
    if firebase_env:
        # Nuvem (Vercel)
        cred_info = json.loads(firebase_env)
        creds = service_account.Credentials.from_service_account_info(cred_info)
    else:
        # Local (PC)
        key_path = "firebase-key.json"
        if os.path.exists(key_path):
            creds = service_account.Credentials.from_service_account_file(key_path)
            
    if creds:
        return firestore.Client(credentials=creds)
    return None

# --- GERENCIAMENTO DE MEMÓRIA ---
def get_chat_history(chat_id, limit=5):
    """Busca as últimas mensagens do banco para dar contexto"""
    db = get_firestore_client()
    if not db: return ""
    
    # Acessa a coleção 'chats', documento do usuário, subcoleção 'mensagens'
    # Ordena por data decrescente (mais recentes) e pega 'limit'
    docs = db.collection('chats').document(str(chat_id)).collection('mensagens')\
             .order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit).stream()
    
    history_list = []
    for doc in docs:
        data = doc.to_dict()
        history_list.append(f"{data['role']}: {data['content']}")
    
    # Inverte para ficar na ordem cronológica (Antiga -> Nova)
    return "\n".join(reversed(history_list))

def save_chat_message(chat_id, role, content):
    """Salva uma mensagem no banco"""
    db = get_firestore_client()
    if not db: return
    
    data = {
        "role": role, # 'user' ou 'model'
        "content": content,
        "timestamp": datetime.now()
    }
    # Salva na subcoleção 'mensagens'
    db.collection('chats').document(str(chat_id)).collection('mensagens').add(data)

# --- GEMINI CÉREBRO ---
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

def get_system_prompt():
    now = datetime.now()
    dias = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    dia_semana = dias[now.weekday()]
    data_formatada = f"{now.strftime('%Y-%m-%d %H:%M')} ({dia_semana})"
    
    return f"""
    SYSTEM: Você é um Assistente Pessoal Inteligente.
    Data atual: {data_formatada}.
    
    REGRAS:
    1. Use o HISTÓRICO DE CONVERSA abaixo para lembrar do contexto (nome do usuário, assuntos anteriores).
    2. Se for AGENDAR: Retorne JSON {{ "intent": "agendar", "title": "...", "start_iso": "...", "end_iso": "..." }}
    3. Se for LER AGENDA: Retorne JSON {{ "intent": "consultar", "time_min": "...", "time_max": "..." }}
    4. Se for CONVERSA: Retorne JSON {{ "intent": "conversa", "response": "..." }}
    """

def ask_gemini(text_input, chat_id, is_audio=False):
    if not GEMINI_KEY: return None
    
    # 1. Busca memória
    history = get_chat_history(chat_id)
    
    # 2. Monta o prompt com memória
    system_instruction = get_system_prompt()
    full_prompt = f"{system_instruction}\n\nHISTÓRICO RECENTE:\n{history}\n\nUSUÁRIO ATUAL:\n{text_input}"
    
    model = genai.GenerativeModel("gemini-2.0-flash")
    
    try:
        # Se for áudio, precisamos mandar o arquivo + prompt de texto
        content = [text_input, full_prompt] if is_audio else full_prompt
        
        response = model.generate_content(content, generation_config={"response_mime_type": "application/json"})
        result = json.loads(response.text)
        
        # Salva o que o usuário disse
        user_msg = "[Audio Enviado]" if is_audio else text_input
        save_chat_message(chat_id, "user", str(user_msg))
        
        # Salva o que a IA respondeu (se for conversa simples)
        if result.get("intent") == "conversa":
            save_chat_message(chat_id, "model", result["response"])
        elif result.get("intent") == "agendar":
            save_chat_message(chat_id, "model", f"Agendei: {result.get('title')}")
            
        return result
    except Exception as e:
        print(f"❌ Erro IA: {e}")
        return None

# --- CALENDAR & TOOLS ---
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
            body = {
                'summary': data['title'], 'description': data.get('description', ''),
                'start': {'dateTime': data['start_iso'], 'timeZone': 'America/Sao_Paulo'},
                'end': {'dateTime': data['end_iso'], 'timeZone': 'America/Sao_Paulo'}
            }
            service.events().insert(calendarId=self.calendar_id, body=body).execute()
            return True
            
        elif action == "list":
            tmin, tmax = data['time_min'], data['time_max']
            if not tmin.endswith("Z"): tmin += "-03:00"
            if not tmax.endswith("Z"): tmax += "-03:00"
            events = service.events().list(calendarId=self.calendar_id, timeMin=tmin, timeMax=tmax, singleEvents=True, orderBy='startTime').execute().get('items', [])
            if not events: return "📅 Nada agendado."
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
    
    # 1. Processa Entrada
    if "text" in msg:
        print(f"📩 Texto: {msg['text']}")
        ai_resp = ask_gemini(msg['text'], chat_id, is_audio=False)
    elif "voice" in msg:
        print("🎙️ Voz recebida")
        path = download_telegram_voice(msg["voice"]["file_id"])
        if path:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "🎧 Ouvindo..."})
            myfile = genai.upload_file(path, mime_type="audio/ogg")
            ai_resp = ask_gemini(myfile, chat_id, is_audio=True)

    # 2. Executa Ação
    if ai_resp:
        intent = ai_resp.get("intent")
        cal = CalendarService()
        
        if intent == "conversa":
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": ai_resp["response"]})
            
        elif intent == "agendar":
            if cal.execute("create", ai_resp):
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": f"✅ Agendado: {ai_resp['title']}"})
            else:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "❌ Falha ao agendar."})
                
        elif intent == "consultar":
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "🔍 Consultando..."})
            resp = cal.execute("list", ai_resp)
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": resp})
            save_chat_message(chat_id, "model", resp) # Salva o que a IA leu da agenda

    return {"status": "ok"}