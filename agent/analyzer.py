import logging
import re
from datetime import datetime

from agent.llm import get_llm
from agent.tools import ALL_TOOLS
from agent.react import run_react
import agent.prompts as prompts
from data.models import AnalysisResult
import storage.repository as repo
from web.repository import get_prompt_override

logger = logging.getLogger(__name__)


async def _run_analysis(analysis_type: str, system_prompt: str, user_message: str) -> AnalysisResult:
    try:
        raw = await run_react(get_llm(), ALL_TOOLS, system_prompt, user_message)
        return AnalysisResult(
            timestamp=datetime.now(),
            type=analysis_type,
            summary=raw[:500],
            alerts=[],
            raw_response=raw,
        )
    except Exception as e:
        logger.error("Analysis failed for %s: %s", analysis_type, e)
        return AnalysisResult(
            timestamp=datetime.now(),
            type=analysis_type,
            summary=f"Analysis failed: {e}",
            alerts=[],
            raw_response=str(e),
        )


async def daily_digest() -> AnalysisResult:
    today = datetime.now().strftime("%B %d, %Y")
    override = await get_prompt_override("daily_digest")
    system = (override or prompts.DAILY_DIGEST_SYSTEM).format(date=today)
    message = (
        "/no_think "
        "Please generate today's daily financial digest. "
        "Use the available tools to fetch yesterday's spending, budget status, "
        "subscription detection, savings rate, and net worth delta. "
        "Be specific with all dollar amounts."
    )
    result = await _run_analysis("daily_digest", system, message)
    await repo.save_analysis_result(result)
    logger.info("Daily digest complete.")
    return result


async def anomaly_check() -> AnalysisResult:
    override = await get_prompt_override("anomaly_check")
    system = override or prompts.ANOMALY_CHECK_SYSTEM
    message = (
        "/think "
        "Run an intelligent anomaly detection scan. Use get_spending_by_category, "
        "get_top_merchants, get_anomalies, and get_subscription_list to gather context. "
        "Do not just flag rule-based thresholds — reason about what is genuinely unusual "
        "given the user's overall spending patterns. Consider timing, category context, "
        "merchant history, and combinations of signals that together suggest something worth flagging."
    )
    result = await _run_analysis("anomaly_check", system, message)

    alerts = [
        line.strip()
        for line in result.raw_response.split("\n")
        if any(m in line for m in ["🚨", "⚠️", "•"]) and "$" in line
    ]
    result.alerts = alerts

    await repo.save_analysis_result(result)
    logger.info("Anomaly check complete. Found %d alerts.", len(alerts))
    return result


async def weekly_report() -> AnalysisResult:
    week_start = datetime.now().strftime("%B %d, %Y")
    override = await get_prompt_override("weekly_report")
    system = (override or prompts.WEEKLY_REPORT_SYSTEM).format(date=week_start)
    message = (
        "/no_think "
        "Generate the weekly financial report. Compare this week's spending to prior week "
        "by category. Identify top 3 overspend categories and top 3 wins. "
        "Calculate savings rate. Report monthly budget progress. "
        "Identify one behavioral spending pattern. Use all available tools."
    )
    result = await _run_analysis("weekly_report", system, message)
    await repo.save_analysis_result(result)
    logger.info("Weekly report complete.")
    return result


async def snapshot_investments() -> int:
    """Fetch live prices for all holdings and store the day's snapshot. Returns rows saved."""
    import aiohttp
    import asyncio
    import config
    from data.fetcher import get_investment_holdings

    holdings = await get_investment_holdings()
    tickers = {h.ticker.upper() for h in holdings if h.ticker}
    if not tickers:
        logger.warning("snapshot_investments: no tickers found in holdings.")
        return 0

    async def fetch_quote(session: aiohttp.ClientSession, symbol: str):
        try:
            async with session.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": config.FINNHUB_API_KEY},
            ) as resp:
                data = await resp.json()
                return symbol, data if data.get("c", 0) != 0 else None
        except Exception as e:
            logger.warning("Finnhub quote failed for %s: %s", symbol, e)
            return symbol, None

    async with aiohttp.ClientSession() as session:
        raw = await asyncio.gather(*(fetch_quote(session, s) for s in tickers))
    quotes = {s: q for s, q in raw if q}

    today = datetime.now().strftime("%Y-%m-%d")
    rows = []
    for h in holdings:
        if not h.ticker:
            continue
        q = quotes.get(h.ticker.upper())
        if not q:
            continue
        current = float(q["c"])
        prev_close = float(q["pc"])
        day_change = current - prev_close
        day_change_pct = float(q.get("dp", (day_change / prev_close * 100) if prev_close else 0))
        rows.append({
            "date": today,
            "symbol": h.ticker.upper(),
            "account": h.account,
            "quantity": h.quantity,
            "price": current,
            "prev_close": prev_close,
            "day_change": day_change,
            "day_change_pct": day_change_pct,
            "position_value": h.quantity * current,
            "day_pnl": h.quantity * day_change,
        })

    count = await repo.save_investment_snapshot(rows)
    logger.info("Investment snapshot saved: %d positions for %s.", count, today)
    return count


async def investment_tracker() -> AnalysisResult:
    today = datetime.now().strftime("%B %d, %Y")
    override = await get_prompt_override("investment_tracker")
    system = (override or prompts.INVESTMENT_TRACKER_SYSTEM).format(date=today)
    message = (
        "/no_think "
        "Generate the weekly investment tracker report. "
        "Use get_investment_accounts_summary and get_investment_holdings_summary to fetch portfolio data. "
        "Use get_net_worth_trend to show investment accounts in context of overall net worth. "
        "Be specific with dollar amounts and percentages."
    )
    result = await _run_analysis("investment_tracker", system, message)
    await repo.save_analysis_result(result)
    logger.info("Investment tracker complete.")
    return result


async def monthly_review() -> AnalysisResult:
    month = datetime.now().strftime("%B %Y")
    override = await get_prompt_override("monthly_review")
    system = (override or prompts.MONTHLY_REVIEW_SYSTEM).format(month=month)
    message = (
        "/think "
        "Generate the full monthly financial review. Include: income vs expenses, "
        "savings rate vs goal, net worth change, subscription audit, category trends "
        "vs prior 3 months, financial health score (1-10) with reasoning, "
        "and 3 specific recommendations for next month. Use all available tools."
    )
    result = await _run_analysis("monthly_review", system, message)

    if not result.summary.startswith("Analysis failed"):
        match = re.search(r"(\d+(?:\.\d+)?)/10", result.raw_response)
        if match:
            result.score = float(match.group(1))

    await repo.save_analysis_result(result)
    if result.summary.startswith("Analysis failed"):
        logger.error("Monthly review did not complete: %s", result.summary)
    else:
        logger.info("Monthly review complete.")
    return result
