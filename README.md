# K-B

**Une bibliothèque de veille tech qui se range toute seule.**

K-B collecte chaque jour l'actualité tech de plusieurs sources, la range en items datés dans une
base SQLite, puis la sert par deux API REST : une API de données, en lecture seule, et une API IA
qui rédige par un modèle de langage les comptes rendus de veille, un par sujet.

## Fonctionnalités

- **Collecte** (`kb collect`) : une panne isolée par source, le brut sauvegardé avant tout
  traitement, une collecte manquée rattrapée à la suivante. Sources :
  - API GitHub : releases des dépôts suivis, alertes de sécurité, dépôts les plus étoilés et les
    plus forkés ;
  - GH Archive, par BigQuery : les dépôts qui montent ;
  - API arXiv : papiers sur la sécurité de l'IA ;
  - catalogue CISA des vulnérabilités activement exploitées (CSV) ;
  - newsletters tldr.tech (AI, InfoSec, Dev) ;
  - Zotero : papiers lus et surlignages, lus sur une copie de la base SQLite ;
  - clips Markdown d'Obsidian Web Clipper.
- **Import** (`kb import`) : le brut normalisé en items communs, les doublons écartés, chaque rejet
  journalisé.
- **API de données** (`kb serve-data`) : `GET /items`, `GET /items/{item_id}`, `GET /trends`.
- **API IA** (`kb serve-ai`) : `POST /digests`, les comptes rendus de veille par sujet.
- **Fiches** (`kb write-tops`, `kb digest`) : les classements et les comptes rendus écrits en
  Markdown dans un coffre Obsidian.

## Prérequis

- Python 3.13 ou plus récent
- [uv](https://docs.astral.sh/uv/)

## Installation

```sh
git clone <url-du-dépôt>
cd <dossier-du-dépôt>
uv sync
```

## Configuration

Copier `.env.example` en `.env` à la racine du dépôt, puis le compléter. Le `Makefile` lit ce
fichier. Les principales variables :

| Variable | Rôle |
|---|---|
| `KB_DATA_DIR` | obligatoire : dossier des données, hors du dépôt ; il doit exister |
| `KB_GITHUB_TOKEN` | jeton GitHub en lecture seule ; sans lui, 60 requêtes par heure au lieu de 5 000 |
| `GOOGLE_APPLICATION_CREDENTIALS` | clé du compte de service BigQuery ; sans elle, GH Archive est sauté |
| `KB_ZOTERO_DIR` | dossier de données de Zotero ; sans lui, Zotero est sauté |
| `KB_VAULT_DIR` | coffre Obsidian : clips à collecter, fiches écrites |
| `KB_COLLECT_ONLY` | les sources à collecter, séparées par des virgules ; sans elle, toutes |
| `KB_DATA_API_KEY_<CLIENT>` | clé d'un client de l'API de données, une variable par client |
| `KB_AI_API_KEY_<CLIENT>` | clé d'un client de l'API IA, une variable par client |
| `KB_LLM_*` | fournisseur, modèle, adresse et clé du modèle de langage |

Une clé d'API compte au moins 32 caractères, pris dans `A-Z`, `a-z`, `0-9`, `_` et `-`. Les autres
réglages (hôtes, ports, fenêtre des comptes rendus, traces) sont décrits dans `.env.example`.

## Utilisation

Chaque commande a sa cible dans le `Makefile`, qui charge le `.env` :

```sh
make create-db    # crée la base, une fois
make collect      # collecte les sources dans <KB_DATA_DIR>/raw/
make import       # importe le brut dans la base
make write-tops   # écrit les fiches des classements dans le coffre
make serve-data   # API de données sur http://127.0.0.1:8000
make serve-ai     # API IA sur http://127.0.0.1:8001
make digest       # demande les comptes rendus à l'API IA, les écrit dans le coffre
make openapi      # régénère les contrats OpenAPI de docs/api/
```

Sans `make` : `uv run --env-file .env kb <commande>`. `uv run kb --help` liste les commandes et
leurs codes de sortie.

La documentation interactive de chaque API (Swagger) est servie sur `/docs`, sans clé ; `/health`
sert de sonde. Les contrats OpenAPI versionnés sont dans `docs/api/` : `openapi-donnees.json` et
`openapi-ia.json`.

## Tests et qualité

```sh
uv run pytest                 # tests
uv run ruff check             # lint
uv run ruff format --check    # format
uv run mypy --strict          # types
```

Par défaut, `pytest` écarte les tests de bout en bout de `tests/e2e/`, qui lancent kb sur les
vraies sources ; `uv run pytest -m e2e` les lance. Les autres tests lisent les extraits de
`tests/fixtures/`. La CI (`.github/workflows/ci.yml`) lance ces quatre contrôles à chaque push sur
`main`, plus un audit des dépendances de `uv.lock` par `pip-audit`.

## Structure

```
src/k_b/
├── cli.py         point d'entrée de la commande kb
├── commands/      une commande kb par module, codes de sortie, journal
├── etl/           collecte (extract), lecture et normalisation (transform), import (load)
│   └── sources/   un module par source
├── items.py       l'item commun en lequel chaque source est lue
├── database/      création, lecture et écriture de la base SQLite
├── data_api/      API de données (FastAPI), en lecture seule
├── ai_api/        API IA (FastAPI) : comptes rendus de veille par un modèle de langage
├── post/          fiches Markdown écrites dans le coffre Obsidian
└── core/          réglages, clés d'API, erreurs et journaux communs aux deux API
tests/             tests pytest par paquet, tests de bout en bout (e2e/), extraits (fixtures/)
docs/api/          contrats OpenAPI des deux API
```

## Licence

© 2026 Sabine Laurent — sous licence
[Creative Commons Attribution - Pas d'Utilisation Commerciale 4.0 International (CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/deed.fr).
Texte complet : [LICENSE](LICENSE).

## Crédits

Les papiers viennent de l'API arXiv, qui demande cette mention à chacun de ses utilisateurs
([arXiv API Access](https://info.arxiv.org/help/api/index.html)) :

> Thank you to arXiv for use of its open access interoperability.
