from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel


class Transaction(BaseModel):
    id: str
    date: date
    merchant: str
    amount: float
    category: str
    account: str
    is_income: bool
    notes: Optional[str] = None


class Account(BaseModel):
    id: str
    name: str
    type: str
    balance: float
    institution: str


class Budget(BaseModel):
    category: str
    allocated: float
    spent: float
    remaining: float
    percent_used: float


class CashflowMonth(BaseModel):
    month: str  # "YYYY-MM"
    income: float
    expenses: float
    savings: float
    savings_rate: float


class NetWorthSnapshot(BaseModel):
    date: date
    assets: float
    liabilities: float
    net_worth: float


class AnalysisResult(BaseModel):
    timestamp: datetime
    type: str  # "daily_digest", "anomaly_check", "weekly_report", "monthly_review"
    summary: str
    alerts: list[str]
    score: Optional[float] = None
    raw_response: str
