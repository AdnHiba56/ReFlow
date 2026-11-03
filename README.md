# ReFlow

ReFlow est le backend FastAPI de l'agent IA du projet de hackathon Meta : il expose
une API HTTP qui dialogue avec l'API Meta Model.

## Prérequis

- Python 3 installé et disponible via la commande `python`.
- Une clé API Meta Model (à garder strictement personnelle).

## Installation

Depuis la racine du projet (`C:\Users\user\ReFlow`), avec PowerShell :

```powershell
# 1. Créer l'environnement virtuel local (s'il n'existe pas déjà)
python -m venv .venv

# 2. Activer l'environnement
.\.venv\Scripts\Activate.ps1

# 3. Installer les dépendances
pip install -r requirements.txt
```

> Sous macOS / Linux, l'activation se fait avec `source .venv/bin/activate`.

## Configuration locale

1. Copier le fichier d'exemple vers `.env` :
   ```powershell
   Copy-Item .env.example .env
   ```
2. Ouvrir `.env` et remplacer `COLLER_MA_CLE_ICI` par votre clé Meta.
3. Redémarrer le serveur après toute modification du `.env`.

**Important :** le fichier `.env` contient un secret. Il doit rester local sur
votre machine : ne le partagez jamais et ne l'ajoutez jamais à Git (il est
déjà exclu via `.gitignore`).

## Démarrage du serveur

Depuis la racine du projet, environnement virtuel activé :

```powershell
uvicorn main:app --reload
```

Le serveur écoute alors sur `http://127.0.0.1:8000`, avec la documentation
interactive sur `http://127.0.0.1:8000/docs`.

## Structure du projet

- `main.py` — application FastAPI et client Meta Model API.
- `requirements.txt` — dépendances Python.
- `.env.example` — modèle de configuration locale (sans secret).
- `.env` — configuration locale réelle (à créer, jamais partagée).
