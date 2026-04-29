import html
import logging
import hashlib
import re
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


def _inline_to_html(text: str) -> str:
    """Escape HTML and convert inline markdown (**bold**, *bold*, _italic_, `code`)."""
    pattern = re.compile(r'(\*\*[^*\n]+\*\*|\*[^*\n]+\*|_[^_\n]+_|`[^`\n]+`)')
    result = []
    last = 0
    for m in pattern.finditer(text):
        result.append(html.escape(text[last:m.start()]))
        s = m.group(0)
        if s.startswith('**'):
            result.append(f'<b>{html.escape(s[2:-2])}</b>')
        elif s.startswith('*'):
            result.append(f'<b>{html.escape(s[1:-1])}</b>')
        elif s.startswith('_'):
            result.append(f'<i>{html.escape(s[1:-1])}</i>')
        else:
            result.append(f'<code>{html.escape(s[1:-1])}</code>')
        last = m.end()
    result.append(html.escape(text[last:]))
    return ''.join(result)


def _md_to_html(text: str) -> str:
    """Convert markdown from LLM output to Telegram HTML."""
    lines = []
    for line in text.split('\n'):
        m = re.match(r'^#{1,3}\s+(.*)', line)
        if m:
            lines.append(f'<b>{_inline_to_html(m.group(1))}</b>')
            continue
        m = re.match(r'^[-*]\s+(.*)', line)
        if m:
            lines.append(f'• {_inline_to_html(m.group(1))}')
            continue
        lines.append(_inline_to_html(line))
    return '\n'.join(lines)


async def send_message(text: str, parse_mode: str = "HTML") -> bool:
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

    # Retry without parse_mode if HTML caused a parse error
    logger.warning("Retrying Telegram message without parse_mode.")
    payload.pop("parse_mode")
    return await _post(payload)


async def send_alert(title: str, body: str, urgency: str = "normal") -> bool:
    body = _strip_think(body)
    emoji = URGENCY_EMOJI.get(urgency, "📢")
    alert_key = hashlib.md5(f"{title}:{body}".encode()).hexdigest()

    already_sent = await repo.was_alert_sent(alert_key, hours=24)
    if already_sent:
        logger.info(f"Alert '{title}' already sent in last 24h, skipping.")
        return False

    text = f"{emoji} <b>{html.escape(title)}</b>\n\n{body}"
    success = await send_message(text)
    if success:
        await repo.log_alert_sent(alert_key)
    return success


def _strip_think(text: str) -> str:
    """Remove <think>...</think> blocks from LLM output before sending."""
    return re.sub(r"<think>[\s\S]*?</think>", "", text).strip()


async def send_digest(result: AnalysisResult) -> bool:
    text = _strip_think(result.summary)
    return await send_message(text)


async def send_startup_message(accounts_summary: str) -> bool:
    text = (
        "✅ <b>FinanceAdvisor is online</b>\n\n"
        f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M')}</i>\n\n"
        f"{accounts_summary}\n\n"
        "Scheduled jobs active:\n"
        "• Daily digest at 07:00\n"
        "• Anomaly check every 4h\n"
        "• Weekly report Sunday 19:00\n"
        "• Monthly review on the 1st"
    )
    return await send_message(text)
