import logging
import re
from datetime import datetime

import config
from agent.llm import get_llm, LLMLogger
from agent.tools import ALL_TOOLS
from agent.react import run_react
import agent.prompts as prompts
from data.models import AnalysisResult
import storage.repository as repo
from web.repository import get_prompt_override
import job_state

logger = logging.getLogger(__name__)


async def _run_analysis(analysis_type: str, system_prompt: str, user_message: str) -> AnalysisResult:
    llm_logger = LLMLogger()
    cancel_event = job_state.get_cancel_event(analysis_type)
    try:
        raw = await run_react(
            get_llm(llm_logger), ALL_TOOLS, system_prompt, user_message,
            on_tool_call=lambda: job_state.increment_tool_calls(analysis_type),
            cancel_event=cancel_event,
        )
        return AnalysisResult(
            timestamp=datetime.now(),
            type=analysis_type,
            summary=raw[:500],
            alerts=[],
            raw_response=raw,
            model=config.LLAMA_CPP_MODEL_ID,
            prompt_tokens=llm_logger.total_prompt_tokens or None,
            completion_tokens=llm_logger.total_completion_tokens or None,
            tokens_per_sec=llm_logger.tokens_per_sec,
            latency_seconds=round(llm_logger.total_latency_seconds, 3) or None,
        )
    except Exception as e:
        logger.error("Analysis failed for %s: %s", analysis_type, e)
        return AnalysisResult(
            timestamp=datetime.now(),
            type=analysis_type,
            summary=f"Analysis failed: {e}",
            alerts=[],
            raw_response=str(e),
            model=config.LLAMA_CPP_MODEL_ID,
            prompt_tokens=llm_logger.total_prompt_tokens or None,
            completion_tokens=llm_logger.total_completion_tokens or None,
            tokens_per_sec=llm_logger.tokens_per_sec,
            latency_seconds=round(llm_logger.total_latency_seconds, 3) or None,
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
        if any(m in line for m in ["🚨", "⚠️"]) and "$" in line
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
        f"Today is {week_start}. Generate the weekly financial report. "
        "Call get_spending_by_category with days=7 for this week, then days=14 to get both weeks combined "
        "(subtract to get prior week). Compare each category. "
        "Call get_savings_rate for the savings rate trend. "
        "Call get_recent_transactions with limit=50 to identify a specific behavioral pattern. "
        "Be specific with dollar amounts — show actual numbers for both weeks in every comparison."
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
        f"Today is {today}. Generate the daily investment P&L update. "
        "Step 1: Use get_portfolio_daily_pnl to get P&L data for all holdings. "
        "Step 2: Use calculate to get the total portfolio % change: calculate('day_pnl / (total_value - day_pnl) * 100'). "
        "Step 3: Identify the top 3 best and worst movers by absolute day P&L. "
        "Step 4: For each top mover, call web_search('[ticker] stock news today') to find the reason it moved. "
        "For each stock's %, use the 'Stock day chg%' field from the tool output — never use the dollar Day P&L value as a percentage. "
        "Be specific with dollar amounts and percentages."
    )
    result = await _run_analysis("investment_tracker", system, message)
    await repo.save_analysis_result(result)
    logger.info("Investment tracker complete.")
    return result


async def weekly_investment_tracker() -> AnalysisResult:
    today = datetime.now().strftime("%B %d, %Y")
    override = await get_prompt_override("weekly_investment_tracker")
    system = (override or prompts.WEEKLY_INVESTMENT_TRACKER_SYSTEM).format(date=today)
    message = (
        "/no_think "
        "Generate the weekly investment tracker report. "
        "Use get_investment_accounts_summary and get_investment_holdings_summary for portfolio data. "
        "Use get_portfolio_daily_pnl for today's P&L. "
        "Use get_net_worth_trend with months=3 for the month-by-month net worth numbers. "
        "Be specific with dollar amounts and percentages."
    )
    result = await _run_analysis("weekly_investment_tracker", system, message)
    await repo.save_analysis_result(result)
    logger.info("Weekly investment tracker complete.")
    return result


def _parse_ticker_batches(discovery_text: str, batch_size: int = 2) -> list[list[str]]:
    """Extract ticker symbols from Stage 2 JSON output and group into batches."""
    import json
    try:
        match = re.search(r'\{[\s\S]*\}', discovery_text)
        if match:
            data = json.loads(match.group())
            tickers = []
            for item in data.get("held_signals", []):
                if "ticker" in item:
                    tickers.append(item["ticker"])
            for item in data.get("new_candidates", []):
                if "ticker" in item:
                    tickers.append(item["ticker"])
            tickers = list(dict.fromkeys(tickers))
            if tickers:
                logger.info("Stage 2 JSON parsed: %d tickers extracted.", len(tickers))
                return [tickers[i:i + batch_size] for i in range(0, len(tickers), batch_size)]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Stage 2 JSON parse failed, falling back to regex: %s", e)
    # Fallback: regex heuristic
    skip = {"ETF", "CEO", "GDP", "EPS", "PE", "USA", "US", "NYSE", "HOLD", "HELD", "BUY", "SELL", "AI", "QE", "NEW", "FOR", "NOT", "ALL", "TOP", "THE"}
    tickers = list(dict.fromkeys(re.findall(r'\b([A-Z]{2,5})\b', discovery_text)))
    tickers = [t for t in tickers if t not in skip]
    return [tickers[i:i + batch_size] for i in range(0, len(tickers), batch_size)]


async def stock_research_agent() -> AnalysisResult:
    import asyncio
    today = datetime.now().strftime("%B %d, %Y")
    llm_logger = LLMLogger()

    from agent.tools import (
        get_investment_holdings_summary, get_portfolio_symbols,
        get_investment_accounts_summary, get_net_worth_trend,
        web_search, calculate,
    )

    _job_id = "stock_research"
    _on_tool_call = lambda: job_state.increment_tool_calls(_job_id)

    try:
        # Stage 1 — Holdings Agent (portfolio tools only, no web search)
        job_state.update_stage(_job_id, "holdings")
        stage1_system = await get_prompt_override("stock_research_holdings") or prompts.STOCK_HOLDINGS_SYSTEM
        stage1_message = (
            "/no_think "
            f"Today is {today}. Fetch all portfolio data and output the compact holdings table."
        )
        stage1_result = await run_react(
            get_llm(llm_logger),
            [get_investment_holdings_summary, get_portfolio_symbols,
             get_investment_accounts_summary, get_net_worth_trend],
            stage1_system,
            stage1_message,
            max_steps=5,
            on_tool_call=_on_tool_call,
        )
        logger.info("Stock research Stage 1 (Holdings) complete. Output: %d chars", len(stage1_result))

        # Stage 2 — Discovery Agent (web search only, fresh context)
        job_state.update_stage(_job_id, "discovery")
        stage2_system = await get_prompt_override("stock_research_discovery") or prompts.STOCK_DISCOVERY_SYSTEM
        stage2_message = (
            "/no_think "
            f"Today is {today}. Here is the current portfolio:\n\n"
            f"{stage1_result}\n\n"
            "Research the market and output held-ticker signals + 6-10 new candidates."
        )
        stage2_result = await run_react(
            get_llm(llm_logger),
            [web_search],
            stage2_system,
            stage2_message,
            max_steps=12,
            on_tool_call=_on_tool_call,
        )
        logger.info("Stock research Stage 2 (Discovery) complete. Output: %d chars", len(stage2_result))

        # Stage 3 — Per-Ticker Parallel Agents (each with fresh context)
        job_state.update_stage(_job_id, "ticker research")
        ticker_batches = _parse_ticker_batches(stage2_result)
        logger.info("Stock research Stage 3: %d ticker batches to research in parallel.", len(ticker_batches))

        stage3_system = await get_prompt_override("stock_research_ticker") or prompts.STOCK_TICKER_RESEARCH_SYSTEM

        async def research_batch(batch: list[str]) -> str:
            tickers_str = ", ".join(batch)
            message = (
                "/no_think "
                f"Today is {today}. Research these tickers: {tickers_str}. "
                "Run 3 searches per ticker as instructed and output the summary blocks."
            )
            return await run_react(
                get_llm(llm_logger),
                [web_search],
                stage3_system,
                message,
                max_steps=8,
                on_tool_call=_on_tool_call,
            )

        stage3_raw = await asyncio.gather(
            *[research_batch(batch) for batch in ticker_batches],
            return_exceptions=True,
        )
        stage3_summaries = []
        for i, res in enumerate(stage3_raw):
            if isinstance(res, Exception):
                logger.error("Stage 3 batch %d failed: %s", i, res)
                stage3_summaries.append(f"[Batch {i} research failed: {res}]")
            else:
                stage3_summaries.append(res)
        stage3_combined = "\n\n---\n\n".join(stage3_summaries)
        logger.info("Stock research Stage 3 complete. %d/%d batches succeeded.",
                    sum(1 for r in stage3_raw if not isinstance(r, Exception)), len(stage3_raw))

        # Stage 4 — Synthesis Agent (fresh context, all summaries as input)
        job_state.update_stage(_job_id, "synthesis")
        stage4_system = await get_prompt_override("stock_research_synthesis") or prompts.STOCK_SYNTHESIS_SYSTEM
        stage4_message = (
            "/no_think "
            f"Today is {today}.\n\n"
            f"=== STAGE 2: DISCOVERY (JSON) ===\n{stage2_result}\n\n"
            f"=== STAGE 3: PER-TICKER RESEARCH (JSON) ===\n{stage3_combined}\n\n"
            "Synthesize into the final Telegram-formatted buy/hold/sell report with 3 action items."
        )
        stage4_result = await run_react(
            get_llm(llm_logger),
            [calculate],
            stage4_system,
            stage4_message,
            max_steps=5,
            on_tool_call=_on_tool_call,
        )
        logger.info("Stock research Stage 4 (Synthesis) complete.")

        combined = "\n\n".join([
            "=== STAGE 1: HOLDINGS ===", stage1_result,
            "=== STAGE 2: DISCOVERY ===", stage2_result,
            "=== STAGE 3: PER-TICKER RESEARCH ===", stage3_combined,
            "=== STAGE 4: FINAL REPORT ===", stage4_result,
        ])

        result = AnalysisResult(
            timestamp=datetime.now(),
            type="stock_research",
            summary=stage4_result,
            alerts=[],
            raw_response=combined,
            model=config.LLAMA_CPP_MODEL_ID,
            prompt_tokens=llm_logger.total_prompt_tokens or None,
            completion_tokens=llm_logger.total_completion_tokens or None,
            tokens_per_sec=llm_logger.tokens_per_sec,
            latency_seconds=round(llm_logger.total_latency_seconds, 3) or None,
        )
    except Exception as e:
        logger.error("Stock research agent failed: %s", e)
        result = AnalysisResult(
            timestamp=datetime.now(),
            type="stock_research",
            summary=f"Analysis failed: {e}",
            alerts=[],
            raw_response=str(e),
            model=config.LLAMA_CPP_MODEL_ID,
            prompt_tokens=llm_logger.total_prompt_tokens or None,
            completion_tokens=llm_logger.total_completion_tokens or None,
            tokens_per_sec=llm_logger.tokens_per_sec,
            latency_seconds=round(llm_logger.total_latency_seconds, 3) or None,
        )

    await repo.save_analysis_result(result)
    logger.info("Stock research agent complete.")
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
