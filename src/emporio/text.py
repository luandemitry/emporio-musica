"""Text normalisation for searching the (Portuguese) catalogue.

Deliberately tiny: the catalogue has ~65 products, so accent stripping, a naive
plural stemmer and a handful of synonyms are enough, and keep search predictable.
"""

from __future__ import annotations

import re
import unicodedata

# Filler words customers (and LLMs) put in queries; they never help matching.
STOPWORDS = frozenset(
    {
        "a", "as", "o", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos",
        "e", "ou", "em", "no", "na", "com", "para", "pra", "por", "que", "ate",
        "qual", "quais", "tem", "voce", "voces",
    }
)  # fmt: skip

# Customer vocabulary -> catalogue vocabulary (applied after stemming).
SYNONYMS = {
    "eletroacustico": "eletrico",
    "eletrica": "eletrico",
    "ukelele": "ukulele",
}


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def stem(token: str) -> str:
    """Naive plural folding: 'violoes' -> 'violao', 'ukuleles' -> 'ukulele'."""
    if token.endswith("oes") and len(token) > 4:
        return token[:-3] + "ao"
    if token.isalpha() and len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase, accent-free, stop-word-free, stemmed tokens."""
    words = re.findall(r"[a-z0-9]+", strip_accents(text).lower())
    stemmed = (stem(w) for w in words if w not in STOPWORDS)
    return [SYNONYMS.get(t, t) for t in stemmed]


def compact(text: str) -> str:
    """Alphanumerics only, so 'GD 20', 'GD-20' and 'gd20' compare equal."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(text).lower())
