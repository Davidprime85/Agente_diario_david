import os
import json
import requests
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import google.generativeai as genai
from dotenv import load_dotenv

# --- CONFIGURAÇÕES ---
load_dotenv()
app = FastAPI(title="Agente Diario", version="0.7.0 (Audio)")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

# --- FUNÇÕES AUXILIARES ---
def get_current_date_prompt():
    now = datetime.now()
    return f"""
    Data atual: {now.strftime("%Y-%m-%d %H:%M")}.
    Instrução: Analise o input (texto ou audio).
    
    Se for agendar, responda APENAS JSON:
    {{ "intent": "agendar_reuniao", "title": "Titulo", "start_iso": "YYYY-MM-DDTHH:MM:SS", "end_iso": "YYYY-MM-DDTHH:MM:SS", "description": "Detalhes" }}
    
    Se for conversa, responda APENAS JSON:
    {{ "intent": "conversa", "response": "Sua resposta curta" }}
    """

def ask_gemini_text(text: str):
    if not GEMINI_KEY: return None
    model = genai.GenerativeModel("gemini-2.0-flash")
    prompt = get_current_date_prompt() + f'\nUsuario disse: "{text}"'
    
    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except Exception as e:
        print(f"❌ Erro IA Texto: {e}")
        return None

def ask_gemini_audio(file_path: str):
    if not GEMINI_KEY: return None
    model = genai.GenerativeModel("gemini-2.0-flash")
    
    # Upload do arquivo para o Google AI
    audio_file = genai.upload_file(file_path, mime_type="audio/ogg")
    
    prompt = get_current_date_prompt() + "\nO usuário enviou este áudio. Extraia a intenção dele."
    
    try:
        # Envia o áudio + prompt juntos
        response = model.generate_content([audio_file, prompt], generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except Exception as e:
        print(f"❌ Erro IA Audio: {e}")
        return None

def download_telegram_voice(file_id: str):
    # 1. Pega o caminho do arquivo
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}")
    file_path_info = r.json().get("result", {}).get("file_path")
    
    if not file_path_info: return None
    
    # 2. Baixa o binário
    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path_info}"
    voice_data = requests.get(download_url).content
    
    # 3. Salva no /tmp (pasta temporária do Vercel)
    local_path = "/tmp/voice.ogg"
    with open(local_path, "wb") as f:
        f.write(voice_data)
        
    return local_path

# --- CALENDAR SERVICE ---
class GoogleCalendarService:
    def __init__(self):
        self.creds = None
        self.calendar_id = CALENDAR_ID
        firebase_env = os.environ.get("FIREBASE_CREDENTIALS")
        
        if firebase_env:
            cred_info = json.loads(firebase_env)
            self.creds = service_account.Credentials.from_service_account_info(
                cred_info, scopes=['https://www.googleapis.com/auth/calendar']
            )
        else:
            key_path = "firebase-key.json"
            if os.path.exists(key_path):
                self.creds = service_account.Credentials.from_service_account_file(
                    key_path, scopes=['https://www.googleapis.com/auth/calendar']
                )

    def create_event(self, title, start, end, desc=""):
        if not self.creds: return None
        service = build('calendar', 'v3', credentials=self.creds)
        body = {
            'summary': title, 'description': desc,
            'start': {'dateTime': start, 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': end, 'timeZone': 'America/Sao_Paulo'}
        }
        try:
            evt = service.events().insert(calendarId=self.calendar_id, body=body).execute()
            return evt.get('id')
        except Exception as e:
            print(f"❌ Erro Calendar: {e}")
            return None

def send_telegram(chat_id, text):
    if TELEGRAM_TOKEN:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})

# --- ROTAS ---
@app.get("/")
def home():
    return {"status": "Agente com Ouvidos Ativo 👂"}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except:
        return {"status": "error"}

    if "message" not in data: return {"status": "ok"}
    
    chat_id = data["message"]["chat"]["id"]
    ai_response = None

    # 1. PROCESSAR TEXTO
    if "text" in data["message"]:
        text = data["message"]["text"]
        print(f"📩 Texto: {text}")
        ai_response = ask_gemini_text(text)

    # 2. PROCESSAR ÁUDIO (VOICE)
    elif "voice" in data["message"]:
        print("🎙️ Áudio recebido...")
        file_id = data["message"]["voice"]["file_id"]
        audio_path = download_telegram_voice(file_id)
        
        if audio_path:
            send_telegram(chat_id, "🎧 Ouvindo...")
            ai_response = ask_gemini_audio(audio_path)
        else:
            send_telegram(chat_id, "❌ Erro ao baixar audio.")

    # 3. EXECUTAR AÇÃO
    if ai_response:
        if ai_response.get("intent") == "agendar_reuniao":
            cal = GoogleCalendarService()
            if cal.create_event(ai_response["title"], ai_response["start_iso"], ai_response["end_iso"], ai_response.get("description")):
                send_telegram(chat_id, f"✅ Agendado via voz: {ai_response['title']}")
            else:
                send_telegram(chat_id, "❌ Entendi o áudio, mas falhei na agenda.")
        elif "response" in ai_response:
            send_telegram(chat_id, ai_response["response"])
            
    return {"status": "ok"}