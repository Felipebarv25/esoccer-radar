"""Salida a Telegram (canal de eSoccer). Maneja rate limit 429."""
import time

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramNotifier:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send(self, text: str, reply_to: int = None, max_retries: int = 3):
        """Envía un mensaje. Devuelve el message_id (int) si sale bien, o None.

        reply_to: message_id al que responder (para cerrar el partido bajo su alerta).
        """
        payload = {"chat_id": self.chat_id, "text": text,
                   "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
            payload["allow_sending_without_reply"] = True
        for intento in range(1, max_retries + 1):
            resp = requests.post(self.api_url, json=payload, timeout=15)
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("message_id")
            if resp.status_code == 429:
                wait = resp.json().get("parameters", {}).get("retry_after", 2) + intento
                print(f"[Telegram] 429, reintento en {wait}s...")
                time.sleep(wait)
                continue
            print(f"[Telegram] Error {resp.status_code}: {resp.text[:300]}")
            return None
        return None
