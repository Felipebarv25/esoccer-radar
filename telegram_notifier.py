"""Salida a Telegram (canal de eSoccer). Maneja rate limit 429."""
import time

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramNotifier:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send(self, text: str, max_retries: int = 3) -> bool:
        payload = {"chat_id": self.chat_id, "text": text,
                   "parse_mode": "HTML", "disable_web_page_preview": True}
        for intento in range(1, max_retries + 1):
            resp = requests.post(self.api_url, json=payload, timeout=15)
            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                wait = resp.json().get("parameters", {}).get("retry_after", 2) + intento
                print(f"[Telegram] 429, reintento en {wait}s...")
                time.sleep(wait)
                continue
            print(f"[Telegram] Error {resp.status_code}: {resp.text[:300]}")
            return False
        return False
