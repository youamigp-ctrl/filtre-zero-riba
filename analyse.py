#!/usr/bin/env python3
"""Filtre « zéro riba » v2 — analyse quotidienne.

1. États-Unis : données officielles de la SEC, toutes les sociétés, chaque jour.
2. Canada : Yahoo Finance en rotation (budget quotidien), à partir des listes TMX et CSE.
3. Rapport web dans docs/, historique des conformes dans data/historique/.

Usage : SEC_USER_AGENT="Nom vous@exemple.com" python analyse.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

import regles
from rapport import ecrire_rapport

ROOT = Path(__file__).parent
CACHE = ROOT / "data" / "cache"          # conservé entre deux exécutions (cache GitHub Actions)
HISTO = ROOT / "data" / "historique"     # versionné dans le dépôt
SIC_CACHE = ROOT / "data" / "sic.json"   # versionné : change très rarement


def _lire(p: Path, defaut):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return defaut


def _ecrire(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


# ------------------------------------------------------------------ rotation
def choisir(symboles: list[str], cache: dict, maintenant: datetime, budget: int) -> list[str]:
    """Sociétés à interroger aujourd'hui, par priorité décroissante.

    Jamais analysées d'abord ; puis les plus anciennes, les conformes étant revues
    trois fois plus souvent ; les bilans de plus de 100 jours (un nouveau trimestre
    est probablement publié) deux fois plus souvent. Un échec n'est retenté qu'après 3 jours.
    """
    def score(s):
        c = cache.get(s)
        if not c:
            return (2, 0.0)
        essai = datetime.fromisoformat(c["last_try"])
        age = (maintenant - essai).total_seconds() / 86400
        if c.get("data") is None:
            return (1, age) if age >= 3 else (0, 0.0)
        poids = 3.0 if c.get("status") == regles.CONFORME else 1.0
        bilan = c["data"].get("balance_sheet_date")
        if bilan and (maintenant.date() - datetime.fromisoformat(bilan).date()).days > 100:
            poids *= 2
        return (1, age * poids)

    candidats = [s for s in symboles if score(s)[0] > 0]
    candidats.sort(key=score, reverse=True)
    return candidats[:budget]


def phase_canada(cfg, maintenant, notes) -> list[dict]:
    from univers import charger_canada
    import source_yahoo

    entrees, avert = charger_canada(ROOT / "listes", cfg.get("tickers_supplementaires", []))
    notes += avert
    cache = _lire(CACHE / "canada.json", {})

    a_analyser = []
    for e in entrees:
        e["prealable"] = regles.exclusion_prealable(e, cfg)
        if not e["prealable"]:
            a_analyser.append(e["symbol"])

    # À priorité égale, les grandes bourses passent d'abord (le tri de choisir() est stable)
    rang = {"TSX": 0, "TSXV": 1, "CSE": 2}
    bourse_de = {e["symbol"]: e["exchange"] for e in entrees}
    a_analyser.sort(key=lambda s: rang.get(bourse_de[s], 3))
    choix = choisir(a_analyser, cache, maintenant, cfg.get("ca_budget_quotidien", 400))
    print(f"Canada : {len(entrees)} sociétés, {len(a_analyser)} à analyser, {len(choix)} aujourd'hui", flush=True)
    debut, echecs = time.time(), 0
    for i, sym in enumerate(choix, 1):
        if time.time() - debut > cfg.get("ca_minutes_max", 100) * 60:
            notes.append(f"Phase canadienne arrêtée après {cfg.get('ca_minutes_max', 100)} minutes ({i - 1} sociétés).")
            break
        d = source_yahoo.fetch(sym)
        print(f"  [{i}/{len(choix)}] {sym} {'ok' if not d.get('error') else d['error'][:60]}", flush=True)
        prec = cache.get(sym, {})
        if d.get("transient"):
            echecs += 1
            cache[sym] = {**prec, "last_try": maintenant.isoformat()} if prec.get("data") else \
                {"last_try": maintenant.isoformat(), "data": None, "error": d["error"]}
            if echecs >= cfg.get("echecs_consecutifs_max", 15):
                notes.append(f"Yahoo a refusé {echecs} requêtes d’affilée : phase canadienne interrompue, "
                             "elle reprendra demain là où elle s’est arrêtée.")
                break
        else:
            echecs = 0
            r = regles.evaluer(d, cfg) if not d.get("error") else None
            cache[sym] = {"last_try": maintenant.isoformat(),
                          "data": d if not d.get("error") else None,
                          "error": d.get("error"),
                          "status": r["status"] if r else None}
        time.sleep(cfg.get("request_delay", 1.5))
        if i % 50 == 0:
            _ecrire(CACHE / "canada.json", cache)
    _ecrire(CACHE / "canada.json", cache)

    resultats = []
    for e in entrees:
        base = {"symbol": e["symbol"], "name": e["name"], "exchange": e["exchange"],
                "sector": "", "industry": e.get("list_sector", ""), "source": "Yahoo"}
        c = cache.get(e["symbol"])
        if e["prealable"]:
            r = {**base, "status": regles.EXCLU, "reasons": [e["prealable"]], "source": "Liste"}
        elif c and c.get("data"):
            r = regles.evaluer({**c["data"], "exchange": e["exchange"]}, cfg)
            r["name"] = e["name"] if e["name"] != e["symbol"] else r["name"]
        elif c and c.get("error"):
            r = regles.evaluer({**base, "error": c["error"]}, cfg)
        else:
            r = {**base, "status": regles.ATTENTE, "reasons": ["Pas encore analysée (rotation en cours)"]}
        if c:
            r["checked"] = c["last_try"][:10]
        resultats.append(r)
    return resultats


def phase_us(cfg, maintenant, notes) -> list[dict]:
    import source_sec

    if "@" not in os.environ.get("SEC_USER_AGENT", ""):
        notes.append("Sociétés américaines non analysées : le secret SEC_USER_AGENT n’est pas configuré.")
        return []
    try:
        entrees = source_sec.univers_us(cfg)
        source_sec.ajouter_sic(entrees, SIC_CACHE)
    except Exception as e:
        notes.append(f"SEC inaccessible aujourd’hui ({type(e).__name__}) : sociétés américaines non analysées.")
        return []

    resultats, a_lire = [], []
    for e in entrees:
        motif = regles.exclusion_prealable(e, cfg)
        if motif:
            resultats.append({"symbol": e["symbol"], "name": e["name"], "exchange": e["exchange"],
                              "industry": e.get("sic_desc", ""), "status": regles.EXCLU,
                              "reasons": [motif], "source": "SEC"})
        else:
            a_lire.append(e)
    print(f"États-Unis : {len(entrees)} sociétés, {len(a_lire)} bilans à lire", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            zp = source_sec.telecharger_faits(Path(tmp) / "companyfacts.zip")
        except Exception as e:
            notes.append(f"Téléchargement des données de la SEC impossible ({type(e).__name__}).")
            return resultats
        for d in source_sec.analyser(a_lire, zp):
            r = regles.evaluer(d, cfg)
            r["checked"] = maintenant.date().isoformat()
            resultats.append(r)
    return resultats


# ------------------------------------------------------------------ principal
def changements(resultats, precedents):
    out = []
    for r in resultats:
        avant = precedents.get(r["symbol"])
        if avant and r["status"] != regles.ATTENTE and avant != r["status"]:
            out.append({"symbol": r["symbol"], "name": r["name"], "from": avant, "to": r["status"],
                        "reasons": r["reasons"]})
    return out


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    maintenant = datetime.now(timezone.utc)
    etat = _lire(CACHE / "etat.json", {})
    notes: list[str] = []

    resultats = phase_us(cfg, maintenant, notes) + phase_canada(cfg, maintenant, notes)
    chg = changements(resultats, etat.get("statuts", {}))

    ecrire_rapport(resultats, chg, maintenant, etat.get("date"), notes, cfg, ROOT / "docs")

    HISTO.mkdir(parents=True, exist_ok=True)
    with open(HISTO / f"{maintenant:%Y-%m-%d}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbole", "nom", "bourse", "date_du_bilan"])
        for r in sorted(resultats, key=lambda r: r["symbol"]):
            if r["status"] == regles.CONFORME:
                w.writerow([r["symbol"], r["name"], r.get("exchange", ""), r.get("balance_sheet_date", "")])

    statuts = {r["symbol"]: r["status"] for r in resultats if r["status"] != regles.ATTENTE}
    _ecrire(CACHE / "etat.json", {"date": maintenant.isoformat(), "statuts": {**etat.get("statuts", {}), **statuts}})

    n = {s: sum(r["status"] == s for r in resultats) for s in
         (regles.CONFORME, regles.EXAMINER, regles.EXCLU, regles.ATTENTE)}
    print(f"Terminé : {n}. {len(chg)} changement(s).")
    for x in notes:
        print("Note :", x, file=sys.stderr)


if __name__ == "__main__":
    main()
