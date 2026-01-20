import os
import json
import requests
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import google.generativeai as genai
from dotenv import load_dotenv # Importa o leitor de .env

# --- CONFIGURAÇÕES INICIAIS ---
# 1. Carrega as chaves do arquivo .env (se estiver no seu PC)
load_dotenv()

app = FastAPI(title="Agente Diario", version="0.6.0 (Secure)")

# 2. Pega as chaves de forma segura (do .env ou do Vercel)
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID") # <--- CORRIGIDO AQUI

# Configura Gemini
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

# --- CÉREBRO (GEMINI) ---
def ask_gemini(user_text: str):
    if not GEMINI_KEY: 
        print("❌ Erro: Chave Gemini não encontrada")
        return None
        
    model = genai.GenerativeModel("gemini-2.0-flash")
    
    now = datetime.now()
    prompt = f"""
    Data atual: {now.strftime("%Y-%m-%d %H:%M")}.
    Analise o pedido: "{user_text}"
    
    Se for agendar, responda APENAS JSON:
    {{ "intent": "agendar_reuniao", "title": "Titulo", "start_iso": "YYYY-MM-DDTHH:MM:SS", "end_iso": "YYYY-MM-DDTHH:MM:SS", "description": "Detalhes" }}
    
    Se for conversa, responda APENAS JSON:
    {{ "intent": "conversa", "response": "Sua resposta curta" }}
    """
    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except Exception as e:
        print(f"❌ Erro IA: {e}")
        return None

# --- MÃOS (GOOGLE CALENDAR) ---
class GoogleCalendarService:
    def __init__(self):
        self.creds = None
        self.calendar_id = CALENDAR_ID
        
        # 1. Tenta ler do Vercel (Variável de Ambiente com o JSON inteiro)
        firebase_env = os.environ.get("FIREBASE_CREDENTIALS")
        
        if firebase_env:
            # Nuvem (Vercel)
            cred_info = json.loads(firebase_env)
            self.creds = service_account.Credentials.from_service_account_info(
                cred_info, scopes=['https://www.googleapis.com/auth/calendar']
            )
        else:
            # Local (Seu PC) - Procura o arquivo na pasta atual
            key_path = "firebase-key.json"
            if os.path.exists(key_path):
                self.creds = service_account.Credentials.from_service_account_file(
                    key_path, scopes=['https://www.googleapis.com/auth/calendar']
                )

    def create_event(self, title, start, end, desc=""):
        if not self.creds: 
            print("❌ Sem credenciais do Firebase/Google")
            return None
            
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

# --- ENDPOINTS ---
@app.get("/")
def home():
    return {"status": "Agente Diario Online 🚀"}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except:
        return {"status": "error", "msg": "Invalid JSON"}

    if "message" in data and "text" in data["message"]:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"]["text"]
        
        print(f"📩 Recebido: {text}")
        ai = ask_gemini(text)
        
        if ai and ai.get("intent") == "agendar_reuniao":
            cal = GoogleCalendarService()
            if cal.create_event(ai["title"], ai["start_iso"], ai["end_iso"], ai.get("description")):
                send_telegram(chat_id, f"✅ Agendado: {ai['title']}")
            else:
                send_telegram(chat_id, "❌ Falha ao agendar. Verifique logs.")
        elif ai:
            send_telegram(chat_id, ai.get("response"))
        else:
            send_telegram(chat_id, "Não entendi ou houve um erro.")
            
    return {"status": "ok"}