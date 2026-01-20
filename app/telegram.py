from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.firestore_repo import FirestoreRepository
from app.deps import get_repo
from app.models import Task


router = APIRouter(prefix="/telegram", tags=["telegram"])


def _get_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN nao configurado.")
    return token


def _get_default_chat_id() -> Optional[str]:
    return os.getenv("TELEGRAM_DEFAULT_CHAT_ID")


async def _send_message(chat_id: str, text: str) -> None:
    token = _get_token()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json={"chat_id": chat_id, "text": text})
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Falha ao enviar mensagem ao Telegram.")


def _create_task_from_text(repo: FirestoreRepository, text: str) -> Task:
    task_id = int(datetime.utcnow().timestamp())
    task = Task(id=task_id, title=text.strip(), due=None, project_id=None)
    repo.set_document("tasks", str(task.id), task.model_dump())
    return task


@router.post("/webhook")
async def telegram_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    message = payload.get("message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if not text:
        return {"ok": True, "ignored": True}

    repo = get_repo()
    if text.lower().startswith("task "):
        task_title = text[5:]
        task = _create_task_from_text(repo, task_title)
        if chat_id:
            await _send_message(str(chat_id), f"Tarefa criada: {task.title}")
        return {"ok": True, "task_id": task.id}

    if chat_id:
        await _send_message(str(chat_id), "Comando nao reconhecido. Use: task <titulo>")
    return {"ok": True}


@router.post("/notify/test")
async def telegram_notify_test(request: Request) -> Dict[str, Any]:
    body = await request.json()
    text = body.get("text", "Teste de notificacao.")
    chat_id = body.get("chat_id") or _get_default_chat_id()
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id nao informado.")
    await _send_message(str(chat_id), text)
    return {"ok": True}
