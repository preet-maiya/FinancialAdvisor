.PHONY: help setup build up down restart logs shell \
        up-with-llama digest anomaly weekly monthly sync ps

# ── Default target ────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  FinanceAdvisor — Make targets"
	@echo ""
	@echo "  Setup"
	@echo "    make setup          Copy .env.example → .env and create dirs"
	@echo ""
	@echo "  Docker"
	@echo "    make build          Build the Docker image"
	@echo "    make up             Start financeadvisor in the background"
	@echo "    make up-with-llama  Start financeadvisor + llama.cpp server"
	@echo "    make down           Stop all containers"
	@echo "    make restart        Restart financeadvisor"
	@echo "    make logs           Tail live logs"
	@echo "    make ps             Show running containers"
	@echo "    make shell          Open a bash shell inside the container"
	@echo ""
	@echo "  Manual jobs (runs inside the container)"
	@echo "    make sync           Sync latest transactions from Monarch"
	@echo "    make digest         Run daily digest now + send to Telegram"
	@echo "    make anomaly        Run anomaly check now + send to Telegram"
	@echo "    make weekly         Run weekly report now + send to Telegram"
	@echo "    make monthly        Run monthly review now + send to Telegram"
	@echo ""

# ── Setup ─────────────────────────────────────────────────────────────────────
setup:
	@[ -f .env ] || (cp .env.example .env && echo "Created .env — fill in your credentials.")
	@mkdir -p data models
	@touch finance_advisor.log
	@[ -d .monarch_session.json ] && rmdir .monarch_session.json || true
	@touch .monarch_session.json
	@echo "Done. Edit .env before running 'make build'."

# ── Docker lifecycle ──────────────────────────────────────────────────────────
build:
	docker compose build

up:
	docker compose up -d financeadvisor
	@echo "Started. Use 'make logs' to follow output."

up-with-llama:
	docker compose --profile llama up -d
	@echo "Started financeadvisor + llama.cpp. Use 'make logs' to follow output."

down:
	docker compose --profile llama down

restart:
	docker compose restart financeadvisor

logs:
	docker compose logs -f financeadvisor

ps:
	docker compose ps

shell:
	docker compose exec financeadvisor bash

# ── Manual job triggers ───────────────────────────────────────────────────────
sync:
	docker compose run --rm financeadvisor python run_job.py sync

digest:
	docker compose run --rm financeadvisor python run_job.py digest

anomaly:
	docker compose run --rm financeadvisor python run_job.py anomaly

weekly:
	docker compose run --rm financeadvisor python run_job.py weekly

monthly:
	docker compose run --rm financeadvisor python run_job.py monthly
