"""
Google Gemini AI Service
"""
import json
import re
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import google.generativeai as genai

from app.core.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


class GeminiService:
    """Serviço de integração com Google Gemini AI"""
    
    def __init__(self):
        self.model = genai.GenerativeModel('gemini-2.0-flash') if GEMINI_API_KEY else None
    
    def chat(self, text: str, history_str: str, is_audio: bool = False) -> Dict[str, Any]:
        """
        Processa mensagem com IA.
        REGRA 4: Anti-Papagaio - Previne repetição da mensagem do usuário.
        """
        if not self.model:
            return {"intent": "conversa", "response": "IA não configurada."}
        
        now = datetime.now()
        user_prompt = "[Audio]" if is_audio else text
        
        system_prompt = f"""SYSTEM: Jarvis. Data: {now.strftime('%d/%m %H:%M')}.
1. Não repita o usuário.
2. JSON Intents: 
   - agendar, consultar_agenda, add_task, list_tasks, complete_task, add_expense, finance_report
   - analyze_project (Use isso se o usuario pedir para ler/resumir arquivos de uma pasta JÁ listada ou nova)
   - conversa
3. IMPORTANTE para add_expense:
   - Se o usuário digitar "50,00" ou "50.00", extraia EXATAMENTE como está (com vírgula ou ponto)
   - O campo "amount" deve conter o valor EXATO digitado pelo usuário (ex: "50,00" ou "50.00")
   - NÃO converta para número, mantenha como string com vírgula ou ponto
HISTÓRICO: {history_str}
USUÁRIO: "{user_prompt}"
"""
        
        try:
            content = [text, system_prompt] if is_audio else system_prompt
            response = self.model.generate_content(
                content,
                generation_config={"response_mime_type": "application/json"}
            )
            
            raw = (response.text or "").strip()
            # Tenta extrair JSON se vier em markdown (```json ... ```)
            if raw.startswith("```"):
                raw = re.sub(r'^```(?:json)?\s*', '', raw)
                raw = re.sub(r'\s*```$', '', raw)
            
            data = json.loads(raw)
            
            # REGRA 4: Anti-Papagaio
            if data.get("intent") == "conversa":
                ai_response = data.get("response", "").strip().lower()
                user_text_lower = (text or "").strip().lower()
                if ai_response == user_text_lower or not ai_response:
                    data["response"] = "Entendi. Como posso ajudar?"
            
            return data
        except json.JSONDecodeError as e:
            logger.error(f"IA retornou JSON inválido: {e}. Raw: {raw[:500] if raw else 'vazio'}")
            return {"intent": "conversa", "response": "Desculpe, não consegui processar. Tente de novo."}
        except Exception as e:
            logger.error(f"Erro na IA: {e}", exc_info=True)
            return {"intent": "conversa", "response": "Desculpe, tive um problema. Tente em instantes."}
    
    def generate_content(self, prompt: str) -> str:
        """Gera conteúdo a partir de um prompt"""
        if not self.model:
            return ""
        
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Erro ao gerar conteúdo: {e}")
            return ""
    
    def transcribe_audio(self, audio_file_path: str) -> str:
        """Transcreve áudio usando Gemini"""
        if not self.model:
            return ""
        
        try:
            audio_file = genai.upload_file(audio_file_path, mime_type="audio/ogg")
            response = self.model.generate_content(audio_file)
            return response.text
        except Exception as e:
            logger.error(f"Erro ao transcrever áudio: {e}")
            return ""
