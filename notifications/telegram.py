import logging
import hashlib
from datetime import datetime

import aiohttp

import config
from data.models import AnalysisResult
import storage.repository as repo

logger = logging.getLogger(__name__)

URGENCY_EMOJI = {
    "info": "ℹ️",
    "warning": "⚠️",
    "critical": "🚨",
}


def _fmt_currency(amount: float) -> str:
    return f"${amount:,.2f}"


def _fmt_pct(value: float, show_arrow: bool = True) -> str:
    if show_arrow:
        arrow = "↑" if value >= 0 else "↓"
        return f"{arrow}{abs(value):.1f}%"
    return f"{value:.1f}%"


async def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured, skipping message.")
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"

    async def _post(payload: dict) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        logger.info("Telegram message sent successfully.")
                        return True
                    body = await resp.text()
                    logger.error(f"Telegram API error {resp.status}: {body}")
                    return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode}
    if await _post(payload):
        return True

    # Retry without parse_mode if Markdown caused a parse error
    logger.warning("Retrying Telegram message without parse_mode.")
    payload.pop("parse_mode")
    return await _post(payload)


async def send_alert(title: str, body: str, urgency: str = "normal") -> bool:
    emoji = URGENCY_EMOJI.get(urgency, "📢")
    alert_key = hashlib.md5(f"{title}:{body}".encode()).hexdigest()

    already_sent = await repo.was_alert_sent(alert_key, hours=24)
    if already_sent:
        logger.info(f"Alert '{title}' already sent in last 24h, skipping.")
        return False

    text = f"{emoji} *{title}*\n\n{body}"
    success = await send_message(text)
    if success:
        await repo.log_alert_sent(alert_key)
    return success


async def send_digest(result: AnalysisResult) -> bool:
    text = result.raw_response
    # Truncate if too long for Telegram (4096 char limit)
    if len(text) > 4000:
        text = text[:3990] + "\n\n_[truncated]_"
    return await send_message(text)


async def send_startup_message(accounts_summary: str) -> bool:
    text = (
        "✅ *FinanceAdvisor is online*\n\n"
        f"_{datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n"
        f"{accounts_summary}\n\n"
        "Scheduled jobs active:\n"
        "• Daily digest at 07:00\n"
        "• Anomaly check every 4h\n"
        "• Weekly report Sunday 19:00\n"
        "• Monthly review on the 1st"
    )
    return await send_message(text)
