"""Données officielles de la SEC (sociétés américaines et étrangères déposant à la SEC).

- company_tickers_exchange.json : symboles, CIK et bourse
- submissions/CIK##########.json : code SIC (mis en cache, il ne change presque jamais)
- companyfacts.zip : toutes les données comptables XBRL, en un seul fichier
"""
from __future__ import annotations

import json
import os
import re
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{:010d}.json"
FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
PAUSE = 0.12  # ≈ 8 requêtes par seconde, sous la limite de 10 fixée par la SEC

DEBT = {
    "us-gaap": ["LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtCurrent", "DebtCurrent",
                "ShortTermBorrowings", "CommercialPaper", "LineOfCredit", "LongTermLineOfCredit",
                "ConvertibleNotesPayable", "ConvertibleNotesPayableCurrent", "ConvertibleDebtNoncurrent",
                "NotesPayable", "NotesPayableCurrent", "LongTermNotesPayable", "SeniorNotes",
                "SecuredDebt", "UnsecuredDebt", "OtherLongTermDebt", "OtherBorrowings",
                "LongTermDebtAndCapitalLeaseObligations", "LongTermDebtAndCapitalLeaseObligationsCurrent",
                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", "DebtAndCapitalLeaseObligations",
                "DebtLongtermAndShorttermCombinedAmount", "LongTermDebtFairValue", "DebtInstrumentCarryingAmount",
                "LinesOfCreditCurrent", "SecuredLongTermDebt", "UnsecuredLongTermDebt", "SeniorLongTermNotes",
                "OtherNotesPayable", "OtherLongTermDebtNoncurrent", "ShortTermBankLoansAndNotesPayable",
                "LoansPayable", "LoansPayableCurrent", "LongTermLoansPayable"],
    "ifrs-full": ["Borrowings", "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
                  "NoncurrentPortionOfNoncurrentBorrowings", "ShorttermBorrowings", "LongtermBorrowings",
                  "BondsIssued", "NotesAndDebenturesIssued", "BankBorrowings", "LoansReceived",
                  "OtherCurrentBorrowingsAndCurrentPortionOfOtherNoncurrentBorrowings"],
}
LEASES = {
    "us-gaap": ["OperatingLeaseLiability", "FinanceLeaseLiability", "OperatingLeaseLiabilityNoncurrent",
                "OperatingLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent", "FinanceLeaseLiabilityCurrent"],
    "ifrs-full": ["LeaseLiabilities", "NoncurrentLeaseLiabilities", "CurrentLeaseLiabilities"],
}
INT_EXP = {
    "us-gaap": ["InterestExpense", "InterestExpenseDebt", "InterestExpenseNonoperating", "InterestExpenseBorrowings",
                "InterestPaid", "InterestPaidNet", "InterestCostsIncurred"],
    "ifrs-full": ["InterestExpense", "FinanceCosts", "InterestExpenseOnBorrowings"],
}
INT_INC = {
    "us-gaap": ["InvestmentIncomeInterest", "InterestIncomeOther", "InvestmentIncomeInterestAndDividend",
                "InterestIncomeOperating"],
    "ifrs-full": ["InterestRevenueCalculatedUsingEffectiveInterestMethod", "InterestIncome", "FinanceIncome"],
}
DEBT_FLOWS = {
    "us-gaap": ["ProceedsFromIssuanceOfLongTermDebt", "RepaymentsOfLongTermDebt", "ProceedsFromLinesOfCredit",
                "RepaymentsOfLinesOfCredit", "ProceedsFromRepaymentsOfShortTermDebt", "RepaymentsOfDebt",
                "ProceedsFromIssuanceOfDebt", "ProceedsFromConvertibleDebt", "ProceedsFromNotesPayable",
                "RepaymentsOfNotesPayable", "ProceedsFromIssuanceOfSeniorLongTermDebt", "RepaymentsOfShortTermDebt",
                "ProceedsFromShortTermDebt", "ProceedsFromRepaymentsOfLinesOfCredit"],
    "ifrs-full": ["ProceedsFromBorrowingsClassifiedAsFinancingActivities",
                  "RepaymentsOfBorrowingsClassifiedAsFinancingActivities"],
}
ASSETS = {"us-gaap": ["Assets"], "ifrs-full": ["Assets"]}
CURRENCY = re.compile(r"^[A-Z]{3}$")


def _session():
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in ua:
        raise RuntimeError("SEC_USER_AGENT manquant : la SEC exige un nom et une adresse courriel "
                           "(par exemple « FiltreZeroRiba vous@exemple.com »).")
    s = requests.Session()
    s.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
    return s


# ------------------------------------------------------------------ univers
def univers_us(cfg) -> list[dict]:
    s = _session()
    raw = s.get(TICKERS_URL, timeout=60).json()
    idx = {k: i for i, k in enumerate(raw["fields"])}
    bourses = set(cfg.get("us_bourses", ["NYSE", "Nasdaq"]))
    par_cik = {}
    for row in raw["data"]:
        cik, ticker, ex = row[idx["cik"]], row[idx["ticker"]], row[idx["exchange"]]
        if ex not in bourses or not ticker:
            continue
        e = par_cik.setdefault(cik, {"symbol": ticker, "name": row[idx["name"]], "exchange": ex,
                                     "cik": cik, "autres": []})
        if ticker != e["symbol"]:
            e["autres"].append(ticker)
    for e in par_cik.values():  # privilégier l'action ordinaire (pas de bon de souscription W, unité U, droit R)
        tous = [e["symbol"]] + e["autres"]
        tous.sort(key=lambda t: (len(t) == 5 and t[-1] in "WURZ", "-" in t, len(t)))
        e["symbol"], e["autres"] = tous[0], tous[1:]
        e["derive_seul"] = len(tous[0]) == 5 and tous[0][-1] in "WURZ"
    return list(par_cik.values())


def ajouter_sic(entrees: list[dict], cache_path: Path) -> None:
    """Complète chaque entrée avec son code SIC, en ne téléchargeant que les CIK inconnus."""
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    manquants = [e["cik"] for e in entrees if str(e["cik"]) not in cache]
    if manquants:
        s = _session()
        print(f"SEC : codes SIC à récupérer pour {len(manquants)} sociétés", flush=True)
        for i, cik in enumerate(manquants, 1):
            try:
                j = s.get(SUBMISSIONS_URL.format(cik), timeout=30).json()
                cache[str(cik)] = [int(j["sic"]) if j.get("sic") else None, j.get("sicDescription") or ""]
            except Exception:
                pass  # on réessaiera au prochain passage
            if i % 250 == 0:
                cache_path.write_text(json.dumps(cache))
            time.sleep(PAUSE)
        cache_path.write_text(json.dumps(cache))
    for e in entrees:
        sic, desc = cache.get(str(e["cik"]), [None, ""])
        e["sic"], e["sic_desc"] = sic, desc
        e["sic_connu"] = str(e["cik"]) in cache


def telecharger_faits(dest: Path) -> Path:
    s = _session()
    with s.get(FACTS_URL, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return dest


# ------------------------------------------------------------------ extraction
def _unites(facts, ns, concept):
    c = facts.get(ns, {}).get(concept)
    if not c:
        return []
    out = []
    for unit, vals in c.get("units", {}).items():
        if CURRENCY.match(unit):
            out += [{**v, "_unit": unit} for v in vals]
    return out


def _instant(facts, groupe, jour, fenetre=400):
    """Pour chaque étiquette : valeur à la date du bilan, sinon la plus récente des 400 jours précédents
    (certaines sociétés ne détaillent leur dette qu'en fin d'exercice). Renvoie le maximum."""
    limite = (date.fromisoformat(jour) - timedelta(days=fenetre)).isoformat()
    vals = []
    for ns, concepts in groupe.items():
        for c in concepts:
            faits = [v for v in _unites(facts, ns, c) if "start" not in v and limite <= v["end"] <= jour]
            if not faits:
                continue
            fin = max(v["end"] for v in faits)
            vals += [abs(v["val"]) for v in faits if v["end"] == fin]
    return max(vals) if vals else None


def _jours(v):
    return (date.fromisoformat(v["end"]) - date.fromisoformat(v["start"])).days


def _periode(facts, groupe, bilan):
    """Montant annuel le plus récent ; positif si un trimestre ultérieur est positif."""
    annuels, trimestres = [], []
    for ns, concepts in groupe.items():
        for c in concepts:
            for v in _unites(facts, ns, c):
                if "start" not in v:
                    continue
                d = _jours(v)
                if 330 <= d <= 400:
                    annuels.append(v)
                elif 80 <= d <= 100:
                    trimestres.append(v)
    if not annuels and not trimestres:
        return None
    limite = (date.fromisoformat(bilan) - date(1, 1, 1)).days - 550 if bilan else 0
    recent = lambda v: (date.fromisoformat(v["end"]) - date(1, 1, 1)).days >= limite
    annuels = [v for v in annuels if recent(v)]
    trimestres = [v for v in trimestres if recent(v)]
    if not annuels and not trimestres:
        return None  # seulement des données anciennes : poste inconnu aujourd'hui
    fin_annuel = max((v["end"] for v in annuels), default="")
    montant = max((abs(v["val"]) for v in annuels if v["end"] == fin_annuel), default=0.0)
    suivants = [abs(v["val"]) for v in trimestres if v["end"] > fin_annuel]
    if suivants and max(suivants) > 0:
        montant = max(montant, max(suivants))
    return montant


PROFIT = {"us-gaap": ["NetIncomeLoss", "ProfitLoss"], "ifrs-full": ["ProfitLoss"]}
OCF = {"us-gaap": ["NetCashProvidedByUsedInOperatingActivities"],
       "ifrs-full": ["CashFlowsFromUsedInOperatingActivities"]}
REVENUE = {"us-gaap": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
           "ifrs-full": ["Revenue"]}


def _annuel_signe(facts, groupe, bilan):
    """Dernier montant annuel (avec son signe), s'il date de moins de 550 jours."""
    limite = (date.fromisoformat(bilan) - timedelta(days=550)).isoformat()
    vals = []
    for ns, concepts in groupe.items():
        for c in concepts:
            vals += [v for v in _unites(facts, ns, c)
                     if "start" in v and 330 <= _jours(v) <= 400 and v["end"] >= limite]
    if not vals:
        return None
    return max(vals, key=lambda v: (v["end"], v.get("filed", "")))["val"]


def extraire(cf: dict) -> dict:
    facts = cf.get("facts", {})
    actifs = []
    for ns, concepts in ASSETS.items():
        for c in concepts:
            actifs += [v for v in _unites(facts, ns, c) if "start" not in v]
    if not actifs:
        return {"error": "aucun bilan dans les données XBRL de la SEC"}
    dernier = max(actifs, key=lambda v: (v["end"], v.get("filed", "")))
    norme = "ifrs" if "Assets" in facts.get("ifrs-full", {}) and "Assets" not in facts.get("us-gaap", {}) else "us-gaap"
    jour = dernier["end"]
    return {
        "currency": dernier["_unit"],
        "balance_sheet_date": jour,
        "financial_debt": _instant(facts, DEBT, jour) or 0.0,
        "debt_tagged": _instant(facts, DEBT, jour) is not None,
        "assets": abs(dernier["val"]),
        "revenue": _annuel_signe(facts, REVENUE, jour),
        "net_income": _annuel_signe(facts, PROFIT, jour),
        "ocf": _annuel_signe(facts, OCF, jour),
        "leases": _instant(facts, LEASES, jour) or 0.0,
        "interest_expense": _periode(facts, INT_EXP, jour),
        "interest_income": _periode(facts, INT_INC, jour),
        "debt_flows": _periode(facts, DEBT_FLOWS, jour),
        "norme": norme,
        "error": None,
    }


def analyser(entrees: list[dict], zip_path: Path):
    """Produit les données normalisées pour chaque entrée, à partir de companyfacts.zip."""
    with zipfile.ZipFile(zip_path) as z:
        noms = set(z.namelist())
        for e in entrees:
            base = {"symbol": e["symbol"], "name": e["name"], "exchange": e["exchange"],
                    "sector": "", "industry": e.get("sic_desc") or "", "source": "SEC"}
            fichier = f"CIK{e['cik']:010d}.json"
            if fichier not in noms:
                yield {**base, "error": "aucune donnée XBRL à la SEC"}
                continue
            try:
                yield {**base, **extraire(json.loads(z.read(fichier)))}
            except Exception as ex:
                yield {**base, "error": f"lecture impossible ({type(ex).__name__})"}
