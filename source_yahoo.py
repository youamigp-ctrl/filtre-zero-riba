"""Données Yahoo Finance (sociétés canadiennes), via la bibliothèque yfinance."""
from __future__ import annotations

import math
import time

DEBT_ROWS = ["Total Debt"]
LEASE_ROWS = ["Capital Lease Obligations"]
LT_DEBT_ROWS = ["Long Term Debt"]
ST_DEBT_ROWS = ["Current Debt"]
INT_EXP_ROWS = ["Interest Expense", "Interest Expense Non Operating"]
INT_INC_ROWS = ["Interest Income", "Interest Income Non Operating"]
REVENUE_ROWS = ["Total Revenue", "Operating Revenue"]


def _clean(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _latest(df, rows):
    if df is None or getattr(df, "empty", True):
        return None
    for r in rows:
        if r in df.index:
            for col in df.columns:
                v = _clean(df.loc[r, col])
                if v is not None:
                    return v
    return None


def _ttm(qdf, adf, rows):
    if qdf is not None and not getattr(qdf, "empty", True):
        for r in rows:
            if r in qdf.index:
                vals = [v for v in (_clean(x) for x in qdf.loc[r].iloc[:4]) if v is not None]
                if len(vals) == 4:
                    return sum(abs(v) for v in vals)
    v = _latest(adf, rows)
    return abs(v) if v is not None else None


def fetch(symbol: str, retries: int = 2) -> dict:
    import yfinance as yf

    err = None
    for attempt in range(retries):
        try:
            t = yf.Ticker(symbol)
            info = t.info or {}
            if not info.get("longName") and not info.get("shortName") and not info.get("sector"):
                raise LookupError("symbole introuvable sur Yahoo")
            qbs, abs_ = t.quarterly_balance_sheet, t.balance_sheet
            qis, ais = t.quarterly_income_stmt, t.income_stmt
            bs = qbs if qbs is not None and not qbs.empty else abs_
            bs_date = str(bs.columns[0])[:10] if bs is not None and not bs.empty else None

            total = _latest(bs, DEBT_ROWS)
            leases = _latest(bs, LEASE_ROWS)
            if total is None:
                lt, st = _latest(bs, LT_DEBT_ROWS), _latest(bs, ST_DEBT_ROWS)
                if lt is not None or st is not None:
                    total = (lt or 0) + (st or 0) + (leases or 0)

            return {
                "symbol": symbol,
                "name": info.get("longName") or info.get("shortName") or symbol,
                "sector": info.get("sector") or "",
                "industry": info.get("industry") or "",
                "currency": info.get("financialCurrency") or info.get("currency") or "",
                "total_debt": total,
                "financial_debt": None if total is None else max(total - (leases or 0), 0),
                "leases": leases,
                "interest_expense": _ttm(qis, ais, INT_EXP_ROWS),
                "interest_income": _ttm(qis, ais, INT_INC_ROWS),
                "revenue": _clean(info.get("totalRevenue")),
                "net_income": _clean(info.get("netIncomeToCommon")),
                "ocf": _clean(info.get("operatingCashflow")),
                "balance_sheet_date": bs_date,
                "source": "Yahoo",
                "error": None,
            }
        except LookupError as e:
            return {"symbol": symbol, "name": symbol, "source": "Yahoo", "error": str(e)}
        except Exception as e:  # limite de requêtes, réseau…
            err = e
            time.sleep(4 * (attempt + 1))
    return {"symbol": symbol, "name": symbol, "source": "Yahoo",
            "error": f"{type(err).__name__}: {err}", "transient": True}
