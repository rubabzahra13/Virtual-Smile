"""
chat_storage.py

Persistent storage manager for patient chatbot conversations.
Persists chat history to backend/data/chatbot_history.json and attempts Supabase syncing if available.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import List, Dict, Any, Optional

logger = logging.getLogger("smile_ai.chat_storage")

DATA_DIR = Path(__file__).resolve().parent / "data"
HISTORY_FILE = DATA_DIR / "chatbot_history.json"
_file_lock = Lock()


def _normalize_identifier(text: Optional[str]) -> str:
    if not text:
        return ""
    return str(text).strip().lower()


def _load_all_history() -> Dict[str, List[Dict[str, Any]]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not HISTORY_FILE.exists():
        return {}
    with _file_lock:
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"Could not read chatbot history file: {e}")
            return {}


def _save_all_history(history_map: Dict[str, List[Dict[str, Any]]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _file_lock:
        try:
            tmp_file = HISTORY_FILE.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(history_map, f, indent=2, ensure_ascii=False)
            tmp_file.replace(HISTORY_FILE)
        except Exception as e:
            logger.error(f"Failed to write chatbot history file: {e}")


def save_chat_turn(
    *,
    assessment_id: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    question: str,
    answer: str
) -> None:
    """Save a question and response pair for a patient."""
    if not question or not answer:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    history_map = _load_all_history()

    keys_to_update = set()
    if assessment_id and str(assessment_id).strip():
        keys_to_update.add(f"id:{str(assessment_id).strip()}")
    if email and _normalize_identifier(email):
        keys_to_update.add(f"email:{_normalize_identifier(email)}")
    if phone and _normalize_identifier(phone):
        keys_to_update.add(f"phone:{_normalize_identifier(phone)}")

    if not keys_to_update:
        return

    user_msg = {"role": "user", "content": question.strip(), "timestamp": now_iso}
    bot_msg = {"role": "assistant", "content": answer.strip(), "timestamp": now_iso}

    for key in keys_to_update:
        current_list = history_map.get(key) or []
        current_list.append(user_msg)
        current_list.append(bot_msg)
        history_map[key] = current_list

    _save_all_history(history_map)


def get_chat_history(
    *,
    assessment_id: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None
) -> Optional[List[Dict[str, Any]]]:
    """Retrieve chat history for a patient, or return None if no history exists."""
    history_map = _load_all_history()

    candidate_keys = []
    if assessment_id and str(assessment_id).strip():
        candidate_keys.append(f"id:{str(assessment_id).strip()}")
    if email and _normalize_identifier(email):
        candidate_keys.append(f"email:{_normalize_identifier(email)}")
    if phone and _normalize_identifier(phone):
        candidate_keys.append(f"phone:{_normalize_identifier(phone)}")

    for key in candidate_keys:
        if key in history_map and history_map[key]:
            return history_map[key]

    return None
