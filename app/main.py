import os
import json
import requests
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import google.generativeai as genai
from dotenv import load_dotenv

# --- CONFIGURAÇÕES ---
load_dotenv()
app = FastAPI(title="Agente Diario", version="0.8.0 (Full Vision)")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

# --- FUNÇÕES AUXILIARES ---
def get_current_date_prompt():
    now = datetime.now()
    return f"""
    Data e Hora atual: {now.strftime("%Y-%m-%d %H:%M")}.
    Analise o pedido do usuário (texto ou audio).
    
    1. Se for AGENDAR, retorne JSON:
    {{ "intent": "agendar", "title": "Titulo", "start_iso": "YYYY-MM-DDTHH:MM:SS", "end_iso": "YYYY-MM-DDTHH:MM:SS", "description": "Detalhes" }}
    
    2. Se for CONSULTAR/LER agenda (ex: "o que tenho hoje?", "estou livre amanhã?"), retorne JSON calculando o intervalo de tempo pedido:
    {{ "intent": "consultar", "time_min": "YYYY-MM-DDTHH:MM:SS", "time_max": "YYYY-MM-DDTHH:MM:SS" }}
    (Dica: Para 'hoje', time_min é agora e time_max é final do dia. Para 'amanhã', o dia todo).
    
    3. Se for CONVERSA genérica, retorne JSON:
    {{ "intent": "conversa", "response": "Sua resposta curta" }}
    """

def ask_gemini_generic(content_input):
    """Função única para Texto ou Áudio"""
    if not GEMINI_KEY: return None
    model = genai.GenerativeModel("gemini-2.0-flash")
    prompt = get_current_date_prompt()
    
    try:
        # Se for lista (audio + prompt), passa direto. Se for string (texto), cria lista.
        parts = [content_input, prompt] if not isinstance(content_input, list) else content_input + [prompt]
        
        response = model.generate_content(parts, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except Exception as e:
        print(f"❌ Erro IA: {e}")
        return None

def download_telegram_voice(file_id: str):
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}")
    file_path_info = r.json().get("result", {}).get("file_path")
    if not file_path_info: return None
    
    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path_info}"
    voice_data = requests.get(download_url).content
    
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
            print(f"❌ Erro Calendar Create: {e}")
            return None

    def list_events(self, time_min, time_max):
        if not self.creds: return "Erro de credenciais."
        service = build('calendar', 'v3', credentials=self.creds)
        
        # Garante fuso horário UTC (o Z no final) para a busca funcionar bem
        if not time_min.endswith("Z"): time_min += "-03:00" # Ajuste básico BR
        if not time_max.endswith("Z"): time_max += "-03:00"

        try:
            events_result = service.events().list(
                calendarId=self.calendar_id, 
                timeMin=time_min, timeMax=time_max,
                singleEvents=True, orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])

            if not events:
                return "📅 Nada agendado para esse período."

            msg = "📅 **Sua Agenda:**\n"
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                # Formatação simples da hora (pega T10:00:00...)
                hora = start[11:16] if 'T' in start else "Dia todo"
                msg += f"• {hora} - {event['summary']}\n"
            return msg

        except Exception as e:
            print(f"❌ Erro Calendar List: {e}")
            return "Erro ao ler agenda."

def send_telegram(chat_id, text):
    if TELEGRAM_TOKEN:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})

# --- ROTAS ---
@app.get("/")
def home():
    return {"status": "Agente Completo Online 👁️👂🤚"}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except:
        return {"status": "error"}

    if "message" not in data: return {"status": "ok"}
    
    chat_id = data["message"]["chat"]["id"]
    ai_response = None

    # 1. ENTRADA (Texto ou Voz)
    if "text" in data["message"]:
        text = data["message"]["text"]
        print(f"📩 Texto: {text}")
        ai_response = ask_gemini_generic(text)

    elif "voice" in data["message"]:
        print("🎙️ Áudio recebido...")
        file_id = data["message"]["voice"]["file_id"]
        audio_path = download_telegram_voice(file_id)
        if audio_path:
            send_telegram(chat_id, "🎧 Ouvindo...")
            # Upload do arquivo para Gemini
            myfile = genai.upload_file(audio_path, mime_type="audio/ogg")
            ai_response = ask_gemini_generic([myfile])

    # 2. AÇÃO
    if ai_response:
        intent = ai_response.get("intent")
        cal = GoogleCalendarService()

        if intent == "agendar":
            if cal.create_event(ai_response["title"], ai_response["start_iso"], ai_response["end_iso"], ai_response.get("description")):
                send_telegram(chat_id, f"✅ Agendado: {ai_response['title']}")
            else:
                send_telegram(chat_id, "❌ Falha ao agendar.")
        
        elif intent == "consultar":
            # O Gemini calculou as datas, agora o Python busca
            send_telegram(chat_id, "🔍 Verificando sua agenda...")
            agenda_texto = cal.list_events(ai_response["time_min"], ai_response["time_max"])
            send_telegram(chat_id, agenda_texto)
            
        elif "response" in ai_response:
            send_telegram(chat_id, ai_response["response"])
            
    return {"status": "ok"}