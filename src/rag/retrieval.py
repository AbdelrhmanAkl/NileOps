"""Deterministic Arabic retrieval (BM25) over policy chunks.

No embeddings or external services: results are reproducible and explainable.
Each result carries a ``coverage`` value (share of the question's informative
words found in the passage) that the answer layer uses to decide whether to
answer or abstain.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from src.services.complaint_router import normalize_arabic

_TOKEN = re.compile(r"[؀-ۿa-zA-Z0-9]+")
_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ب", "ل", "ف", "ك")
_SUFFIXES = ("ات", "ون", "ين", "ان", "ها", "هم", "نا", "ه", "ي", "ا")
_PAST_T = "ت"
_PUNCT = re.compile(r"[،؛؟,.!:;()\[\]\"'«»]")

# Question words and very common function words that carry no topic.
STOP_WORDS = frozenset(
    normalize_arabic(w)
    for w in """
    ما ماذا ماهي ماهو هل كم متي اين كيف لماذا من الي علي عن في مع او ان اذا لو
    هذا هذه ذلك تلك هو هي هم التي الذي الذين يجب يتم يكون تكون كان كانت
    لا لم لن قد ثم حتي بعد قبل عند اي كل بين غير فقط ايضا
    العميل عميل العملاء عملاء الموظف موظف الموظفين النظام
    كام ايه ايش ليه امتي فين ازاي لازم يمكن يجوز عمره عمر اعمل افعل افعله
    مبلغ المبلغ يعتبر يعتبرالحساب مباشره
    اللي الي عشان منه منها فيه فيها مش بتاع بتاعه بتاعت بتاعها ينفع اقدر تقدر يقدر حد
    بكره بكرا النهارده دلوقتي ده دي دول انا انت احنا لسه برضه كده يعني عايز عاوز محتاج
    محتاجه محتاجها محتاجين بيقول بيعمل اعمل اعملها هعمل ممكن ازاي ليه اي ايوه طيب
    بس خلاص تاني مين وانا والعميل للعميل بالعميل
    """.split()
)

# Dialect / spelling variants of words used in the policies, mapped to the
# policy wording. Applied to questions only. Keep it short and explainable.
QUERY_REWRITES = {
    normalize_arabic(k): normalize_arabic(v)
    for k, v in {
        "اتسرقت": "سرقت", "اتسرق": "سرقت", "اتسرقه": "سرقت", "ضاعت": "فقدان", "ضاع": "فقدان",
        "اتخصم": "خصم", "اتخصمت": "خصم", "اتحول": "تحويل", "اطلب": "يطلب", "اشهر": "شهر",
        "بلغ": "ابلاغ", "otp": "رمز التحقق", "اوتيبي": "رمز التحقق", "اتاخر": "تاخر",
        "متاخر": "تاخر", "نحتفظ": "احتفاظ", "نحتفظ": "احتفاظ", "سنه": "سنوات", "سنين": "سنوات", "ارقام": "رقم", "مرتين": "مكرر", "اتقفل": "اغلاق", "تتقفل": "اغلاق", "نقفل": "اغلاق", "قفل": "اغلاق", "اقفل": "اغلاق",
        "كارت": "بطاقه", "كارته": "بطاقه", "كارتي": "بطاقه", "كروت": "بطاقه", "كارد": "بطاقه",
        "باسورد": "رقم سري", "كود": "رمز", "ورق": "مستندات", "اوراق": "مستندات",
        "هتوصل": "تسليم", "توصل": "تسليم", "يوصل": "تسليم", "تتسلم": "تسليم",
        "رجعوا": "رد", "ترجع": "رد", "يرجع": "رد", "ارجع": "رد",
        "الايميل": "بريد", "ايميل": "بريد", "الاليكتروني": "الكتروني",
        "الباسورد": "رقم سري", "افتح": "فتح", "يفتح": "فتح", "نفتح": "فتح", "فتحت": "فتح", "بتخلص": "تسليم", "يخلص": "تسليم",
    }.items()
}
_BM25_K1 = 1.4
_BM25_B = 0.75


def stem(token: str) -> str:
    """Light Arabic stemmer: strip up to two prefixes and one suffix."""
    for _ in range(2):
        for prefix in _PREFIXES:
            if token.startswith(prefix) and len(token) - len(prefix) >= 3:
                token = token[len(prefix):]
                break
        else:
            break
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            break
    if token.endswith(_PAST_T) and len(token) - 1 >= 3:
        token = token[:-1]  # past-tense / possessive ta: "سرقت" == "سرقة"
    return token


def _rewrite_token(raw: str) -> str:
    for candidate in (raw, raw[2:] if raw.startswith("ال") else raw, raw[1:] if raw[:1] in "وبلف" else raw):
        if candidate in QUERY_REWRITES:
            return QUERY_REWRITES[candidate]
    return raw


def tokenize(text: str, *, rewrite: bool = False) -> list[str]:
    """Normalize, drop stop words and stem. ``rewrite`` maps dialect words (questions)."""
    text = _PUNCT.sub(" ", normalize_arabic(text))
    tokens: list[str] = []
    for raw in _TOKEN.findall(text):
        if rewrite:
            raw = _rewrite_token(raw)
        for part in raw.split():
            if part in STOP_WORDS or (part.startswith(("و", "ف")) and part[1:] in STOP_WORDS):
                continue
            tokens.append(stem(part))
    return [t for t in tokens if t]


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: int
    policy_code: str
    version: int
    title: str
    heading: str
    text: str


@dataclass(frozen=True)
class Hit:
    chunk: IndexedChunk
    score: float
    coverage: float
    matched_terms: tuple[str, ...]


class PolicyIndex:
    def __init__(self, chunks: list[IndexedChunk]) -> None:
        self.chunks = chunks
        self._tokens = [tokenize(f"{c.title} {c.heading} {c.text}") for c in chunks]
        self._counts = [Counter(t) for t in self._tokens]
        self._lengths = [len(t) for t in self._tokens]
        self._avg_len = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        df: Counter[str] = Counter()
        for counts in self._counts:
            df.update(counts.keys())
        n = len(chunks)
        self._idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }
        self._max_idf = max(self._idf.values(), default=1.0)

    def idf(self, term: str) -> float:
        # A word the corpus has never seen is maximally informative: if the
        # question is full of such words, coverage drops and we abstain.
        return self._idf.get(term, self._max_idf)

    def search(self, query: str, top_k: int = 3) -> list[Hit]:
        terms = list(dict.fromkeys(tokenize(query, rewrite=True)))
        if not terms or not self.chunks:
            return []
        total_weight = sum(self.idf(t) for t in terms)
        hits: list[Hit] = []
        for index, chunk in enumerate(self.chunks):
            counts = self._counts[index]
            length = self._lengths[index] or 1
            score = 0.0
            matched: list[str] = []
            for term in terms:
                freq = counts.get(term, 0)
                if not freq:
                    continue
                matched.append(term)
                norm = freq + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / (self._avg_len or 1))
                score += self.idf(term) * freq * (_BM25_K1 + 1) / norm
            if not matched:
                continue
            coverage = sum(self.idf(t) for t in matched) / total_weight
            hits.append(Hit(chunk, score, coverage, tuple(matched)))
        hits.sort(key=lambda h: (-h.score, h.chunk.chunk_id))
        return hits[:top_k]
