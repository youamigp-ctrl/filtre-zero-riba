"""Rapport web quotidien : docs/index.html (autonome) et docs/resultats.csv."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import regles

LABELS = {"conforme": "Conforme", "examiner": "À examiner", "exclu": "Exclue", "attente": "En attente"}
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]
BOURSES = ["TSX", "TSXV", "CSE", "NYSE", "Nasdaq"]


def _date(dt: datetime) -> str:
    dt = dt.astimezone(ZoneInfo("America/Toronto"))
    return f"{dt.day} {MOIS[dt.month - 1]} {dt.year} à {dt:%H} h {dt:%M}"


def _pct(a, b):
    return f"{100 * a / b:.0f}\u00a0%" if b else "–"


def couverture(resultats):
    lignes = []
    for b in BOURSES + sorted({r.get("exchange", "") for r in resultats} - set(BOURSES) - {""}):
        rs = [r for r in resultats if r.get("exchange") == b]
        if not rs:
            continue
        prealable = sum(r.get("source") == "Liste" or (r.get("source") == "SEC" and r["status"] == "exclu"
                        and r["reasons"][0].startswith("Activité exclue")) for r in rs)
        attente = sum(r["status"] == regles.ATTENTE for r in rs)
        vides = sum(regles.sans_donnees(r) for r in rs)
        analysables = len(rs) - prealable
        traitees = analysables - attente
        lignes.append({"bourse": b, "total": len(rs), "prealable": prealable, "traitees": traitees,
                       "analysables": analysables, "vides": vides,
                       "conformes": sum(r["status"] == regles.CONFORME for r in rs)})
    return lignes


def _table_couverture(lignes):
    corps = "".join(
        f"<tr><th scope=\"row\">{escape(l['bourse'])}</th><td class=\"num\">{l['total']}</td>"
        f"<td class=\"num\">{l['prealable']}</td>"
        f"<td class=\"num\">{l['traitees']} / {l['analysables']} <span class=\"muted\">({_pct(l['traitees'], l['analysables'])})</span></td>"
        f"<td class=\"num {'warn' if l['traitees'] and l['vides'] / max(l['traitees'], 1) > .25 else ''}\">"
        f"{l['vides']} <span class=\"muted\">({_pct(l['vides'], l['traitees'])})</span></td>"
        f"<td class=\"num\">{l['conformes']}</td></tr>" for l in lignes)
    return f"""<div class="table-wrap"><table class="cov">
      <thead><tr><th>Bourse</th><th class="num">Sociétés</th><th class="num">Exclues d’office</th>
      <th class="num">Analysées</th><th class="num">Sans données</th><th class="num">Conformes</th></tr></thead>
      <tbody>{corps}</tbody></table></div>
    <p class="muted small">« Sans données » : la source n’a pas fourni de quoi juger la dette. Au-delà de 25 %
    (en orange), un fournisseur payant pourrait valoir la peine pour cette bourse.</p>"""


def _changements(chg, date_prec):
    if date_prec is None:
        return '<p class="muted">Première analyse : les changements apparaîtront à partir de la prochaine.</p>'
    prec = _date(datetime.fromisoformat(date_prec))
    if not chg:
        return f'<p class="muted">Aucun changement de statut depuis l’analyse du {prec}.</p>'
    chg = sorted(chg, key=lambda c: c["from"] != "conforme")
    items = "".join(
        f'<li class="{"lost" if c["from"] == "conforme" else "gained" if c["to"] == "conforme" else ""}">'
        f'<strong>{escape(c["symbol"])}</strong> {escape(c["name"])} : {LABELS[c["from"]].lower()} → '
        f'{LABELS[c["to"]].lower()}.{" Selon votre règle, cette position est à revendre." if c["from"] == "conforme" else ""}'
        f'<span class="why">{escape("; ".join(c["reasons"]))}</span></li>' for c in chg[:150])
    plus = f'<p class="muted">… et {len(chg) - 150} autres (voir resultats.csv).</p>' if len(chg) > 150 else ""
    return f'<p class="muted">Depuis l’analyse du {prec} :</p><ul class="changes">{items}</ul>{plus}'


def ecrire_rapport(resultats, chg, maintenant, date_prec, notes, cfg, dossier: Path):
    dossier.mkdir(parents=True, exist_ok=True)
    ordre = {"conforme": 0, "examiner": 1, "exclu": 2, "attente": 3}
    resultats = sorted(resultats, key=lambda r: (ordre[r["status"]], r["symbol"]))

    with open(dossier / "resultats.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["statut", "symbole", "nom", "bourse", "activite", "dette_financiere", "baux",
                    "interets_verses", "interets_percus", "devise", "date_du_bilan", "source", "verifie_le", "profil", "motifs"])
        for r in resultats:
            w.writerow([LABELS[r["status"]], r["symbol"], r["name"], r.get("exchange", ""),
                        r.get("industry") or r.get("sector") or "", r.get("financial_debt"), r.get("leases"),
                        r.get("interest_expense"), r.get("interest_income"), r.get("currency", ""),
                        r.get("balance_sheet_date", ""), r.get("source", ""), r.get("checked", ""),
                        r.get("profil", ""), " ; ".join(r["reasons"])])

    data = [[r["status"], r["symbol"], r["name"], r.get("exchange", ""), r.get("industry") or r.get("sector") or "",
             r.get("financial_debt"), r.get("leases"), r.get("interest_expense"), r.get("interest_income"),
             r.get("currency", ""), r.get("balance_sheet_date") or "", r.get("source", ""), " ; ".join(r["reasons"]),
             r.get("profil", "Inconnu")]
            for r in resultats if r["status"] != regles.ATTENTE]
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    n = {s: sum(r["status"] == s for r in resultats) for s in ordre}
    analysees = len(resultats) - n["attente"]
    titre = (f"{n['conforme']} société{'s' if n['conforme'] > 1 else ''} sur {analysees} analysée"
             f"{'s' if analysees > 1 else ''} passe{'nt' if n['conforme'] > 1 else ''} le filtre.")
    notes_html = "".join(f"<li>{escape(x)}</li>" for x in notes)
    notes_html = f'<ul class="notes">{notes_html}</ul>' if notes else ""
    pol = lambda k: "les excluent" if cfg.get(k) == "exclure" else "les signalent pour examen"
    options = "".join(f'<option value="{b}">{b}</option>' for b in BOURSES)

    html = f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Filtre zéro riba : {maintenant:%Y-%m-%d}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🌿</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Literata:opsz,wght@7..72,400;7..72,600&family=Public+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{ --paper:#F2F4F1; --surface:#FFF; --ink:#17201C; --muted:#5B6862; --rule:#CFD8D3;
  --ok:#1E5B4A; --ok-bg:#DDEDE6; --warn:#8A5D0B; --warn-bg:#F6EBD3; --no:#93302F; --no-bg:#F5DFDC; --focus:#2B6CB0;
  color-scheme:light; box-sizing:border-box;
  padding-top:env(safe-area-inset-top,0px); padding-bottom:env(safe-area-inset-bottom,0px); }}
@media (prefers-color-scheme: dark) {{ :root {{ --paper:#121815; --surface:#1A221E; --ink:#E4EAE6; --muted:#9AA8A1;
  --rule:#2E3934; --ok:#7CC9AE; --ok-bg:#1C3A30; --warn:#E3B865; --warn-bg:#3A2F17; --no:#EE9A93; --no-bg:#3D1F1D;
  --focus:#8AB8F0; color-scheme:dark; }} }}
*,*::before,*::after {{ box-sizing:inherit; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font:400 15px/1.55 "Public Sans",system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:48px 24px 64px; }}
h1 {{ font:600 clamp(28px,4.4vw,46px)/1.12 Literata,Georgia,serif; margin:0 0 12px; max-width:24ch; letter-spacing:-.01em; }}
h2 {{ font:600 20px/1.3 Literata,Georgia,serif; margin:40px 0 10px; }}
.muted {{ color:var(--muted); }} .small {{ font-size:13.5px; }} p {{ margin:0 0 8px; }}
.tally {{ display:flex; gap:28px; flex-wrap:wrap; margin:24px 0 0; padding:0; list-style:none; font-variant-numeric:tabular-nums; }}
.tally b {{ font:600 22px/1 Literata,Georgia,serif; margin-right:6px; }}
.tally .conforme b {{ color:var(--ok); }} .tally .examiner b {{ color:var(--warn); }} .tally .exclu b {{ color:var(--no); }}
.notes {{ margin:20px 0 0; padding:10px 14px 10px 32px; background:var(--warn-bg); color:var(--ink); border-radius:8px; }}
.changes {{ list-style:none; padding:0; margin:0; border-left:3px solid var(--rule); }}
.changes li {{ padding:8px 14px; }}
.changes li.lost {{ border-left:3px solid var(--no); margin-left:-3px; background:var(--no-bg); }}
.changes li.gained {{ border-left:3px solid var(--ok); margin-left:-3px; background:var(--ok-bg); }}
.why {{ display:block; color:var(--muted); font-size:13px; }}
.controls {{ display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin:0 0 14px; }}
.seg {{ display:inline-flex; border:1px solid var(--rule); border-radius:8px; overflow:hidden; background:var(--surface); }}
.seg button {{ font:inherit; font-size:14px; color:var(--ink); background:none; border:0; padding:7px 14px; cursor:pointer; }}
.seg button+button {{ border-left:1px solid var(--rule); }}
.seg button[aria-pressed="true"] {{ background:var(--ink); color:var(--paper); }}
select, input[type=search] {{ font:inherit; font-size:14px; color:var(--ink); background:var(--surface); border:1px solid var(--rule); border-radius:8px; padding:7px 12px; }}
input[type=search] {{ min-width:220px; flex:1; max-width:340px; }}
:focus-visible {{ outline:2px solid var(--focus); outline-offset:2px; }}
.table-wrap {{ overflow-x:auto; background:var(--surface); border:1px solid var(--rule); border-radius:10px; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
#liste {{ min-width:1080px; }}
thead th {{ text-align:left; font-weight:500; color:var(--muted); font-size:13px; padding:10px 12px; border-bottom:1px solid var(--rule); white-space:nowrap; }}
td, tbody th {{ padding:10px 12px; border-bottom:1px solid var(--rule); vertical-align:top; text-align:left; font-weight:400; }}
tbody tr:last-child>* {{ border-bottom:0; }}
tbody th a {{ color:var(--ink); font-weight:600; text-decoration-color:var(--rule); text-underline-offset:3px; }}
.name {{ display:block; color:var(--muted); font-size:13px; min-width:14ch; max-width:26ch; }}
.num, thead th.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.num.warn {{ color:var(--warn); font-weight:600; }}
.sector {{ color:var(--muted); max-width:18ch; }}
.prof {{ white-space:nowrap; color:var(--muted); font-size:13px; }} .prof.ok {{ color:var(--ok); font-weight:500; }} .date {{ white-space:nowrap; color:var(--muted); font-variant-numeric:tabular-nums; }}
.reasons {{ font-size:13px; min-width:30ch; max-width:42ch; }}
.badge {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:12.5px; font-weight:500; white-space:nowrap; }}
.badge.conforme {{ background:var(--ok-bg); color:var(--ok); }} .badge.examiner {{ background:var(--warn-bg); color:var(--warn); }}
.badge.exclu {{ background:var(--no-bg); color:var(--no); }}
.more {{ font:inherit; font-size:14px; margin:14px 0 0; padding:8px 16px; border:1px solid var(--rule); border-radius:8px; background:var(--surface); color:var(--ink); cursor:pointer; }}
.count {{ font-size:13.5px; color:var(--muted); margin:10px 0 0; }}
footer {{ margin-top:44px; color:var(--muted); font-size:13.5px; max-width:78ch; }}
footer a {{ color:inherit; }}
</style></head>
<body><main>
<header>
  <h1>{titre}</h1>
  <p class="muted">Analyse du {_date(maintenant)}. Sociétés américaines : données officielles de la SEC.
  Sociétés canadiennes : Yahoo Finance, en rotation.</p>
  <ul class="tally">
    <li class="conforme"><b>{n['conforme']}</b>conformes</li>
    <li class="examiner"><b>{n['examiner']}</b>à examiner</li>
    <li class="exclu"><b>{n['exclu']}</b>exclues</li>
    <li><b>{n['attente']}</b>en attente de rotation</li>
  </ul>
  {notes_html}
</header>

<section aria-labelledby="h-chg"><h2 id="h-chg">Changements</h2>{_changements(chg, date_prec)}</section>

<section aria-labelledby="h-cov"><h2 id="h-cov">Couverture des données</h2>{_table_couverture(couverture(resultats))}</section>

<section aria-labelledby="h-list"><h2 id="h-list">Résultats</h2>
  <div class="controls">
    <div class="seg" role="group" aria-label="Filtrer par statut">
      <button type="button" data-f="conforme" aria-pressed="true">Conformes</button>
      <button type="button" data-f="examiner" aria-pressed="false">À examiner</button>
      <button type="button" data-f="exclu" aria-pressed="false">Exclues</button>
      <button type="button" data-f="all" aria-pressed="false">Toutes</button>
    </div>
    <select id="bourse" aria-label="Bourse"><option value="">Toutes les bourses</option>{options}</select>
    <select id="profil" aria-label="Profil financier"><option value="">Tous les profils</option>
      <option>Rentable</option><option>Déficitaire</option><option>Sans revenus</option><option>Inconnu</option></select>
    <input type="search" id="q" placeholder="Chercher un symbole, un nom, une activité" aria-label="Chercher">
  </div>
  <div class="table-wrap"><table id="liste">
    <thead><tr><th>Statut</th><th>Société</th><th>Bourse</th><th>Profil</th><th>Activité</th>
      <th class="num">Dette financière</th><th class="num">Baux</th><th class="num">Intérêts versés</th>
      <th class="num">Intérêts perçus</th><th>Bilan au</th><th>Motifs</th></tr></thead>
    <tbody id="corps"></tbody></table></div>
  <p class="count" id="compte"></p>
  <button type="button" class="more" id="plus" hidden>Afficher 200 de plus</button>
</section>

<footer>
  <p>Règles : exclusion des secteurs financiers et des activités illicites, aucune dette financière portant intérêt,
  aucun intérêt versé. Pour les intérêts perçus, les règles actuelles {pol('interest_income_policy')} ; pour les baux,
  elles {pol('lease_policy')}. Montants des 12 derniers mois ou du dernier exercice, en devise de publication.</p>
  <p>Profil : « Rentable » signifie bénéfice net et flux de trésorerie d’exploitation positifs au dernier exercice.
  Les conformes américaines sont recoupées avec Yahoo Finance : si l’une des deux sources voit une dette ou des intérêts
  versés, la société est exclue.</p>
  <p>Les données automatiques peuvent être incomplètes ou erronées : une dette déclarée sous un libellé inhabituel peut
  échapper au filtre. Avant tout achat, vérifiez le bilan et ses notes dans le rapport officiel (SEDAR+ ou EDGAR).
  Ce rapport est un outil de tri : ni conseil financier, ni avis religieux.
  <a href="resultats.csv">Télécharger tous les résultats (CSV)</a>.</p>
</footer>
</main>
<script type="application/json" id="data">{blob}</script>
<script>
(function () {{
  var D = JSON.parse(document.getElementById("data").textContent);
  var L = {{conforme:"Conforme", examiner:"À examiner", exclu:"Exclue"}};
  var corps = document.getElementById("corps"), plus = document.getElementById("plus"), compte = document.getElementById("compte");
  var profil = document.getElementById("profil");
  var q = document.getElementById("q"), bourse = document.getElementById("bourse"), boutons = document.querySelectorAll(".seg button");
  var filtre = "conforme", vue = [], montre = 0;
  function argent(v, cur) {{
    if (v === null || v === undefined || v === "") return "n.d.";
    var a = Math.abs(v); if (a === 0) return "0";
    var s = a >= 1e9 ? (v/1e9).toLocaleString("fr-CA", {{maximumFractionDigits:1}}) + " G"
          : a >= 1e6 ? (v/1e6).toLocaleString("fr-CA", {{maximumFractionDigits:0}}) + " M"
          : (v/1e3).toLocaleString("fr-CA", {{maximumFractionDigits:0}}) + " k";
    return s + "\\u00a0" + (cur || "");
  }}
  function cell(tr, txt, cls, tag) {{ var c = document.createElement(tag || "td"); if (cls) c.className = cls; c.textContent = txt; tr.appendChild(c); return c; }}
  function ligne(r) {{
    var tr = document.createElement("tr");
    var b = document.createElement("span"); b.className = "badge " + r[0]; b.textContent = L[r[0]];
    var td = document.createElement("td"); td.appendChild(b); tr.appendChild(td);
    var th = document.createElement("th"); th.scope = "row";
    var a = document.createElement("a"); a.textContent = r[1]; a.target = "_blank"; a.rel = "noopener";
    a.href = r[11] === "SEC" ? "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=" + encodeURIComponent(r[1])
                             : "https://finance.yahoo.com/quote/" + encodeURIComponent(r[1]) + "/balance-sheet";
    var n = document.createElement("span"); n.className = "name"; n.textContent = r[2];
    th.appendChild(a); th.appendChild(n); tr.appendChild(th);
    cell(tr, r[3], "date"); cell(tr, r[13], "prof " + (r[13] === "Rentable" ? "ok" : "")); cell(tr, r[4], "sector");
    [5,6,7,8].forEach(function (i) {{ cell(tr, argent(r[i], r[9]), "num"); }});
    cell(tr, r[10] || "n.d.", "date"); cell(tr, r[12] || "Aucun motif d’exclusion détecté", "reasons");
    return tr;
  }}
  function afficher(n) {{
    var frag = document.createDocumentFragment();
    vue.slice(montre, montre + n).forEach(function (r) {{ frag.appendChild(ligne(r)); }});
    corps.appendChild(frag); montre = Math.min(vue.length, montre + n);
    plus.hidden = montre >= vue.length;
    compte.textContent = vue.length ? montre + " sur " + vue.length + " affichées" : "Aucune société ne correspond. Changez de filtre ou de mot-clé.";
  }}
  function appliquer() {{
    var t = q.value.trim().toLowerCase(), b = bourse.value;
    vue = D.filter(function (r) {{
      return (filtre === "all" || r[0] === filtre) && (!b || r[3] === b) && (!profil.value || r[13] === profil.value) &&
             (!t || (r[1] + " " + r[2] + " " + r[4]).toLowerCase().indexOf(t) !== -1);
    }});
    corps.textContent = ""; montre = 0; afficher(200);
  }}
  boutons.forEach(function (x) {{ x.addEventListener("click", function () {{
    filtre = x.dataset.f; boutons.forEach(function (y) {{ y.setAttribute("aria-pressed", String(y === x)); }}); appliquer(); }}); }});
  q.addEventListener("input", appliquer); bourse.addEventListener("change", appliquer); profil.addEventListener("change", appliquer);
  plus.addEventListener("click", function () {{ afficher(200); }});
  appliquer();
}})();
</script>
</body></html>"""
    (dossier / "index.html").write_text(html, encoding="utf-8")
