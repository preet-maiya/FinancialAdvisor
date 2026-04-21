import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from langchain.tools import tool

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


@tool
async def get_subscription_list() -> str:
    """Detect recurring charges that look like subscriptions."""
    rows = await repo.get_transactions(days=90, expense_only=True)
    merchant_months: dict[str, dict] = {}
    for t in rows:
        m = t["merchant"]
        month = t["date"][:7]
        if m not in merchant_months:
            merchant_months[m] = {}
        if month not in merchant_months[m]:
            merchant_months[m][month] = []
        merchant_months[m][month].append(t["amount"])

    subscriptions = []
    for merchant, months in merchant_months.items():
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

    anomalies = []
    for t in rows:
        baseline = baselines.get(t["category"])
        if baseline:
            daily_avg = baseline["monthly_avg"] / 30
            if t["amount"] > daily_avg * 2 and t["amount"] > 20:
                ratio = t["amount"] / daily_avg if daily_avg > 0 else 0
                anomalies.append(
                    f"  ⚠️ {t['merchant']} — ${t['amount']:.2f} on {t['date']} "
                    f"({ratio:.1f}x daily avg for {t['category']})"
                )

    if not anomalies:
        return f"No anomalies detected in the last {days} days."
    lines = [f"Anomalies detected (last {days} days):"] + anomalies
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
]
