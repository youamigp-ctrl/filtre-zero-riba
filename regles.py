"""Règles de conformité, communes aux données SEC et Yahoo. Fonctions pures."""
from __future__ import annotations

CONFORME, EXAMINER, EXCLU, ATTENTE = "conforme", "examiner", "exclu", "attente"


def exclusion_prealable(entree: dict, cfg: dict) -> str | None:
    """Motif d'exclusion connu avant tout téléchargement (secteur de la liste, SIC, manuel)."""
    if entree["symbol"] in cfg.get("exclusions_manuelles", []):
        return "Exclusion manuelle"
    secteur = (entree.get("list_sector") or "").lower()
    for s in cfg.get("secteurs_listes_exclus", []):
        if s.lower() in secteur:
            return f"Secteur exclu : {entree['list_sector']}"
    sic = entree.get("sic")
    if sic is not None:
        for lo, hi in cfg.get("sic_exclus", []):
            if lo <= sic <= hi:
                return f"Activité exclue : {entree.get('sic_desc') or 'code SIC ' + str(sic)}"
    return None


def evaluer(d: dict, cfg: dict) -> dict:
    """Statut et motifs à partir des données normalisées d'une société."""
    exclu, examiner = [], []
    dtol, itol = cfg.get("debt_tolerance", 0), cfg.get("interest_tolerance", 0)

    if d.get("error"):
        return {**d, "status": EXAMINER, "reasons": [f"Données indisponibles : {d['error'][:120]}"]}

    if d.get("sector") in cfg.get("excluded_sectors", []):
        exclu.append(f"Secteur exclu : {d['sector']}")
    if d.get("industry") in cfg.get("excluded_industries", []):
        exclu.append(f"Activité exclue : {d['industry']}")

    leases = d.get("leases") or 0
    fdebt = d.get("financial_debt")
    if fdebt is None and d.get("total_debt") is not None:
        fdebt = max(d["total_debt"] - leases, 0)

    if fdebt is None:
        if not exclu:
            examiner.append("Dette non publiée : vérifier le bilan")
    elif fdebt > dtol:
        exclu.append("Dette financière portant intérêt")

    if leases > dtol:
        (exclu if cfg.get("lease_policy") == "exclure" else examiner).append("Obligations locatives (baux)")

    iexp = d.get("interest_expense")
    if iexp is not None and iexp > itol:
        if fdebt == 0 and leases > 0:
            examiner.append("Intérêts versés, possiblement liés aux seuls baux")
        else:
            exclu.append("Verse des intérêts")

    iinc = d.get("interest_income")
    if iinc is not None and iinc > itol:
        (exclu if cfg.get("interest_income_policy") == "exclure" else examiner).append(
            "Perçoit des intérêts sur sa trésorerie")

    status = EXCLU if exclu else EXAMINER if examiner else CONFORME
    return {**d, "financial_debt": fdebt, "status": status, "reasons": exclu + examiner}


def sans_donnees(r: dict) -> bool:
    """Société pour laquelle la source n'a pas fourni de quoi juger la dette."""
    return r["status"] == EXAMINER and any(
        m.startswith(("Données indisponibles", "Dette non publiée")) for m in r["reasons"])
