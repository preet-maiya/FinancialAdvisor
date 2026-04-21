import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential
from monarchmoney import MonarchMoney

import config
from data.models import Transaction, Account, Budget, CashflowMonth, NetWorthSnapshot

logger = logging.getLogger(__name__)


def _get_monarch_client() -> MonarchMoney:
    return MonarchMoney()


async def _ensure_authenticated(mm: MonarchMoney) -> None:
    email = config.MONARCH_EMAIL
    password = config.MONARCH_PASSWORD
    await mm.login(email=email, password=password)
    logger.info("Authenticated with Monarch Money.")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def get_transactions(days: int = 30) -> list[Transaction]:
    mm = _get_monarch_client()
    await _ensure_authenticated(mm)

    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")

    raw = await mm.get_transactions(
        start_date=start_date,
        end_date=end_date,
        limit=1000,
    )

    transactions = []
    for t in raw.get("allTransactions", {}).get("results", []):
        try:
            transactions.append(
                Transaction(
                    id=t.get("id", ""),
                    date=datetime.strptime(t.get("date", ""), "%Y-%m-%d").date(),
                    merchant=t.get("merchant", {}).get("name", "") or t.get("plaidName", "Unknown"),
                    amount=abs(float(t.get("amount", 0))),
                    category=t.get("category", {}).get("name", "Uncategorized"),
                    account=t.get("account", {}).get("displayName", "Unknown"),
                    is_income=float(t.get("amount", 0)) > 0,
                    notes=t.get("notes", None),
                )
            )
        except Exception as e:
            logger.warning(f"Failed to parse transaction {t.get('id')}: {e}")

    logger.info(f"Fetched {len(transactions)} transactions for the last {days} days.")
    return transactions


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def get_accounts() -> list[Account]:
    mm = _get_monarch_client()
    await _ensure_authenticated(mm)

    raw = await mm.get_accounts()
    accounts = []
    for a in raw.get("accounts", []):
        try:
            accounts.append(
                Account(
                    id=a.get("id", ""),
                    name=a.get("displayName", ""),
                    type=a.get("type", {}).get("name", "unknown"),
                    balance=float(a.get("currentBalance", 0)),
                    institution=a.get("institution", {}).get("name", "Unknown") if a.get("institution") else "Manual",
                )
            )
        except Exception as e:
            logger.warning(f"Failed to parse account {a.get('id')}: {e}")

    logger.info(f"Fetched {len(accounts)} accounts.")
    return accounts


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def get_budgets() -> list[Budget]:
    mm = _get_monarch_client()
    await _ensure_authenticated(mm)

    raw = await mm.get_budgets()
    budgets = []
    for b in raw.get("budgets", []):
        try:
            allocated = float(b.get("amount", 0))
            spent = abs(float(b.get("totalSpending", 0)))
            remaining = allocated - spent
            percent_used = (spent / allocated * 100) if allocated > 0 else 0.0
            budgets.append(
                Budget(
                    category=b.get("category", {}).get("name", "Unknown"),
                    allocated=allocated,
                    spent=spent,
                    remaining=remaining,
                    percent_used=percent_used,
                )
            )
        except Exception as e:
            logger.warning(f"Failed to parse budget: {e}")

    logger.info(f"Fetched {len(budgets)} budget categories.")
    return budgets


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def get_cashflow(months: int = 3) -> list[CashflowMonth]:
    mm = _get_monarch_client()
    await _ensure_authenticated(mm)

    now = datetime.now()
    start = datetime(now.year, now.month, 1) - timedelta(days=30 * (months - 1))
    start_date = start.strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    raw = await mm.get_cashflow(start_date=start_date, end_date=end_date)
    cashflow = []
    for entry in raw.get("summary", []):
        try:
            income = abs(float(entry.get("sumIncome", 0)))
            expenses = abs(float(entry.get("sumExpense", 0)))
            savings = income - expenses
            savings_rate = (savings / income * 100) if income > 0 else 0.0
            cashflow.append(
                CashflowMonth(
                    month=entry.get("month", ""),
                    income=income,
                    expenses=expenses,
                    savings=savings,
                    savings_rate=savings_rate,
                )
            )
        except Exception as e:
            logger.warning(f"Failed to parse cashflow entry: {e}")

    logger.info(f"Fetched cashflow for {len(cashflow)} months.")
    return cashflow


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def get_net_worth_history(months: int = 6) -> list[NetWorthSnapshot]:
    mm = _get_monarch_client()
    await _ensure_authenticated(mm)

    now = datetime.now()
    start = datetime(now.year, now.month, 1) - timedelta(days=30 * (months - 1))
    start_date = start.strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    raw = await mm.get_net_worth(start_date=start_date, end_date=end_date)
    snapshots = []
    for entry in raw.get("netWorthTimeseries", []):
        try:
            snapshots.append(
                NetWorthSnapshot(
                    date=datetime.strptime(entry.get("date", ""), "%Y-%m-%d").date(),
                    assets=float(entry.get("assets", 0)),
                    liabilities=float(entry.get("liabilities", 0)),
                    net_worth=float(entry.get("netWorth", 0)),
                )
            )
        except Exception as e:
            logger.warning(f"Failed to parse net worth snapshot: {e}")

    logger.info(f"Fetched {len(snapshots)} net worth snapshots.")
    return snapshots
