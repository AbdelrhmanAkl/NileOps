"""Deterministic generator of clean, unique, Egyptian-style synthetic people (no real individuals)."""

import random
from datetime import date

MALE = ["أحمد", "محمد", "محمود", "مصطفى", "عمر", "يوسف", "عبد الرحمن", "كريم", "طارق", "هشام", "إبراهيم", "خالد",
        "حسن", "علي", "ياسر", "ماجد", "سامح", "وليد", "أمير", "شريف", "إسلام", "عمرو", "تامر", "بسام"]
FEMALE = ["سارة", "مريم", "نور", "منى", "آية", "دينا", "ملك", "هبة", "رنا", "ياسمين", "نهى", "سلمى", "هند", "مي",
          "إيمان", "عبير", "شيماء", "نادين", "رحمة", "فاطمة", "جيهان", "داليا"]
FAMILY = ["السيد", "حسن", "إبراهيم", "عبد الله", "فتحي", "مصطفى", "عادل", "شعبان", "منصور", "سليمان", "عثمان", "رضوان",
          "الشرقاوي", "زكريا", "عبد العزيز", "البدري", "النجار", "الجمال", "صلاح", "فهمي", "جابر", "نصر", "طه", "قاسم"]

# National-ID governorate codes (first two digits after the birth date).
GOVERNORATES = {
    "القاهرة": "01", "الإسكندرية": "02", "الجيزة": "21", "الدقهلية": "12", "الشرقية": "13",
    "الغربية": "16", "المنوفية": "17", "أسيوط": "27", "قنا": "29", "أسوان": "28",
}

BRANCHES = [  # (code, name, governorate)
    ("CAI01", "فرع وسط البلد", "القاهرة"), ("CAI02", "فرع مدينة نصر", "القاهرة"),
    ("CAI03", "فرع المعادي", "القاهرة"), ("GIZ01", "فرع الدقي", "الجيزة"),
    ("GIZ02", "فرع 6 أكتوبر", "الجيزة"), ("ALX01", "فرع كورنيش الإسكندرية", "الإسكندرية"),
    ("MNS01", "فرع المنصورة", "الدقهلية"), ("TNT01", "فرع طنطا", "الغربية"),
    ("ZAG01", "فرع الزقازيق", "الشرقية"), ("AST01", "فرع أسيوط", "أسيوط"),
]


def person(index: int) -> dict:
    """Unique name for each index (up to ~8000): first + father + grandfather, gender-aware."""
    rng = random.Random(index * 7919)
    female = index % 3 == 0
    pool = FEMALE if female else MALE
    # Mixed-radix digits of a permuted index: unique for every index below F*M*A, yet well mixed.
    total = len(pool) * len(MALE) * len(FAMILY)
    j = (index * 7919) % total  # 7919 is prime and does not divide total, so this is a bijection
    first = pool[j % len(pool)]
    father = MALE[(j // len(pool)) % len(MALE)]
    family = FAMILY[(j // (len(pool) * len(MALE))) % len(FAMILY)]
    branch_code, branch_name, governorate = BRANCHES[index % len(BRANCHES)]
    born = date(rng.randint(1965, 2003), rng.randint(1, 12), rng.randint(1, 28))
    return {
        "name": f"{first} {father} {family}",
        "female": female,
        "governorate": governorate,
        "gov_code": GOVERNORATES[governorate],
        "born": born,
        "branch": (branch_code, branch_name, governorate),
        "mobile_last_four": f"{rng.randint(0, 9999):04d}",
    }
