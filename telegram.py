"""
telegram.py
Handles Telegram bot messaging and video delivery.
"""

import os
import time
from pathlib import Path

import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_FILE_SIZE_MB = 50


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a text message to the Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] ⚠️  Missing bot token or chat ID")
        return False
    try:
        r = requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[Telegram] sendMessage error: {e}")
        return False


def send_video(
    video_path: str,
    caption: str = "",
    concept: dict | None = None,
) -> bool:
    """Send a video file to Telegram. Falls back to document if too large."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] ⚠️  Missing credentials")
        return False

    path = Path(video_path)
    if not path.exists():
        print(f"[Telegram] ❌ File not found: {video_path}")
        return False

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        print(f"[Telegram] ⚠️  File {size_mb:.1f}MB > {MAX_FILE_SIZE_MB}MB limit, sending as document")

    if not caption and concept:
        caption = build_caption(concept)

    endpoint = "sendVideo" if size_mb <= MAX_FILE_SIZE_MB else "sendDocument"
    file_key = "video" if endpoint == "sendVideo" else "document"

    try:
        with open(video_path, "rb") as f:
            r = requests.post(
                f"{BASE_URL}/{endpoint}",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"},
                files={file_key: f},
                timeout=120,
            )
        if r.status_code == 200:
            print(f"[Telegram] ✅ Sent: {path.name}")
            return True
        else:
            print(f"[Telegram] ❌ Error {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        print(f"[Telegram] send error: {e}")
        return False


def build_caption(concept: dict) -> str:
    title = concept.get("concept_title", "Dance Video")
    trend = concept.get("trend_reference", "")
    style = concept.get("dance_style", "")
    mood = concept.get("mood", "")

    lines = [f"<b>🎬 {title}</b>"]
    if trend:
        lines.append(f"📈 <i>{trend}</i>")
    if style:
        lines.append(f"💃 {style}")
    if mood:
        lines.append(f"✨ {mood.capitalize()}")
    lines.append("\n#HindiDance #BollywoodShorts #AIVideo")
    return "\n".join(lines)


def send_status_update(message: str) -> bool:
    """Send a plain status/log message."""
    return send_message(f"🤖 <b>Agent Status</b>\n{message}")


def send_production_summary(completed: int, failed: int, target: int) -> bool:
    pct = int((completed / target) * 100) if target else 0
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    text = (
        f"📊 <b>Production Summary</b>\n\n"
        f"[{bar}] {pct}%\n\n"
        f"✅ Completed: {completed}/{target}\n"
        f"❌ Failed: {failed}\n"
        f"🔄 Remaining: {max(0, target - completed)}"
    )
    return send_message(text)


if __name__ == "__main__":
    # Quick test
    ok = send_message("🎬 AI Dance Video Agent is online!")
    print("Message sent:", ok)
