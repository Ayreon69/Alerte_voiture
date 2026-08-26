"""Sources d'annonces. Une source = un site, avec une methode `chercher()`."""
from .renew import SourceRenew, ErreurSource

SOURCES = {
    "renew": SourceRenew,
}

__all__ = ["SOURCES", "SourceRenew", "ErreurSource"]
