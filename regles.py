"""Règles de conformité, communes aux données SEC et Yahoo."""
from __future__ import annotations

import re
from datetime import date

SPAC = re.compile(r"\b(acquisition|blank check|capital pool)\s+(corp|co|company|ltd|limited|inc|holdings)\b", re.I)

CONFORME, EXAMINER, EXCLU, ATTENTE = "conforme", "examiner", "exclu", "attente"


def exclusion_prealable(entree: dict, cfg: dict) -> str | None:
    """Motif d'exclusion connu avant tout téléchargement (secteur de la liste, SIC, manuel)."""
    if entree["symbol"] in cfg.get("exclusions_manuelles", []):
        return "Exclusion manuelle"
    if SPAC.search(entree.get("name") or ""):
        return "Société d’acquisition (SPAC) : trésorerie placée en titres à intérêt"
    if entree.get("derive_seul"):
        return "Seul un bon de souscription ou une unité est coté (produit dérivé)"
    secteur = (entree.get("list_sector") or "").lower()
    for s in cfg.get("secteurs_listes_exclus", []):
        if s.lower() in secteur:
            return f"Secteur exclu : {entree['list_sector']}"
    sic = entree.get("sic")
    if entree.get("sic_connu") and sic is None:
        return "Fonds ou véhicule de placement (aucun code d’activité à la SEC)"
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
        # En IFRS, les intérêts sur baux sont comptés en charges financières ; en normes américaines,
        # le loyer d'un bail ordinaire n'est pas un intérêt. Et des intérêts supérieurs à 12 % du
        # montant des baux ne peuvent pas venir des seuls baux.
        baux_plausibles = d.get("norme") != "us-gaap" and leases > 0 and iexp <= 0.12 * leases
        if fdebt == 0 and baux_plausibles:
            examiner.append("Intérêts versés, possiblement liés aux seuls baux")
        else:
            exclu.append("Verse des intérêts")

    flux = d.get("debt_flows")
    if flux is not None and flux > dtol and not any(m.startswith("Dette") for m in exclu):
        examiner.append("Emprunts ou remboursements récents (flux de trésorerie)")

    iinc = d.get("interest_income")
    if iinc is not None and iinc > itol:
        (exclu if cfg.get("interest_income_policy") == "exclure" else examiner).append(
            "Perçoit des intérêts sur sa trésorerie")

    if d.get("source") == "SEC" and not d.get("debt_tagged") and (d.get("assets") or 0) > 2e9:
        examiner.append("Grande société sans dette détectée dans les étiquettes standard : vérifier le bilan")

    bilan = d.get("balance_sheet_date")
    if bilan and (date.today() - date.fromisoformat(bilan[:10])).days > 270:
        examiner.append(f"Bilan de plus de 9 mois ({bilan[:10]})")

    if d.get("cross_debt"):
        exclu.append("Dette ou intérêts relevés par Yahoo Finance (vérification croisée)")

    profil = profil_financier(d)
    if cfg.get("exiger_rentabilite") and profil != "Rentable":
        exclu.append(f"Non rentable ({profil.lower()})")

    status = EXCLU if exclu else EXAMINER if examiner else CONFORME
    return {**d, "financial_debt": fdebt, "status": status, "reasons": exclu + examiner, "profil": profil}


def profil_financier(d: dict) -> str:
    """Rentable : bénéfice net ET flux de trésorerie d'exploitation positifs sur le dernier exercice."""
    ni, ocf, rev = d.get("net_income"), d.get("ocf"), d.get("revenue")
    if rev is not None and rev <= 0 and (ni is None or ni <= 0):
        return "Sans revenus"
    if ni is None or ocf is None:
        return "Inconnu"
    return "Rentable" if ni > 0 and ocf > 0 else "Déficitaire"


def sans_donnees(r: dict) -> bool:
    """Société pour laquelle la source n'a pas fourni de quoi juger la dette."""
    return r["status"] == EXAMINER and any(
        m.startswith(("Données indisponibles", "Dette non publiée")) for m in r["reasons"])
