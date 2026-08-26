"""
Sources d'annonces.

Une source = un site. Elle expose `chercher()` et rend des annonces au format
normalisé commun. L'ordre de `CATALOGUE` fait aussi office de priorité lors du
dédoublonnage : la même voiture vue sur deux sites est conservée depuis la
première source de la liste, celle dont les données sont les plus riches.
"""
from .base import ErreurSource, SourceBase
from .autoscout24 import SourceAutoScout24
from .renew import SourceRenew

CATALOGUE = {
    "renew": SourceRenew,
    "autoscout24": SourceAutoScout24,
}


def sources_pour(cfg) -> dict:
    """Sources demandées dans la configuration, dans l'ordre de priorité."""
    return {
        nom: CATALOGUE[nom]
        for nom in cfg.sources_actives
        if nom in CATALOGUE
    }


__all__ = ["CATALOGUE", "sources_pour", "SourceBase", "ErreurSource",
           "SourceRenew", "SourceAutoScout24"]
