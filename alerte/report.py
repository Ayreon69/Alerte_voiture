"""
Export CSV de l'inventaire courant, lisible directement dans Excel
(separateur ';' et BOM UTF-8, comme attendu par Excel en francais).
"""
from __future__ import annotations

import csv
from pathlib import Path

# Ordre pense pour une lecture dans Excel : les criteres de decision d'abord.
COLONNES = [
    "prix", "km", "batterie_soh", "concession", "ville", "code_postal",
    "departement", "date_1re_immat", "couleur", "titre", "finition",
    "charge_ac_kw", "pompe_a_chaleur", "batterie_kwh",
    "batterie_capacite_restante_kwh", "puissance_ch", "garantie", "prix_neuf",
    "telephone", "reserve", "accidente", "immatriculation", "vin",
    "date_publication", "url", "source", "id",
]


def par_prix(a: dict):
    """Cle de tri : prix croissant, prix inconnus en dernier."""
    return (a.get("prix") is None, a.get("prix") or 0)


def exporter_csv(annonces: list[dict], chemin: Path) -> Path:
    """Ecrit l'inventaire trie par prix croissant."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tri = sorted(annonces, key=par_prix)
    with open(chemin, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLONNES, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(tri)
    return chemin
