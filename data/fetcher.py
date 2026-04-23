import logging
from datetime import datetime, timedelta
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential
from monarchmoney import MonarchMoney

import config
from data.models import Transaction, Account, Budget, CashflowMonth, NetWorthSnapshot, InvestmentHolding

logger = logging.getLogger(__name__)

# Shared client — authenticated once, reused for all calls.
# MonarchMoney creates a new aiohttp session per request so this is safe
# to share across event loops (each scheduler job runs in its own loop).
_mm: Optional[MonarchMoney] = None


async def _get_client() -> MonarchMoney:
    """Return the shared MonarchMoney client, authenticating at most once."""
    global _mm
    if _mm is not None:
        return _mm

    client = MonarchMoney(session_file=config.MONARCH_SESSION_FILE)
    try:
        # Pure file read — no HTTP request when session file is valid
        await client.load_session(config.MONARCH_SESSION_FILE)
        logger.info("Monarch: loaded session from file.")
    except Exception:
        # First run or session file missing/corrupt — do one real login
        await client.login(
            email=config.MONARCH_EMAIL,
            password=config.MONARCH_PASSWORD,
            save_session=True,
        )
        logger.info("Monarch: authenticated (fresh login, session saved).")

    _mm = client
    return _mm


def _invalidate_client() -> None:
    """Force re-authentication on the next call (e.g. after a 401)."""
    global _mm
    _mm = None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=5, max=60))
async def get_transactions(days: int = 30) -> list[Transaction]:
    mm = await _get_client()

    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")

    try:
        raw = await mm.get_transactions(
            start_date=start_date,
            end_date=end_date,
            limit=1000,
        )
    except Exception as e:
        if "401" in str(e) or "unauthorized" in str(e).lower():
            _invalidate_client()
        raise

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


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=5, max=60))
async def get_accounts() -> list[Account]:
    mm = await _get_client()

    try:
        raw = await mm.get_accounts()
    except Exception as e:
        if "401" in str(e) or "unauthorized" in str(e).lower():
            _invalidate_client()
        raise

    accounts = []
    for a in raw.get("accounts", []):
        try:
            accounts.append(
                Account(
                    id=a.get("id", ""),
                    name=a.get("displayName", ""),
                    type=a.get("type", {}).get("name", "unknown"),
                    balance=float(a.get("currentBalance") or 0),
                    institution=a.get("institution", {}).get("name", "Unknown") if a.get("institution") else "Manual",
                )
            )
        except Exception as e:
            logger.warning(f"Failed to parse account {a.get('id')}: {e}")

    logger.info(f"Fetched {len(accounts)} accounts.")
    return accounts


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=5, max=60))
async def get_budgets() -> list[Budget]:
    mm = await _get_client()

    try:
        raw = await mm.get_budgets()
    except Exception as e:
        if "401" in str(e) or "unauthorized" in str(e).lower():
            _invalidate_client()
        raise

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


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=5, max=60))
async def get_cashflow(months: int = 3) -> list[CashflowMonth]:
    mm = await _get_client()

    now = datetime.now()
    start = datetime(now.year, now.month, 1) - timedelta(days=30 * (months - 1))
    start_date = start.strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    try:
        raw = await mm.get_cashflow(start_date=start_date, end_date=end_date)
    except Exception as e:
        if "401" in str(e) or "unauthorized" in str(e).lower():
            _invalidate_client()
        raise

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


INVESTMENT_ACCOUNT_TYPES = {"investment", "brokerage", "retirement", "401k", "ira", "roth"}


async def get_investment_accounts() -> list[Account]:
    accounts = await get_accounts()
    return [
        a for a in accounts
        if a.type.lower() in INVESTMENT_ACCOUNT_TYPES
        or any(kw in a.type.lower() for kw in INVESTMENT_ACCOUNT_TYPES)
    ]


async def get_investment_holdings() -> list[InvestmentHolding]:
    mm = await _get_client()
    inv_accounts = await get_investment_accounts()
    if not inv_accounts:
        logger.warning("No investment accounts found.")
        return []

    holdings = []
    for account in inv_accounts:
        try:
            raw = await mm.get_account_holdings(account_id=account.id)
        except Exception as e:
            if "401" in str(e) or "unauthorized" in str(e).lower():
                _invalidate_client()
            logger.warning("Failed to fetch holdings for account %s: %s", account.name, e)
            continue

        for edge in raw.get("portfolio", {}).get("aggregateHoldings", {}).get("edges", []):
            node = edge.get("node", {})
            try:
                value = float(node.get("totalValue") or 0)
                basis = float(node.get("basis") or 0) or None
                gain_loss = (value - basis) if basis else None
                gain_loss_pct = (gain_loss / basis * 100) if basis else None
                security = node.get("security") or {}
                holdings.append(
                    InvestmentHolding(
                        name=security.get("name", "Unknown"),
                        ticker=security.get("ticker") or None,
                        account=account.name,
                        quantity=float(node.get("quantity") or 0),
                        value=value,
                        cost_basis=basis,
                        gain_loss=gain_loss,
                        gain_loss_pct=gain_loss_pct,
                    )
                )
            except Exception as e:
                logger.warning("Failed to parse holding in %s: %s", account.name, e)

    logger.info("Fetched %d investment holdings across %d accounts.", len(holdings), len(inv_accounts))
    return holdings


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=5, max=60))
async def get_net_worth_history(months: int = 6) -> list[NetWorthSnapshot]:
    mm = await _get_client()

    now = datetime.now()
    start = datetime(now.year, now.month, 1) - timedelta(days=30 * (months - 1))
    start_date = start.strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    try:
        raw = await mm.get_net_worth(start_date=start_date, end_date=end_date)
    except Exception as e:
        if "401" in str(e) or "unauthorized" in str(e).lower():
            _invalidate_client()
        raise

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
