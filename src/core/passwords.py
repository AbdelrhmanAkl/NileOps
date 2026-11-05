"""Password policy shared by the admin API and the command-line tool."""

from __future__ import annotations

MIN_LENGTH = 12
MAX_LENGTH = 128
_COMMON = {
    "password1234", "123456789012", "qwertyuiop12", "admin1234567", "letmein12345",
    "welcome12345", "iloveyou1234", "password-123", "changeme1234",
}


def validate_password(password: str, username: str = "") -> None:
    """Raise ValueError (Arabic message) when the password is too weak."""
    if len(password) < MIN_LENGTH:
        raise ValueError(f"كلمة المرور يجب ألا تقل عن {MIN_LENGTH} حرفًا.")
    if len(password) > MAX_LENGTH:
        raise ValueError(f"كلمة المرور يجب ألا تزيد عن {MAX_LENGTH} حرفًا.")
    lowered = password.lower()
    if username and (username.lower() in lowered):
        raise ValueError("كلمة المرور يجب ألا تحتوي على اسم المستخدم.")
    if len(set(password)) < 5 or lowered in _COMMON:
        raise ValueError("كلمة المرور سهلة التخمين؛ استخدم عبارة أطول وأكثر تنوعًا.")
    classes = sum(
        any(test(c) for c in password)
        for test in (str.islower, str.isupper, str.isdigit, lambda c: not c.isalnum())
    )
    if classes < 3:
        raise ValueError("استخدم ثلاثة أنواع على الأقل من: حروف صغيرة، كبيرة، أرقام، رموز.")
