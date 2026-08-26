"""
Mise en forme francaise des donnees brutes de l'API.

L'API renvoie du texte en majuscules ("BOURG EN BRESSE"), des nombres nus
(18899) et des dates ISO (2023-06-08). Tout est remis en francais lisible ici,
pour que Discord comme la console affichent des valeurs presentables.

Note d'encodage : les accents et le symbole EUR existent en cp1252, donc la
console Windows les affiche sans probleme. Les emojis et les caracteres de
dessin, non : ils restent cantonnes aux messages Discord.
"""
from __future__ import annotations

import re
from datetime import date, datetime

ESPACE_FINE = " "  # espace insecable, separateur de milliers francais

MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

# Mots qui restent en minuscules au milieu d'un nom propre francais.
PARTICULES = {
    "de", "du", "des", "d", "le", "la", "les", "l", "et", "en", "sur",
    "sous", "aux", "au", "lez", "les", "sainte", "saint",
}


def nombre(v, unite: str = "") -> str:
    """18899 -> '18 899 EUR' (avec espaces insecables)."""
    if v is None:
        return "?"
    texte = "{:,.0f}".format(float(v)).replace(",", ESPACE_FINE)
    return "%s%s%s" % (texte, ESPACE_FINE, unite) if unite else texte


def euros(v) -> str:
    return nombre(v, "€") if v is not None else "prix non communiqué"


def kilometres(v) -> str:
    return nombre(v, "km")


def nom_propre(texte: str) -> str:
    """'RENAULT BOURG EN BRESSE - GROUPE BERNARD' -> 'Renault Bourg en Bresse - Groupe Bernard'.

    Ne touche pas au texte deja correctement casse (l'API melange les deux).
    """
    if not texte:
        return ""
    if texte != texte.upper():
        return texte.strip()
    mots = []
    for i, mot in enumerate(texte.strip().lower().split()):
        if i > 0 and mot in PARTICULES:
            mots.append(mot)
        elif "-" in mot:
            mots.append("-".join(p.capitalize() for p in mot.split("-")))
        else:
            mots.append(mot.capitalize())
    return " ".join(mots)


def decimal(v, unite: str = "") -> str:
    """55.8 -> '55,8 kWh' (virgule décimale française)."""
    if v is None:
        return "?"
    texte = ("%g" % float(v)).replace(".", ",")
    return "%s%s%s" % (texte, ESPACE_FINE, unite) if unite else texte


# Corrections de saisie recurrentes dans les libelles de version des vendeurs.
_ORTHOGRAPHE = [
    (re.compile(r"\bmegane\b", re.I), "Mégane"),
    (re.compile(r"\belectrique\b", re.I), "électrique"),
    (re.compile(r"\bautonomie\b", re.I), "autonomie"),
    (re.compile(r"(\d)\s*ch\b", re.I), r"\1 ch"),
]


def titre_vehicule(texte: str) -> str:
    """'Megane E-Tech EV60 130ch optimum charge ' -> 'Mégane E-Tech EV60 130 ch optimum charge'."""
    t = " ".join((texte or "").split())
    if not t:
        return ""
    for motif, remplacement in _ORTHOGRAPHE:
        t = motif.sub(remplacement, t)
    return t[0].upper() + t[1:]


def phrase(texte: str) -> str:
    """'garantie 3 mois' -> 'Garantie 3 mois' ; 'GRIS SCHISTE' -> 'Gris schiste'."""
    if not texte:
        return ""
    texte = " ".join(texte.split())  # les vendeurs saisissent des espaces multiples
    if texte == texte.upper():
        texte = texte.lower()
    return texte[0].upper() + texte[1:]


def telephone(numero: str) -> str:
    """'+33169722424' -> '01 69 72 24 24'."""
    if not numero:
        return ""
    n = numero.strip()
    if n.startswith("+33"):
        n = "0" + n[3:]
    n = "".join(c for c in n if c.isdigit())
    if len(n) != 10:
        return numero
    return " ".join(n[i:i + 2] for i in range(0, 10, 2))


def _date(valeur: str):
    """Accepte '2023-06-08' et '2023-06' : AutoScout24 ne publie que le mois
    de premiere immatriculation. Le mois seul est ramene au 1er."""
    for gabarit, longueur in (("%Y-%m-%d", 10), ("%Y-%m", 7)):
        try:
            return datetime.strptime(valeur[:longueur], gabarit).date()
        except (ValueError, TypeError):
            continue
    return None


def mois_annee(valeur: str) -> str:
    """'2023-06-08' -> 'juin 2023'."""
    d = _date(valeur)
    return "%s %d" % (MOIS[d.month - 1], d.year) if d else "?"


def date_longue(valeur: str) -> str:
    """'2026-07-09' -> '9 juillet 2026'."""
    d = _date(valeur)
    if not d:
        return "?"
    jour = "1er" if d.day == 1 else str(d.day)
    return "%s %s %d" % (jour, MOIS[d.month - 1], d.year)


def anciennete(valeur: str, aujourdhui: date | None = None) -> str:
    """'2023-06-08' -> '3 ans et 2 mois'."""
    d = _date(valeur)
    if not d:
        return ""
    ref = aujourdhui or date.today()
    mois = (ref.year - d.year) * 12 + (ref.month - d.month)
    if ref.day < d.day:
        mois -= 1
    if mois < 1:
        return "moins d'un mois"
    annees, reste = divmod(max(mois, 0), 12)
    bouts = []
    if annees:
        bouts.append("%d an%s" % (annees, "s" if annees > 1 else ""))
    if reste:
        bouts.append("%d mois" % reste)
    return " et ".join(bouts)


def jours_depuis(valeur: str, aujourdhui: date | None = None) -> int | None:
    """Nombre de jours ecoules depuis une date ISO."""
    d = _date(valeur)
    if not d:
        return None
    return ((aujourdhui or date.today()) - d).days


def jauge(pourcentage, cases: int = 10) -> str:
    """92 -> '▰▰▰▰▰▰▰▰▰▱' (Discord uniquement, pas la console Windows).

    L'echelle demarre a 80 % : sur une batterie de VE, tout se joue entre
    80 et 100, une jauge partant de zero serait toujours pleine.
    """
    if pourcentage is None:
        return ""
    ratio = (float(pourcentage) - 80.0) / 20.0
    pleines = max(0, min(cases, round(ratio * cases)))
    return "▰" * pleines + "▱" * (cases - pleines)
