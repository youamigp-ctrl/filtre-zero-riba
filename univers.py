"""Univers canadien, construit à partir des listes officielles déposées dans listes/.

- Fichiers Excel (.xlsx, .xls) : liste du Groupe TMX (TSX et Bourse de croissance).
  La bourse est lue dans une colonne « Exchange » si elle existe, sinon dans le nom de l'onglet.
- Fichiers (.xlsx ou .csv) dont le nom contient « cse » : liste exportée du site du CSE.
Les en-têtes sont détectés automatiquement, car leur position varie d'un mois à l'autre.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

SUFFIXES = {"TSX": ".TO", "TSXV": ".V", "CSE": ".CN"}
COL_SYMBOLE = ("root ticker", "ticker", "symbol", "symbole")
COL_NOM = ("name", "company", "issuer", "nom", "société")
COL_SECTEUR = ("sector", "industry", "secteur")


def symbole_yahoo(racine: str, bourse: str) -> str:
    s = str(racine).strip().upper().replace(".", "-").replace(" ", "-")
    return s + SUFFIXES[bourse]


def _trouver(colonnes, candidats):
    for cand in candidats:
        for c in colonnes:
            if cand in str(c).strip().lower():
                return c
    return None


def _avec_entetes(brut: pd.DataFrame) -> pd.DataFrame | None:
    """Repère la ligne d'en-têtes dans les 40 premières lignes."""
    for i in range(min(40, len(brut))):
        ligne = [str(x).strip().lower() for x in brut.iloc[i].tolist()]
        if any(any(c in cell for c in COL_SYMBOLE) for cell in ligne):
            df = brut.iloc[i + 1:].copy()
            df.columns = [str(x).strip() for x in brut.iloc[i].tolist()]
            return df
    return None


def _bourse(valeur: str) -> str | None:
    v = str(valeur).upper()
    if "CSE" in v or "CANADIAN SECURITIES" in v:
        return "CSE"
    if "TSXV" in v or "VENTURE" in v or "CROISSANCE" in v:
        return "TSXV"
    if "TSX" in v or "TORONTO" in v:
        return "TSX"
    return None


def _lire_tableau(df: pd.DataFrame, bourse_defaut: str | None, source: str, avert: list) -> list[dict]:
    sym, nom = _trouver(df.columns, COL_SYMBOLE), _trouver(df.columns, COL_NOM)
    sect, col_ex = _trouver(df.columns, COL_SECTEUR), _trouver(df.columns, ("exchange", "bourse", "marché"))
    if sym is None:
        avert.append(f"{source} : colonne des symboles introuvable (colonnes : {', '.join(map(str, df.columns))[:200]})")
        return []
    out = []
    for _, r in df.iterrows():
        racine = r.get(sym)
        if pd.isna(racine) or not str(racine).strip():
            continue
        bourse = _bourse(r.get(col_ex)) if col_ex else None
        bourse = bourse or bourse_defaut
        if bourse is None:
            continue
        out.append({
            "symbol": symbole_yahoo(racine, bourse),
            "name": str(r.get(nom)).strip() if nom and not pd.isna(r.get(nom)) else str(racine),
            "exchange": bourse,
            "list_sector": str(r.get(sect)).strip() if sect and not pd.isna(r.get(sect)) else "",
        })
    return out


def charger_canada(dossier: Path, supplementaires: list[str]) -> tuple[list[dict], list[str]]:
    """Renvoie (entrées, avertissements)."""
    entrees, avert = [], []
    for f in sorted(dossier.glob("*")):
        try:
            if f.suffix.lower() in (".xlsx", ".xls"):
                est_cse = "cse" in f.name.lower()
                lues = 0
                for onglet, brut in pd.read_excel(f, sheet_name=None, header=None).items():
                    df = _avec_entetes(brut)
                    if df is None:
                        continue
                    nouvelles = _lire_tableau(df, "CSE" if est_cse else _bourse(onglet), f"{f.name} / {onglet}", avert)
                    lues += len(nouvelles)
                    entrees += nouvelles
                if lues == 0:
                    avert.append(f"{f.name} : aucune société lue (bourse ou en-têtes non reconnus)")
            elif f.suffix.lower() == ".csv" and "cse" in f.name.lower():
                df = _avec_entetes(pd.read_csv(f, header=None, dtype=str))
                if df is None:
                    avert.append(f"{f.name} : en-têtes introuvables")
                    continue
                entrees += _lire_tableau(df, "CSE", f.name, avert)
        except Exception as e:
            avert.append(f"{f.name} : lecture impossible ({type(e).__name__}: {e})")

    inverse = {v: k for k, v in SUFFIXES.items()}
    for s in supplementaires:
        suffixe = "." + s.rsplit(".", 1)[-1] if "." in s else ""
        if suffixe in inverse:
            entrees.append({"symbol": s, "name": s, "exchange": inverse[suffixe], "list_sector": ""})

    uniques = {}
    for e in entrees:
        uniques.setdefault(e["symbol"], e)
    if not any(f.suffix.lower() in (".xlsx", ".xls", ".csv") for f in dossier.glob("*")):
        avert.append("Aucune liste officielle dans listes/ : seuls les symboles supplémentaires sont analysés.")
    return list(uniques.values()), avert
