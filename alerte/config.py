"""
Chargement de la configuration (config.yaml) et des secrets (.env).
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_DATA = RACINE / "data"


def charger_env(chemin: Path | None = None) -> None:
    """Charge un fichier .env dans os.environ (sans écraser l'existant)."""
    chemin = chemin or (RACINE / ".env")
    if not chemin.exists():
        return
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        cle, valeur = cle.strip(), valeur.strip().strip('"').strip("'")
        os.environ.setdefault(cle, valeur)


class Config:
    """Accès typé aux critères de config.yaml."""

    def __init__(self, data: dict):
        self._data = data
        self.recherche = data.get("recherche") or {}
        self.alertes = data.get("alertes") or {}
        self.execution = data.get("execution") or {}

    @classmethod
    def charger(cls, chemin: Path | None = None) -> "Config":
        chemin = chemin or (RACINE / "config.yaml")
        if not chemin.exists():
            raise FileNotFoundError(f"Configuration introuvable : {chemin}")
        return cls(yaml.safe_load(chemin.read_text(encoding="utf-8")) or {})

    # -- recherche ---------------------------------------------------------
    @property
    def marque(self) -> str:
        return self.recherche.get("marque") or "RENAULT"

    @property
    def modele(self) -> str:
        return self.recherche.get("modele") or "MEGANE E-TECH ELECTRIQUE"

    @property
    def modele_court(self) -> str:
        """Nom de modele court, pour les sites qui indexent "megane" et non
        "MEGANE E-TECH ELECTRIQUE"."""
        return self.recherche.get("modele_court") or self.modele.split()[0]

    @property
    def sources_actives(self) -> list[str]:
        """Sources a interroger, dans l'ordre de priorite pour le
        dedoublonnage entre sites."""
        valeur = self.recherche.get("sources")
        if not valeur:
            return ["renew", "autoscout24"]
        return [str(v).strip().lower() for v in valeur]

    @property
    def plateforme(self) -> str:
        """Plateforme de publication exigee (NATIONAL pour fr.renew.auto)."""
        return self.recherche.get("plateforme") or "NATIONAL"

    @property
    def batterie_kwh(self):
        return self.recherche.get("batterie_kwh")

    @property
    def departements(self) -> set[str] | None:
        deps = self.recherche.get("departements")
        if not deps:
            return None
        return {str(d).zfill(2) for d in deps}

    # -- alertes -----------------------------------------------------------
    @property
    def max_notifications(self) -> int:
        return int(self.alertes.get("max_notifications") or 20)

    @property
    def baisse_min_eur(self) -> int:
        return int(self.alertes.get("baisse_min_eur") or 0)

    def critere(self, nom, defaut=None):
        return self.recherche.get(nom, defaut)

    def __repr__(self) -> str:
        return f"<Config {self.marque} {self.modele} EV{self.batterie_kwh}>"


def resume_criteres(cfg: Config) -> str:
    """Une ligne lisible décrivant les critères actifs.

    Formulée en mots plutôt qu'en symboles mathématiques : c'est plus lisible
    dans Discord, et « ≥ » n'existe pas en cp1252 donc casserait la console
    Windows.
    """
    modele = " ".join(m.capitalize() for m in cfg.modele.split())
    bouts = ["%s %s" % (cfg.marque.capitalize(), modele)]
    if cfg.batterie_kwh:
        bouts.append("EV%s" % cfg.batterie_kwh)
    if cfg.critere("annee_min"):
        bouts.append("à partir de %s" % cfg.critere("annee_min"))
    if cfg.critere("annee_max"):
        bouts.append("jusqu'à %s" % cfg.critere("annee_max"))
    if cfg.critere("prix_max"):
        bouts.append("%s € max" % _milliers(cfg.critere("prix_max")))
    if cfg.critere("prix_min"):
        bouts.append("%s € mini" % _milliers(cfg.critere("prix_min")))
    if cfg.critere("km_max"):
        bouts.append("%s km max" % _milliers(cfg.critere("km_max")))
    if cfg.critere("soh_min"):
        bouts.append("batterie %s%% mini" % cfg.critere("soh_min"))
    if cfg.departements:
        deps = sorted(cfg.departements)
        bouts.append("dépts %s" % ", ".join(deps) if len(deps) <= 6
                     else "%d départements" % len(deps))
    return "  ·  ".join(bouts)


def _milliers(v) -> str:
    return "{:,.0f}".format(float(v)).replace(",", " ")
