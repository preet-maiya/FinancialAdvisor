# FinanceAdvisor

A personal finance agent that connects to your Monarch Money account, analyzes your spending with a local LLM (via llama.cpp), and delivers scheduled insights to Telegram.

## Quick Start (Docker)

```bash
make setup      # creates .env and required dirs
# edit .env with your credentials
make build      # build the image
make up         # start the scheduler in the background
make logs       # follow live output
```

If you want llama.cpp to also run in Docker (CPU-only, no GPU passthrough):

```bash
# Place your GGUF model at ./models/model.gguf
# Set LLAMA_MODEL_FILE=model.gguf in .env
make up-with-llama
```

---

## Make Targets

| Command | What it does |
|---------|-------------|
| `make setup` | Copy `.env.example` → `.env`, create `data/` and `models/` dirs |
| `make build` | Build the Docker image |
| `make up` | Start financeadvisor in the background |
| `make up-with-llama` | Start financeadvisor + llama.cpp server |
| `make down` | Stop all containers |
| `make restart` | Restart the financeadvisor container |
| `make logs` | Tail live container logs |
| `make ps` | Show running containers |
| `make shell` | Open bash inside the container |
| `make sync` | Manually trigger a transaction sync |
| `make digest` | Manually run daily digest + send to Telegram |
| `make anomaly` | Manually run anomaly check + send to Telegram |
| `make weekly` | Manually run weekly report + send to Telegram |
| `make monthly` | Manually run monthly review + send to Telegram |

---

## Prerequisites

### 1. llama.cpp server

**Option A — run natively (GPU support):**

```bash
# Clone and build
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
make -j

# Download a model (e.g. Mistral 7B GGUF)
# Place it at ~/models/mistral-7b-instruct.gguf

# Start the OpenAI-compatible server
./llama-server -m ~/models/mistral-7b-instruct.gguf \
  --host 0.0.0.0 --port 8080 \
  --ctx-size 4096 -ngl 35
```

**Option B — run in Docker (CPU only):**

```bash
mkdir -p models
# place your .gguf file in ./models/
# set LLAMA_MODEL_FILE=yourmodel.gguf and LLAMA_CPP_BASE_URL=http://llama-cpp:8080/v1 in .env
make up-with-llama
```

The server will be available at `http://localhost:8080/v1`.

### 2. Telegram Bot

1. Open Telegram and message `@BotFather`
2. Send `/newbot` and follow prompts — save the **bot token**
3. Start a conversation with your new bot
4. Get your chat ID: visit `https://api.telegram.org/bot<TOKEN>/getUpdates` after sending any message to your bot

### 3. Monarch Money account

Your regular Monarch Money credentials. On first run you'll be prompted if not set in `.env`.

### 4. Python

Python 3.11 or newer.

---

## Installation

```bash
git clone <this-repo>
cd FinancialAdvisor

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials
```

---

## Configuration (.env)

```
MONARCH_EMAIL=you@example.com
MONARCH_PASSWORD=yourpassword
MONARCH_SESSION_FILE=.monarch_session.json

LLAMA_CPP_BASE_URL=http://localhost:8080/v1
LLAMA_CPP_MODEL=local-model
LLAMA_CPP_MAX_TOKENS=2048
LLAMA_CPP_TEMPERATURE=0.2

TELEGRAM_BOT_TOKEN=123456:ABC-your-token
TELEGRAM_CHAT_ID=987654321

DB_PATH=data/finance.db
LOG_LEVEL=INFO
```

---

## First Run

```bash
python main.py
```

On first startup, FinanceAdvisor will:
1. Initialize the SQLite database
2. Authenticate with Monarch Money (prompts if session doesn't exist)
3. Sync the last 90 days of transactions
4. Send a "FinanceAdvisor is online ✅" message to Telegram with your account summary
5. Start the scheduler

---

## Scheduled Jobs

| Job | Schedule | What it sends |
|-----|----------|---------------|
| **Daily Digest** | Every day at 07:00 | Yesterday's spending by category, budget status, savings rate, net worth delta, one actionable tip |
| **Anomaly Check** | Every 4 hours | Alerts for charges >2x normal, new merchants over $50, duplicate charges, subscription price increases |
| **Weekly Report** | Sunday at 19:00 | Week vs prior week by category, top 3 overspend + top 3 wins, savings rate, monthly budget progress, one pattern spotted |
| **Monthly Review** | 1st of month at 08:00 | Full month income/expenses, savings rate, net worth change, subscription audit, category trends, financial health score (1–10), 3 recommendations |
| **Transaction Sync** | Every 6 hours | Silently syncs latest transactions from Monarch to SQLite and updates spending baselines |

---

## Running as a Background Service (systemd)

Create `/etc/systemd/system/financeadvisor.service`:

```ini
[Unit]
Description=FinanceAdvisor Personal Finance Agent
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/FinancialAdvisor
ExecStart=/home/youruser/FinancialAdvisor/.venv/bin/python main.py
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal
EnvironmentFile=/home/youruser/FinancialAdvisor/.env

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable financeadvisor
sudo systemctl start financeadvisor
sudo systemctl status financeadvisor

# View logs
journalctl -u financeadvisor -f
```

---

## Project Structure

```
FinancialAdvisor/
├── main.py                    # Entry point, starts scheduler
├── config.py                  # Loads env vars
├── requirements.txt
├── .env.example
├── data/
│   ├── fetcher.py             # Monarch Money data fetching
│   └── models.py              # Pydantic models
├── storage/
│   ├── database.py            # SQLite setup
│   └── repository.py         # CRUD operations
├── agent/
│   ├── llm.py                 # llama.cpp LangChain client
│   ├── tools.py               # LangChain tools
│   ├── prompts.py             # System prompts
│   └── analyzer.py            # Analysis orchestration
├── notifications/
│   └── telegram.py            # Telegram sender
└── scheduler/
    └── jobs.py                # APScheduler job wrappers
```
