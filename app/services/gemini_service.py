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
   - agendar (Use quando usuário pedir "agendar", "lembrar", "lembrete", "lembre-me", "notificar" com data/hora específica)
   - consultar_agenda, add_task, list_tasks, complete_task, add_expense, finance_report
   - analyze_project (Use SEMPRE que o usuário pedir para ler/resumir/analisar arquivos de uma pasta, mesmo que já tenha sido listada antes)
   - conversa
3. IMPORTANTE para agendar/lembrete:
   - Se o usuário pedir "lembrar", "lembrete", "lembre-me", "notificar", "agendar" com data/hora, use intent: "agendar"
   - Exemplos: "Lembrar amanhã 8h colocar comida", "Lembrete hoje 8:20", "Agendar amanhã 8h", "Lembrar de emitir nota fiscal amanhã as 10h"
   - Retorne campos OBRIGATÓRIOS:
     * title: Título do lembrete (extraia do texto, ex: "Emitir nota fiscal tafacil")
     * start_iso: Data/hora em formato ISO 8601 com timezone (ex: "2026-01-27T10:00:00-03:00" para amanhã 10h)
     * end_iso: Data/hora final (se não especificado, use 1 hora depois de start_iso)
     * description: Descrição opcional
   - REGRAS para start_iso:
     * "amanhã 8h" ou "amanhã às 8h" -> data de amanhã às 08:00:00-03:00
     * "hoje 8:20" ou "hoje às 8:20" -> data de hoje às 08:20:00-03:00
     * "amanhã as 10h" -> data de amanhã às 10:00:00-03:00
     * SEMPRE use timezone -03:00 (Brasil)
     * SEMPRE use formato: YYYY-MM-DDTHH:MM:SS-03:00
     * Use a data atual ({now.strftime('%Y-%m-%d')}) como referência para "hoje"
4. IMPORTANTE para analyze_project:
   - Se o usuário pedir "resumo", "analise", "leia", "o que trata", "explique" sobre arquivos/pasta/documento
   - Use intent: "analyze_project" e campo "folder" com o nome da pasta (se mencionado) ou deixe vazio para usar a última pasta listada
   - Se o usuário mencionar um arquivo específico, inclua no campo "file" o nome do arquivo
   - Exemplos que devem gerar analyze_project:
     * "Faça um resumo sobre o que trata esse arquivo" -> {{"intent": "analyze_project", "folder": "", "file": ""}}
     * "Analise essa pasta" -> {{"intent": "analyze_project", "folder": "", "file": ""}}
     * "O que tem nesse documento?" -> {{"intent": "analyze_project", "folder": "", "file": ""}}
     * "Resumo" (quando há arquivos listados recentemente) -> {{"intent": "analyze_project", "folder": "", "file": ""}}
   - Retorne JSON com campos: intent, folder (opcional), file (opcional), response (opcional)
4. IMPORTANTE para add_expense:
   - Moeda brasileira: "reais", "real", "R$", "RS" são todos equivalentes
   - Se o usuário digitar "50 reais", "R$ 50,00", "50,00", "50.00", extraia o valor numérico
   - O campo "amount" deve conter o valor EXATO digitado pelo usuário (ex: "50,00" ou "50.00")
   - NÃO converta para número, mantenha como string com vírgula ou ponto
   - Exemplos:
     * "Adicione 20 reais de uber" -> {{"intent": "add_expense", "amount": "20,00", "item": "uber", "category": "transporte"}}
     * "Gastei R$ 50,00 no almoço" -> {{"intent": "add_expense", "amount": "50,00", "item": "almoço", "category": "alimentação"}}
     * "20 reais" -> {{"intent": "add_expense", "amount": "20,00", "item": "gasto", "category": "outros"}}
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
            # Tenta extrair informações mesmo com JSON inválido
            if "lembrar" in text.lower() or "lembrete" in text.lower() or "agendar" in text.lower():
                logger.warning("Tentando processar agendamento mesmo com JSON inválido")
                return {"intent": "agendar", "title": text, "start_iso": "", "end_iso": "", "description": ""}
            return {"intent": "conversa", "response": "Desculpe, não consegui processar. Tente de novo."}
        except Exception as e:
            logger.error(f"Erro na IA: {e}", exc_info=True)
            # Se o erro for relacionado a agendamento, tenta processar mesmo assim
            if "lembrar" in text.lower() or "lembrete" in text.lower() or "agendar" in text.lower():
                logger.warning("Tentando processar agendamento mesmo com erro na IA")
                return {"intent": "agendar", "title": text, "start_iso": "", "end_iso": "", "description": ""}
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
