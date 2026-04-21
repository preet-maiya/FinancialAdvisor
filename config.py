import os
import ssl
import logging
import certifi
from dotenv import load_dotenv

# Patch ssl to use certifi's CA bundle so aiohttp (and any other library)
# can verify HTTPS certificates regardless of the system's CA store.
_orig_create_default_context = ssl.create_default_context
def _certifi_context(*args, **kwargs):
    kwargs.setdefault("cafile", certifi.where())
    return _orig_create_default_context(*args, **kwargs)
ssl.create_default_context = _certifi_context

load_dotenv()

# Monarch Money
MONARCH_EMAIL = os.getenv("MONARCH_EMAIL", "")
MONARCH_PASSWORD = os.getenv("MONARCH_PASSWORD", "")
MONARCH_SESSION_FILE = os.getenv("MONARCH_SESSION_FILE", ".monarch_session.json")

# LLM (llama.cpp OpenAI-compatible server)
LLAMA_CPP_BASE_URL = os.getenv("LLAMA_CPP_BASE_URL", "http://localhost:8080/v1")
LLAMA_CPP_MODEL = os.getenv("LLAMA_CPP_MODEL", "local-model")
LLAMA_CPP_MAX_TOKENS = int(os.getenv("LLAMA_CPP_MAX_TOKENS", "2048"))
LLAMA_CPP_TEMPERATURE = float(os.getenv("LLAMA_CPP_TEMPERATURE", "0.2"))

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Database
DB_PATH = os.getenv("DB_PATH", "data/finance.db")

# Web UI
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("finance_advisor.log"),
    ],
)
