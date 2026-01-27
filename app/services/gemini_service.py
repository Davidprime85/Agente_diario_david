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
        # prompt-engineer: Role, Context, Instructions, Constraints, Output format, Examples
        system_prompt = f"""## Role
Você é o Jarvis, assistente do Sistema Agente Diário. Responde em português e sempre em JSON.

## Context
Data/hora de referência: {now.strftime('%d/%m %H:%M')} ({now.strftime('%Y-%m-%d')}).

## Instructions
Responda com um único JSON contendo: intent, e os campos específicos de cada intent.
Intents válidos: agendar, consultar_agenda, add_task, list_tasks, complete_task, add_expense, finance_report, analyze_project, conversa.

- agendar: quando o usuário pedir "agendar", "lembrar", "lembrete", "lembre-me", "notificar" com data/hora.
- consultar_agenda: "Consulte agenda", "O que tenho hoje?", "Qual compromisso amanhã?", "compromissos amanhã". Retorne time_min, time_max em ISO -03:00. Para "amanhã" use o dia seguinte 00:00–23:59; para "hoje" ou genérico use hoje 00:00–23:59.
- analyze_project: quando pedir resumir/analisar/ler arquivos ou pasta (mesmo que já tenha listado).
- add_expense: quando mencionar gasto, valor em reais, R$, despesa.

Para agendar: retorne title, start_iso, end_iso, description.
  start_iso/end_iso em ISO 8601 com -03:00 (Brasil). Ex: "2026-01-27T10:00:00-03:00".
  "amanhã 8h" = amanhã 08:00-03:00; "hoje 8:20" = hoje 08:20-03:00.
  Data com ano: "dia 27/01/2025", "27/01/2025" = 2025-01-27. Use o ano explícito quando o usuário informar.
Para consultar_agenda: time_min e time_max (ex: "2026-01-27T00:00:00-03:00" e "2026-01-27T23:59:59-03:00" para um dia).
Para add_expense: amount como string ("50,00" ou "50.00"), item, category. Moeda: reais/R$/real.
Para analyze_project: folder (nome da pasta se mencionado, senão ""), file (nome do arquivo se mencionado, senão "").

## Constraints (NÃO faça)
- Não repita literalmente o texto do usuário na resposta.
- Não invente datas nem use timezone diferente de -03:00.
- Não converta amount para número; mantenha string com vírgula ou ponto.
- Não use outro intent se o usuário claramente pediu lembrete/agendar ou análise de arquivo.

## Output format
JSON válido, sem markdown. Campos obrigatórios: intent. Demais conforme o intent.

## Examples (few-shot)
- "Lembrar amanhã 8h colocar comida" -> intent agendar, title "colocar comida", start_iso em ISO -03:00 para amanhã 08:00.
- "Resumo desse arquivo" / "Analise essa pasta" -> {{"intent":"analyze_project","folder":"","file":""}}
- "Gastei R$ 50 no almoço" -> {{"intent":"add_expense","amount":"50,00","item":"almoço","category":"alimentação"}}

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
            
            # REGRA 4: Anti-Papagaio e resposta vaga
            if data.get("intent") == "conversa":
                ai_response = data.get("response", "").strip()
                ai_lower = ai_response.lower()
                user_text_lower = (text or "").strip().lower()
                if ai_response == user_text_lower or not ai_response:
                    data["response"] = "Entendi. Como posso ajudar?"
                elif ai_lower in ("errr... como posso ajudar?", "errr... como posso ajudar", "como posso ajudar?") or (len(ai_response) < 25 and "ajudar" in ai_lower):
                    data["response"] = "Não tenho informações sobre isso. Posso ajudar com: agenda, tarefas, gastos ou arquivos do Drive. O que você precisa?"
            
            return data
        except json.JSONDecodeError as e:
            logger.error(f"IA retornou JSON inválido: {e}. Raw: {raw[:500] if raw else 'vazio'}")
            t = text.lower()
            if "lembrar" in t or "lembrete" in t or "agendar" in t:
                return {"intent": "agendar", "title": text, "start_iso": "", "end_iso": "", "description": ""}
            if "agenda" in t or "compromisso" in t or ("o que tenho" in t and "amanhã" in t) or ("qual" in t and "amanhã" in t):
                return {"intent": "consultar_agenda", "time_min": "", "time_max": ""}
            return {"intent": "conversa", "response": "Desculpe, não consegui processar. Tente de novo."}
        except Exception as e:
            logger.error(f"Erro na IA: {e}", exc_info=True)
            t = text.lower()
            if "lembrar" in t or "lembrete" in t or "agendar" in t:
                return {"intent": "agendar", "title": text, "start_iso": "", "end_iso": "", "description": ""}
            if "agenda" in t or "compromisso" in t or ("o que tenho" in t and "amanhã" in t) or ("qual" in t and "amanhã" in t):
                return {"intent": "consultar_agenda", "time_min": "", "time_max": ""}
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
