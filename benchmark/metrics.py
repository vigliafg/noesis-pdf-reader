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
    157: [
        "temporal artery",
        "tension-type headache",
        "recurrent headache disorders",
    ],
}


def check_key_strings(md: str, page: int) -> dict:
    """Return, for each expected fragment, whether it appears in the output.

    Matching is case-insensitive and whitespace-normalized, so fragments that
    wrap across a line break still match.
    """
    lowered = _WS_RE.sub(" ", md.lower())
    return {
        frag: _WS_RE.sub(" ", frag.lower()) in lowered
        for frag in KEY_STRINGS.get(page, [])
    }


def evaluate_page(candidate_md: str, reference_md: str, page: int) -> dict:
    """Aggregate all precision signals for one page into a dict."""
    return {
        "chrf": round(chrf(candidate_md, reference_md), 4),
        "word_f1": round(word_overlap_f1(candidate_md, reference_md), 4),
        "structural": structural_counts(candidate_md),
        "key_strings": check_key_strings(candidate_md, page),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  paragraph-level reliability (vs an AI OCR gold reference)
# ─────────────────────────────────────────────────────────────────────────────

def split_paragraphs(md: str) -> list[str]:
    """Split markdown into non-empty paragraphs (on blank lines)."""
    return [p.strip() for p in re.split(r"\n\s*\n", md) if p.strip()]


def paragraph_count(md: str) -> int:
    """Number of non-empty paragraphs."""
    return len(split_paragraphs(md))


def _paragraph_overlap(a: str, b: str) -> float:
    """Dice coefficient over normalized word sets."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return 2 * inter / (len(ta) + len(tb))


def _lis_length(seq: list[int]) -> int:
    """Length of the longest increasing subsequence."""
    if not seq:
        return 0
    tails: list[int] = []
    for x in seq:
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(tails):
            tails.append(x)
        else:
            tails[lo] = x
    return len(tails)


def paragraph_order_score(candidate_md: str, reference_md: str, min_len: int = 12) -> dict:
    """How well the candidate reproduces the reference's reading order.

    Each (non-trivial) reference paragraph is fuzzy-matched to its best
    candidate paragraph; the longest increasing subsequence of matched
    candidate indices measures how much of the reference order is preserved.

    Returns ``{matched, total, order_ratio}`` where ``order_ratio`` is the
    fraction of reference paragraphs that appear in the correct relative order.
    """
    ref_paras = [
        normalize(p) for p in split_paragraphs(reference_md)
        if len(normalize(p)) >= min_len
    ]
    cand_paras = [normalize(p) for p in split_paragraphs(candidate_md)]
    if not ref_paras:
        return {"matched": 0, "total": 0, "order_ratio": 1.0}

    matched_indices: list[int] = []
    for rp in ref_paras:
        best_idx, best_ov = -1, 0.0
        for i, cp in enumerate(cand_paras):
            ov = _paragraph_overlap(rp, cp)
            if ov > best_ov:
                best_ov, best_idx = ov, i
        if best_idx >= 0 and best_ov >= 0.4:
            matched_indices.append(best_idx)

    matched = len(matched_indices)
    return {
        "matched": matched,
        "total": len(ref_paras),
        "order_ratio": round(_lis_length(matched_indices) / len(ref_paras), 4),
    }


def evaluate_reliability(candidate_md: str, gold_md: str, page: int) -> dict:
    """Reliability metrics of a candidate against an AI OCR gold reference."""
    return {
        "chrf": round(chrf(candidate_md, gold_md), 4),
        "word_f1": round(word_overlap_f1(candidate_md, gold_md), 4),
        "paragraphs": {
            "gold": paragraph_count(gold_md),
            "candidate": paragraph_count(candidate_md),
        },
        "order": paragraph_order_score(candidate_md, gold_md),
        "anchors": anchor_order_check(candidate_md, page),
        "key_strings": check_key_strings(candidate_md, page),
    }


# Ordered anchor fragments per page, in TRUE reading order.
# These are curated by a human from the page geometry (column top→bottom,
# left→right), so they verify reading-sequence fidelity independently of
# paragraph segmentation (which differs wildly between engines) and of the
# AI gold's own omissions.
READING_ORDER_ANCHORS: dict[int, list[str]] = {
    156: [
        "perforation of abdominal viscera",      # left column (prev. chapter)
        "further reading",                        # left column bottom
        "headache is among the most common reasons",  # right column top
        "general principles",
        "anatomy and physiology of headache",
    ],
    157: [
        "innervation of the large intracranial vessels",
        "clinical evaluation of acute",
        "a careful neurologic examination",
        "effective in the preventive",
        "region of temporal artery",                # TABLE 17-2 (left col bottom)
        "treatment of both tension-type headache",  # right column top
        "underlying recurrent headache disorders",
        "management of secondary headache focuses",
        "stiff neck and fever suggests meningitis",
        "arteritis is an inflammatory disorder",
    ],
    1007: [
        "table 124-5",                              # full-width table (top)
        "the study of infectious diseases",         # left column
        "further reading",                          # left column bottom
        "cellular responses to microbes",           # right column top
        "entry into the human host",
        "entry into the respiratory tract",
        "entry into the gastrointestinal tract",
    ],
}


def anchor_order_check(md: str, page: int) -> dict:
    """Check that ordered anchors appear in the correct relative order.

    Returns:
      ``aligned`` — one entry per anchor (None when the anchor is missing),
      ``positions`` — the found anchors' offsets (non-decreasing iff in order),
      ``missing`` — the anchors that were not found,
      ``in_order`` — whether the found anchors are in the correct order.
    """
    anchors = READING_ORDER_ANCHORS.get(page, [])
    if not anchors:
        return {"aligned": [], "positions": [], "missing": [], "in_order": True}
    lowered = _WS_RE.sub(" ", md.lower())
    aligned: list[int | None] = []
    missing: list[str] = []
    for frag in anchors:
        pos = lowered.find(_WS_RE.sub(" ", frag.lower()))
        aligned.append(pos if pos >= 0 else None)
        if pos < 0:
            missing.append(frag)
    found = [p for p in aligned if p is not None]
    return {
        "aligned": aligned,
        "positions": found,
        "missing": missing,
        "in_order": found == sorted(found),
    }
