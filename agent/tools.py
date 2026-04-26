import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from langchain.tools import tool

import config
import storage.repository as repo

logger = logging.getLogger(__name__)


@tool
async def get_spending_by_category(days: int = 30) -> str:
    """Get aggregated spending per category for the given number of days."""
    rows = await repo.get_spending_by_category(days=days)
    if not rows:
        return "No expense transactions found."
    lines = [f"Spending by category (last {days} days):"]
    for r in rows:
        lines.append(f"  {r['category']}: ${r['total']:.2f} ({r['count']} transactions)")
    return "\n".join(lines)


@tool
async def get_top_merchants(limit: int = 10, days: int = 30) -> str:
    """Get the top merchants by spend for the given number of days."""
    rows = await repo.get_top_merchants(limit=limit, days=days)
    if not rows:
        return "No merchant data found."
    lines = [f"Top {limit} merchants (last {days} days):"]
    for r in rows:
        lines.append(f"  {r['merchant']}: ${r['total']:.2f} ({r['count']} charges)")
    return "\n".join(lines)


@tool
async def compare_to_baseline(category: str, current_amount: float) -> str:
    """Compare a category's current monthly spend to its historical baseline."""
    baseline = await repo.get_baseline(category)
    if not baseline:
        return f"No baseline found for category '{category}'. This may be a new category."
    avg = baseline["monthly_avg"]
    diff = current_amount - avg
    pct = (diff / avg * 100) if avg > 0 else 0
    direction = "above" if diff > 0 else "below"
    return (
        f"Category '{category}': current ${current_amount:.2f} vs baseline ${avg:.2f}/month. "
        f"That's ${abs(diff):.2f} ({abs(pct):.1f}%) {direction} normal "
        f"(based on {baseline['sample_months']} months of data)."
    )


@tool
async def get_savings_rate(months: int = 3) -> str:
    """Get the monthly savings rate trend for the given number of months."""
    rows = await repo.get_transactions(days=months * 31)
    if not rows:
        return "No transaction data found."

    monthly: dict[str, dict] = {}
    for t in rows:
        month = t["date"][:7]
        if month not in monthly:
            monthly[month] = {"income": 0.0, "expenses": 0.0}
        if t["is_income"]:
            monthly[month]["income"] += t["amount"]
        else:
            monthly[month]["expenses"] += t["amount"]

    lines = ["Savings rate by month:"]
    for m in sorted(monthly.keys()):
        inc = monthly[m]["income"]
        exp = monthly[m]["expenses"]
        savings = inc - exp
        rate = (savings / inc * 100) if inc > 0 else 0
        lines.append(f"  {m}: income ${inc:.2f}, expenses ${exp:.2f}, savings rate {rate:.1f}%")
    return "\n".join(lines)


_NON_SUBSCRIPTION_CATEGORIES = {
    "Groceries", "Restaurants & Bars", "Coffee Shops", "Gas & Electric",
    "Parking & Tolls", "Taxi & Ride Shares", "Postage & Shipping",
    "Gifts", "Shopping", "Miscellaneous", "Personal", "Entertainment & Recreation",
    "Travel & Vacation", "Furniture & Housewares", "Home Improvement",
    "Electronics", "Charity", "Taxes", "Transfer", "Credit Card Payment",
    "Investments (Transfers)", "Loan Repayment",
}


@tool
async def get_subscription_list() -> str:
    """Detect recurring charges that look like subscriptions."""
    rows = await repo.get_transactions(days=90, expense_only=True)
    # Track per-merchant: which months seen, what amounts, and what category
    merchant_months: dict[str, dict] = {}
    merchant_category: dict[str, str] = {}
    for t in rows:
        m = t["merchant"]
        month = t["date"][:7]
        merchant_category.setdefault(m, t["category"])
        if m not in merchant_months:
            merchant_months[m] = {}
        if month not in merchant_months[m]:
            merchant_months[m][month] = []
        merchant_months[m][month].append(t["amount"])

    subscriptions = []
    for merchant, months in merchant_months.items():
        category = merchant_category.get(merchant, "")
        if category in _NON_SUBSCRIPTION_CATEGORIES:
            continue
        if len(months) >= 2:
            all_amounts = [a for amounts in months.values() for a in amounts]
            avg = sum(all_amounts) / len(all_amounts)
            if avg < 500:
                subscriptions.append((merchant, avg, len(months)))

    if not subscriptions:
        return "No recurring subscriptions detected."

    lines = ["Detected recurring charges (subscriptions):"]
    for merchant, avg, month_count in sorted(subscriptions, key=lambda x: -x[1]):
        lines.append(f"  {merchant}: ~${avg:.2f}/month (seen in {month_count} months)")
    return "\n".join(lines)


@tool
async def get_budget_status() -> str:
    """Get all budget categories with percent used and projected overage."""
    from data.fetcher import get_budgets
    budgets = await get_budgets()
    if not budgets:
        return "No budget data available."

    now = datetime.now()
    days_in_month = 30
    day_of_month = now.day
    month_pct = day_of_month / days_in_month * 100

    lines = [f"Budget status (day {day_of_month} of ~{days_in_month}, {month_pct:.0f}% through month):"]
    for b in budgets:
        projected = (b.spent / day_of_month * days_in_month) if day_of_month > 0 else 0
        overage = projected - b.allocated
        status = f"⚠️ Projected +${overage:.2f} over" if overage > 0 else f"✅ On track"
        lines.append(
            f"  {b.category}: ${b.spent:.2f}/${b.allocated:.2f} ({b.percent_used:.0f}%) — {status}"
        )
    return "\n".join(lines)


@tool
async def get_net_worth_trend(months: int = 6) -> str:
    """Get month-over-month net worth trend."""
    from data.fetcher import get_net_worth_history
    snapshots = await get_net_worth_history(months=months)
    if not snapshots:
        return "No net worth history available."

    lines = ["Net worth trend:"]
    prev = None
    for s in sorted(snapshots, key=lambda x: x.date):
        delta = ""
        if prev is not None:
            diff = s.net_worth - prev
            delta = f" ({'+' if diff >= 0 else ''}{diff:.2f})"
        lines.append(f"  {s.date}: ${s.net_worth:.2f}{delta}")
        prev = s.net_worth
    return "\n".join(lines)


@tool
async def get_anomalies(days: int = 7) -> str:
    """Get unusual transactions compared to historical patterns."""
    rows = await repo.get_transactions(days=days, expense_only=True)
    baselines = {b["category"]: b for b in await repo.get_all_baselines()}

    # Compute avg transaction size per category from 90-day history
    history = await repo.get_transactions(days=90, expense_only=True)
    cat_amounts: dict[str, list[float]] = {}
    for t in history:
        cat_amounts.setdefault(t["category"], []).append(t["amount"])
    # Only use categories with at least 3 historical transactions for a reliable avg
    avg_txn_size = {
        cat: sum(amounts) / len(amounts)
        for cat, amounts in cat_amounts.items()
        if len(amounts) >= 3
    }

    anomalies = []
    for t in rows:
        baseline = baselines.get(t["category"])
        # Skip categories with fewer than 2 months of data — baseline is unreliable
        if not baseline or baseline["sample_months"] < 2:
            continue

        avg = avg_txn_size.get(t["category"])
        if avg and avg > 0:
            # Flag if the charge is 3x the typical transaction size for this category
            if t["amount"] > avg * 3 and t["amount"] > 30:
                ratio = t["amount"] / avg
                anomalies.append(
                    f"  ⚠️ {t['merchant']} — ${t['amount']:.2f} on {t['date']} "
                    f"({ratio:.1f}x typical {t['category']} charge of ${avg:.2f})"
                )
        else:
            # Fallback: single transaction exceeds 60% of the monthly budget for this category
            threshold = baseline["monthly_avg"] * 0.6
            if t["amount"] > threshold and t["amount"] > 50:
                pct = t["amount"] / baseline["monthly_avg"] * 100
                anomalies.append(
                    f"  ⚠️ {t['merchant']} — ${t['amount']:.2f} on {t['date']} "
                    f"({pct:.0f}% of monthly {t['category']} budget of ${baseline['monthly_avg']:.2f})"
                )

    if not anomalies:
        return f"No anomalies detected in the last {days} days."
    lines = [f"Anomaly candidates (last {days} days) — verify before flagging:"] + anomalies
    return "\n".join(lines)


@tool
async def get_income_trend(months: int = 3) -> str:
    """Get income consistency and trend over the given months."""
    rows = await repo.get_transactions(days=months * 31, income_only=True)
    monthly: dict[str, float] = {}
    for t in rows:
        month = t["date"][:7]
        monthly[month] = monthly.get(month, 0) + t["amount"]

    if not monthly:
        return "No income data found."

    lines = ["Income trend:"]
    sorted_months = sorted(monthly.keys())
    for i, m in enumerate(sorted_months):
        delta = ""
        if i > 0:
            prev = monthly[sorted_months[i - 1]]
            diff = monthly[m] - prev
            delta = f" ({'+' if diff >= 0 else ''}{diff:.2f} vs prior month)"
        lines.append(f"  {m}: ${monthly[m]:.2f}{delta}")
    return "\n".join(lines)


@tool
async def get_investment_holdings_summary() -> str:
    """Get current investment holdings with value, cost basis, and gain/loss for each position."""
    from data.fetcher import get_investment_holdings
    holdings = await get_investment_holdings()
    if not holdings:
        return "No investment holdings found."

    total_value = sum(h.value for h in holdings)
    total_basis = sum(h.cost_basis for h in holdings if h.cost_basis)
    total_gain = sum(h.gain_loss for h in holdings if h.gain_loss is not None)

    lines = [f"Investment holdings (total value: ${total_value:,.2f}):"]
    for h in sorted(holdings, key=lambda x: -x.value):
        ticker = f" ({h.ticker})" if h.ticker else ""
        pct_of_portfolio = (h.value / total_value * 100) if total_value else 0
        gl = ""
        if h.gain_loss is not None:
            sign = "+" if h.gain_loss >= 0 else ""
            gl = f"  G/L: {sign}${h.gain_loss:,.2f} ({sign}{h.gain_loss_pct:.1f}%)" if h.gain_loss_pct is not None else f"  G/L: {sign}${h.gain_loss:,.2f}"
        lines.append(
            f"  {h.name}{ticker} [{h.account}]"
            f"  {h.quantity:.4f} units @ ${h.value:,.2f} ({pct_of_portfolio:.1f}% of portfolio){gl}"
        )

    if total_basis:
        total_gl_pct = (total_gain / total_basis * 100) if total_basis else 0
        sign = "+" if total_gain >= 0 else ""
        lines.append(f"\nTotal gain/loss: {sign}${total_gain:,.2f} ({sign}{total_gl_pct:.1f}%) vs cost basis ${total_basis:,.2f}")

    return "\n".join(lines)


@tool
async def get_investment_accounts_summary() -> str:
    """Get a summary of investment and retirement accounts with balances."""
    from data.fetcher import get_investment_accounts
    accounts = await get_investment_accounts()
    if not accounts:
        return "No investment accounts found."

    total = sum(a.balance for a in accounts)
    lines = [f"Investment accounts (total: ${total:,.2f}):"]
    for a in sorted(accounts, key=lambda x: -x.balance):
        lines.append(f"  {a.name} [{a.institution}] ({a.type}): ${a.balance:,.2f}")
    return "\n".join(lines)


@tool
async def get_recent_transactions(limit: int = 20, days: int = 30) -> str:
    """Get the most recent N individual transactions from the last M days, ordered newest first."""
    rows = await repo.get_transactions(days=days, limit=limit)
    if not rows:
        return f"No transactions found in the last {days} days."
    lines = [f"Last {len(rows)} transactions (past {days} days):"]
    for t in rows:
        kind = "income" if t["is_income"] else "expense"
        notes = f" — {t['notes']}" if t.get("notes") else ""
        lines.append(
            f"  {t['date']}  {t['merchant']:<30}  ${t['amount']:>8.2f}  [{t['category']}] ({kind}){notes}"
        )
    return "\n".join(lines)


@tool
async def get_portfolio_symbols() -> str:
    """Return all ticker symbols currently held in investment accounts, with name, account, and quantity."""
    from data.fetcher import get_investment_holdings
    holdings = await get_investment_holdings()
    if not holdings:
        return "No investment holdings found."

    lines = ["Held symbols:"]
    for h in sorted(holdings, key=lambda x: -x.value):
        ticker = h.ticker.upper() if h.ticker else "(no ticker)"
        lines.append(f"  {ticker}  |  {h.name}  |  {h.account}  |  qty: {h.quantity:.4f}  |  value: ${h.value:,.2f}")
    return "\n".join(lines)


@tool
async def calculate_pnl_for_symbols(symbols: list[str]) -> str:
    """Given a list of ticker symbols, fetch live prices from Finnhub and calculate day P&L
    for each position using current holdings quantities. Results are sorted most-to-least profitable."""
    from data.fetcher import get_investment_holdings
    if not config.FINNHUB_API_KEY:
        return "FINNHUB_API_KEY is not configured."
    if not symbols:
        return "No symbols provided."

    symbols = [s.upper() for s in symbols]

    # Load holdings to get quantities per ticker
    holdings = await get_investment_holdings()
    holdings_by_ticker: dict[str, list] = {}
    for h in holdings:
        if h.ticker:
            holdings_by_ticker.setdefault(h.ticker.upper(), []).append(h)

    # Fetch quotes concurrently
    async def fetch_quote(session: aiohttp.ClientSession, symbol: str):
        try:
            async with session.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": config.FINNHUB_API_KEY},
            ) as resp:
                data = await resp.json()
                return symbol, data if data.get("c", 0) != 0 else None
        except Exception as e:
            return symbol, None

    async with aiohttp.ClientSession() as session:
        quotes = dict(await asyncio.gather(*(fetch_quote(session, s) for s in symbols)))

    # Build per-position P&L rows
    rows = []
    for symbol in symbols:
        q = quotes.get(symbol)
        if not q:
            rows.append({"symbol": symbol, "error": "no quote data"})
            continue
        current = q["c"]
        prev_close = q["pc"]
        day_change = current - prev_close
        day_change_pct = float(q.get("dp", (day_change / prev_close * 100) if prev_close else 0))

        positions = holdings_by_ticker.get(symbol, [])
        if positions:
            for h in positions:
                rows.append({
                    "symbol": symbol,
                    "name": h.name,
                    "account": h.account,
                    "quantity": h.quantity,
                    "price": current,
                    "day_change": day_change,
                    "day_change_pct": day_change_pct,
                    "position_value": h.quantity * current,
                    "day_pnl": h.quantity * day_change,
                })
        else:
            # Symbol requested but not in holdings — still show price
            rows.append({
                "symbol": symbol,
                "name": symbol,
                "account": "—",
                "quantity": None,
                "price": current,
                "day_change": day_change,
                "day_change_pct": day_change_pct,
                "position_value": None,
                "day_pnl": None,
            })

    # Sort by day P&L descending (positions without P&L go last)
    rows.sort(key=lambda r: r.get("day_pnl") or float("-inf"), reverse=True)

    lines = [f"P&L for {', '.join(symbols)} ({datetime.now().strftime('%Y-%m-%d')}):"]
    total_pnl = 0.0
    total_value = 0.0
    for r in rows:
        if "error" in r:
            lines.append(f"  {r['symbol']}: {r['error']}")
            continue
        sign = "+" if r["day_change"] >= 0 else ""
        if r["day_pnl"] is not None:
            pnl_sign = "+" if r["day_pnl"] >= 0 else ""
            total_pnl += r["day_pnl"]
            total_value += r["position_value"]
            lines.append(
                f"  {r['symbol']} [{r['account']}]  qty {r['quantity']:.4f}"
                f"  @ ${r['price']:.2f} ({sign}{r['day_change_pct']:.2f}%)"
                f"  |  value: ${r['position_value']:,.2f}"
                f"  |  day P&L: {pnl_sign}${r['day_pnl']:,.2f}"
            )
        else:
            lines.append(
                f"  {r['symbol']}  @ ${r['price']:.2f} ({sign}{r['day_change_pct']:.2f}%)  (not in holdings)"
            )

    if total_value:
        total_sign = "+" if total_pnl >= 0 else ""
        lines.append(f"\nTotal value: ${total_value:,.2f}  |  Total day P&L: {total_sign}${total_pnl:,.2f}")
    return "\n".join(lines)


@tool
async def get_portfolio_daily_pnl() -> str:
    """Get today's P&L for all investment holdings by fetching live prices from Finnhub for each held ticker."""
    from data.fetcher import get_investment_holdings
    if not config.FINNHUB_API_KEY:
        return "FINNHUB_API_KEY is not configured."

    holdings = await get_investment_holdings()
    if not holdings:
        return "No investment holdings found."

    # Collect unique tickers that have a symbol
    ticker_map: dict[str, list] = {}
    for h in holdings:
        if h.ticker:
            ticker_map.setdefault(h.ticker.upper(), []).append(h)

    if not ticker_map:
        return "No ticker symbols found in holdings."

    # Fetch all quotes concurrently
    async def fetch_quote(session: aiohttp.ClientSession, symbol: str) -> tuple[str, dict | str]:
        try:
            async with session.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": config.FINNHUB_API_KEY},
            ) as resp:
                if resp.status != 200:
                    return symbol, f"HTTP {resp.status}"
                data = await resp.json()
                return symbol, data if data.get("c", 0) != 0 else "No data"
        except Exception as e:
            return symbol, f"Error: {e}"

    async with aiohttp.ClientSession() as session:
        quotes = dict(await asyncio.gather(*(fetch_quote(session, s) for s in ticker_map)))

    lines = [f"Portfolio day P&L ({datetime.now().strftime('%Y-%m-%d')}):"]
    total_day_pnl = 0.0
    total_value = 0.0

    for symbol, holding_list in sorted(ticker_map.items()):
        q = quotes.get(symbol)
        if not isinstance(q, dict):
            lines.append(f"  {symbol}: {q}")
            continue
        current = q["c"]
        prev_close = q["pc"]
        day_change = current - prev_close
        day_change_pct = q.get("dp", (day_change / prev_close * 100) if prev_close else 0)
        sign = "+" if day_change >= 0 else ""

        for h in holding_list:
            pos_value = h.quantity * current
            pos_day_pnl = h.quantity * day_change
            total_day_pnl += pos_day_pnl
            total_value += pos_value
            pnl_sign = "+" if pos_day_pnl >= 0 else ""
            lines.append(
                f"  {symbol} [{h.account}]  {h.quantity:.4f} units"
                f"  @ ${current:.2f}"
                f"  |  Stock day chg%: {sign}{day_change_pct:.2f}%"
                f"  |  Value: ${pos_value:,.2f}"
                f"  |  Day P&L: {pnl_sign}${pos_day_pnl:,.2f}"
            )

    total_sign = "+" if total_day_pnl >= 0 else ""
    lines.append(f"\nTotal portfolio value: ${total_value:,.2f}")
    lines.append(f"Total day P&L: {total_sign}${total_day_pnl:,.2f}")
    return "\n".join(lines)


@tool
async def get_stock_prices(symbols: list[str]) -> str:
    """Get current stock price, change, and key quote data for a list of ticker symbols using Finnhub."""
    if not config.FINNHUB_API_KEY:
        return "FINNHUB_API_KEY is not configured."
    if not symbols:
        return "No symbols provided."

    async def fetch_quote(session: aiohttp.ClientSession, symbol: str) -> tuple[str, dict | str]:
        url = "https://finnhub.io/api/v1/quote"
        try:
            async with session.get(url, params={"symbol": symbol, "token": config.FINNHUB_API_KEY}) as resp:
                if resp.status != 200:
                    return symbol, f"HTTP {resp.status}"
                data = await resp.json()
                if data.get("c", 0) == 0:
                    return symbol, "No data (invalid symbol or market closed)"
                return symbol, data
        except Exception as e:
            return symbol, f"Error: {e}"

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(fetch_quote(session, s.upper()) for s in symbols))

    lines = ["Stock prices (Finnhub):"]
    for symbol, data in results:
        if isinstance(data, str):
            lines.append(f"  {symbol}: {data}")
            continue
        current = data["c"]
        prev_close = data["pc"]
        change = current - prev_close
        change_pct = data.get("dp", (change / prev_close * 100) if prev_close else 0)
        high = data["h"]
        low = data["l"]
        sign = "+" if change >= 0 else ""
        lines.append(
            f"  {symbol}: ${current:.2f}  {sign}{change:.2f} ({sign}{change_pct:.2f}%)"
            f"  |  H: ${high:.2f}  L: ${low:.2f}  |  Prev close: ${prev_close:.2f}"
        )
    return "\n".join(lines)


@tool
async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for news and information via SearXNG.
    Use this to look up why a stock moved, recent earnings, or market news.
    Example: web_search("TSM stock news today") or web_search("NVDA earnings April 2026")
    """
    if not config.SEARXNG_URL:
        return "Web search is not configured (SEARXNG_URL not set)."

    params = {
        "q": query,
        "format": "json",
        "categories": "news,general",
        "language": "en",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{config.SEARXNG_URL}/search",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return f"Search failed: HTTP {resp.status}"
                data = await resp.json()
    except Exception as e:
        return f"Search error: {e}"

    results = data.get("results", [])[:max_results]
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for '{query}':"]
    for r in results:
        title = r.get("title", "").strip()
        snippet = r.get("content", "").strip()
        published = r.get("publishedDate", "")
        date_str = f" [{published[:10]}]" if published else ""
        lines.append(f"\n• {title}{date_str}")
        if snippet:
            lines.append(f"  {snippet[:200]}")
    return "\n".join(lines)


@tool
def calculate(expression: str) -> str:
    """Safely evaluate a mathematical expression and return the result.
    Supports: +, -, *, /, **, (, ), and basic math (abs, round, min, max).
    Use this whenever you need to derive a number — percentages, deltas, ratios, etc.
    Example: calculate("1766.06 / (131682.50 - 1766.06) * 100") -> portfolio day change %
    """
    import ast
    import operator

    _ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }
    _safe_funcs = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
    }

    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ops:
            left, right = _eval(node.left), _eval(node.right)
            if type(node.op) is ast.Div and right == 0:
                raise ZeroDivisionError("Division by zero")
            return _ops[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ops:
            return _ops[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _safe_funcs:
            args = [_eval(a) for a in node.args]
            return _safe_funcs[node.func.id](*args)
        raise ValueError(f"Unsupported expression: {ast.dump(node)}")

    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval(tree.body)
        return f"{result:g}"
    except ZeroDivisionError:
        return "Error: division by zero"
    except Exception as e:
        return f"Error: {e}"


ALL_TOOLS = [
    get_spending_by_category,
    get_top_merchants,
    compare_to_baseline,
    get_savings_rate,
    get_subscription_list,
    get_budget_status,
    get_net_worth_trend,
    get_anomalies,
    get_income_trend,
    get_recent_transactions,
    get_investment_holdings_summary,
    get_investment_accounts_summary,
    get_portfolio_symbols,
    calculate_pnl_for_symbols,
    get_portfolio_daily_pnl,
    get_stock_prices,
    web_search,
    calculate,
]
