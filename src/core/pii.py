"""Redaction of personal identifiers from free-text staff notes.

Staff notes end up in review records and the audit trail. Employees may paste an ID number or a phone
number by mistake, so long digit sequences are masked before anything is stored. This is a safety net,
not a guarantee: names and addresses cannot be detected reliably, and the UI tells staff not to enter them.
"""

import re
from typing import Annotated

from pydantic import AfterValidator, Field

_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# 8 or more digits, optionally separated by spaces or dashes (IDs, phones, card and account numbers).
_LONG_NUMBER = re.compile(r"(?<![\w])(?:\d[ \-]?){7,}\d(?![\w])")
_MASK = "[رقم محجوب]"


def redact_pii(text: str) -> str:
    normalized = text.translate(_ARABIC_INDIC)
    return _LONG_NUMBER.sub(_MASK, normalized).strip()


# Reviewer free text: 3-2000 characters, long numbers masked.
StaffNote = Annotated[str, Field(min_length=3, max_length=2000), AfterValidator(redact_pii)]
