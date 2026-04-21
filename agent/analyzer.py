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

    match = re.search(r"(\d+(?:\.\d+)?)/10", result.raw_response)
    if match:
        result.score = float(match.group(1))

    await repo.save_analysis_result(result)
    logger.info("Monthly review complete.")
    return result
