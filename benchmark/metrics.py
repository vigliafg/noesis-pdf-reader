"""Precision metrics for engine outputs: text fidelity (chrF) + structural checks."""

from __future__ import annotations

import re
from collections import Counter

# Markdown syntax to strip before measuring lexical fidelity.
_MD_RE = re.compile(
    r"(\*\*|__|\*|_|`|~~)"              # emphasis / code / strikethrough
    r"|(^|\n)\s{0,3}#{1,6}\s*"          # atx headings
    r"|!\[[^\]]*\]\([^)]*\)"            # images
    r"|\[([^\]]*)\]\([^)]*\)"           # links (keep label)
    r"|\|"                              # table pipes
    r"|^\s*[-*+]\s+"                    # list bullets
    r"|^\s*\d+\.\s+"                    # ordered list numbers
    r"|^[-=]{3,}$"                      # setext heading rules / hr
    r"|```[a-zA-Z0-9_-]*"               # code fences
    r"|<[^>]+>"                         # raw html tags
)

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip markdown/punctuation, collapse whitespace to single spaces."""
    text = _MD_RE.sub(lambda m: m.group(2) or "", text)
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text.lower()).strip()


def _ngrams(s: str, n: int) -> Counter:
    s = " " + s + " "
    return Counter(s[i : i + n] for i in range(len(s) - n + 1))


def chrf(candidate: str, reference: str, max_n: int = 6) -> float:
    """Character n-gram F1 averaged over orders 1..max_n (chrF), in [0, 1]."""
    cand, ref = normalize(candidate), normalize(reference)
    if not ref:
        return 1.0 if not cand else 0.0
    if not cand:
        return 0.0
    total = 0.0
    for n in range(1, max_n + 1):
        c, r = _ngrams(cand, n), _ngrams(ref, n)
        if not r:
            continue
        tp = sum((c & r).values())
        if tp == 0:
            continue
        prec = tp / sum(c.values())
        rec = tp / sum(r.values())
        total += 2 * prec * rec / (prec + rec)
    return total / max_n


def word_overlap_f1(candidate: str, reference: str) -> float:
    """Word-level F1 overlap (recall-oriented precision metric)."""
    cand = Counter(normalize(candidate).split())
    ref = Counter(normalize(reference).split())
    if not ref:
        return 1.0 if not cand else 0.0
    tp = sum((cand & ref).values())
    if tp == 0:
        return 0.0
    prec = tp / sum(cand.values())
    rec = tp / sum(ref.values())
    return 2 * prec * rec / (prec + rec)


# ─────────────────────────────────────────────────────────────────────────────
#  structural checks
# ─────────────────────────────────────────────────────────────────────────────

def structural_counts(md: str) -> dict:
    """Count headings, tables, images and lists in a markdown string."""
    return {
        "headings": len(re.findall(r"^\s{0,3}#{1,6}\s+\S", md, flags=re.M)),
        "tables": md.count("|---"),
        "images": len(re.findall(r"!\[[^\]]*\]\(", md)),
        "lists": len(re.findall(r"^\s*[-*+]\s+\S", md, flags=re.M)),
        "chars": len(md),
    }


# Page-specific key strings that every engine must capture.
# (1-based page -> expected fragments; case-insensitive substring checks.)
KEY_STRINGS: dict[int, list[str]] = {
    1007: [
        "corresponding human",
        "cellular responses",
        "further reading",
        "table 124-5",
    ],
}


def check_key_strings(md: str, page: int) -> dict:
    """Return, for each expected fragment, whether it appears in the output."""
    lowered = md.lower()
    return {
        frag: frag in lowered for frag in KEY_STRINGS.get(page, [])
    }


def evaluate_page(candidate_md: str, reference_md: str, page: int) -> dict:
    """Aggregate all precision signals for one page into a dict."""
    return {
        "chrf": round(chrf(candidate_md, reference_md), 4),
        "word_f1": round(word_overlap_f1(candidate_md, reference_md), 4),
        "structural": structural_counts(candidate_md),
        "key_strings": check_key_strings(candidate_md, page),
    }
