"""Salida a Telegram (canal de eSoccer). Maneja rate limit 429."""
import time

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramNotifier:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.chat_id = chat_id
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send_document(self, file_path: str, caption: str = "") -> bool:
        """Envía un archivo (CSV/Excel) al canal."""
        url = f"https://api.telegram.org/bot{self.token}/sendDocument"
        try:
            with open(file_path, "rb") as fh:
                resp = requests.post(
                    url, data={"chat_id": self.chat_id, "caption": caption,
                               "parse_mode": "HTML"},
                    files={"document": fh}, timeout=60)
            if resp.status_code == 200:
                return True
            print(f"[Telegram] doc error {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[Telegram] doc excepción: {e}")
        return False

    def send_photo(self, file_path: str, caption: str = "", reply_to: int = None) -> bool:
        """Envía una imagen (tarjeta de oportunidad) como foto."""
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        payload = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
            payload["allow_sending_without_reply"] = True
        try:
            with open(file_path, "rb") as fh:
                resp = requests.post(url, data=payload, files={"photo": fh}, timeout=60)
            if resp.status_code == 200:
                return True
            print(f"[Telegram] foto error {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[Telegram] foto excepción: {e}")
        return False

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
