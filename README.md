# Filtre zéro riba — version 2

Chaque jour de semaine, ce programme trie les sociétés cotées accessibles dans un CELI (TSX, Bourse de croissance, CSE, NYSE, Nasdaq) selon trois règles : pas de secteur financier ni d’activité illicite, pas de dette portant intérêt, pas d’intérêt versé. Il publie une page web avec les sociétés conformes, les changements depuis la veille et la qualité des données par bourse.

## Comment ça marche

Les **sociétés américaines** sont analysées toutes, chaque jour, avec les données officielles de la SEC (un seul gros fichier téléchargé, gratuit). Les **sociétés canadiennes** sont analysées avec Yahoo Finance **en rotation** : environ 400 par jour, en commençant par celles jamais vues, puis en revenant trois fois plus souvent sur les conformes, pour repérer vite une nouvelle dette. Les bilans ne changeant qu’une fois par trimestre, un passage complet toutes les une à deux semaines suffit.

Les secteurs exclus d’office (banques, assurances, fonds, alcool, tabac…) sont écartés avant tout téléchargement, ce qui économise des requêtes.

## Plan de déploiement

### Étape 1 — Mise en place 

1. Téléchargez les listes officielles et déposez-les dans `listes/` (voir `listes/LISEZMOI.txt`) :
   - Groupe TMX : répertoire des sociétés inscrites, sur tsx.com (section « Listed Company Directory ») ;
   - CSE : liste des sociétés exportée depuis thecse.com.


La première exécution est la plus longue (une à deux heures) : elle récupère une fois pour toutes le secteur de chaque société américaine. L’adresse de votre rapport s’affiche à la fin, sous la forme `https://VOTRE-NOM.github.io/filtre-zero-riba/`.

### Étape 2 — Utilisation courante

- **Chaque jour** : consultez la section *Changements*. Une société qui passe de conforme à exclue apparaît en rouge.
- **Avant tout achat** : vérifiez le bilan et ses notes dans le rapport officiel (SEDAR+ ou EDGAR). Une dette déclarée sous un libellé inhabituel peut échapper à un filtre automatique.
- **Chaque mois** : remplacez les deux fichiers de `listes/` par leur nouvelle version.
- **Chaque trimestre** : relisez les règles dans `config.yaml` et l’historique des conformes dans `data/historique/`.

Pour recevoir une notification à chaque rapport, cliquez sur « Watch » en haut du dépôt.

## Réglages (config.yaml)

| Réglage | Effet |
|---|---|
| `interest_income_policy`, `lease_policy` | `examiner` (par défaut) ou `exclure` pour la lecture la plus stricte |
| `ca_budget_quotidien` | sociétés canadiennes interrogées par jour |
| `secteurs_listes_exclus`, `sic_exclus`, `excluded_industries` | secteurs écartés d’office |
| `exclusions_manuelles` | symboles à écarter (jeux de hasard, etc.) |
| `tickers_supplementaires` | symboles absents des listes |

## Lancer sur votre ordinateur

```bash
pip install -r requirements.txt
SEC_USER_AGENT="FiltreZeroRiba votre@courriel.com" python analyse.py
```

Le rapport est créé dans `docs/index.html`.

## Limites

Yahoo Finance est une source gratuite non officielle : elle peut bloquer des requêtes, changer de format ou contenir des erreurs. Les données de la SEC sont officielles mais dépendent de la façon dont chaque société étiquette ses chiffres. Une société listée à la fois au Canada et aux États-Unis peut apparaître deux fois. Ce rapport est un outil de tri : il ne constitue ni un conseil financier, ni un avis religieux.
