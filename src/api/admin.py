"""Administration: users, audit-trail viewer and system status (admin only).

Every change is written to the audit trail without secrets. Deactivation and role
changes take effect immediately because each request re-reads the user.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.core.auth import require_roles
from src.core.config import get_settings
from src.core.passwords import validate_password
from src.core.security import hash_password
from src.db.session import get_db
from src.models.banking import BankingAuditEvent
from src.models.kyc import AuditEvent
from src.models.policies import PolicyDocument
from src.models.user import ALLOWED_ROLES, User

router = APIRouter(prefix="/api/v1/admin", tags=["إدارة النظام"])
_ADMIN = require_roles("admin")
_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,49}$")
Role = Literal["admin", "reviewer", "viewer"]
DETAIL_LIMIT = 300


class UserItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=200)
    role: Role


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=1, max_length=200)


def _audit(db: Session, admin: User, user_id: int, action: str, details: str) -> None:
    db.add(
        BankingAuditEvent(
            entity_type="user",
            entity_id=user_id,
            action=action,
            actor_role=admin.role,
            actor_username=admin.username,
            details_ar=details,
        )
    )


def _active_admins(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
    ) or 0


def _get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود.")
    return user


@router.get("/users", response_model=list[UserItem], summary="عرض المستخدمين")
def list_users(admin: User = Depends(_ADMIN), db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)))


@router.post("/users", response_model=UserItem, status_code=201, summary="إنشاء مستخدم")
def create_user(payload: UserCreate, admin: User = Depends(_ADMIN), db: Session = Depends(get_db)) -> User:
    username = payload.username.strip()
    if not _USERNAME.match(username):
        raise HTTPException(
            status_code=422,
            detail="اسم المستخدم: 3 إلى 50 حرفًا إنجليزيًا أو أرقامًا أو (_ . -) ويبدأ بحرف أو رقم.",
        )
    try:
        validate_password(payload.password, username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if db.scalar(select(User).where(func.lower(User.username) == username.lower())):
        raise HTTPException(status_code=409, detail="اسم المستخدم مستخدم من قبل.")
    user = User(username=username, password_hash=hash_password(payload.password), role=payload.role, is_active=True)
    try:
        db.add(user)
        db.flush()
        _audit(db, admin, user.id, "USER_CREATED", f"إنشاء مستخدم بدور «{payload.role}».")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="اسم المستخدم مستخدم من قبل.") from exc
    return user


@router.patch("/users/{user_id}", response_model=UserItem, summary="تغيير دور مستخدم أو إيقافه/تفعيله")
def update_user(
    user_id: int, payload: UserUpdate, admin: User = Depends(_ADMIN), db: Session = Depends(get_db)
) -> User:
    user = _get_user(db, user_id)
    if payload.role is None and payload.is_active is None:
        raise HTTPException(status_code=422, detail="لم يُحدَّد أي تغيير.")
    if user.id == admin.id:
        raise HTTPException(status_code=409, detail="لا يمكنك تغيير دورك أو إيقاف حسابك بنفسك.")
    removes_admin = user.role == "admin" and user.is_active and (
        (payload.role is not None and payload.role != "admin") or payload.is_active is False
    )
    if removes_admin and _active_admins(db) <= 1:
        raise HTTPException(status_code=409, detail="لا يمكن إزالة آخر مدير نشط في النظام.")

    changes = []
    if payload.role is not None and payload.role != user.role:
        changes.append(f"الدور من «{user.role}» إلى «{payload.role}»")
        user.role = payload.role
    if payload.is_active is not None and payload.is_active != user.is_active:
        changes.append("تفعيل الحساب" if payload.is_active else "إيقاف الحساب")
        user.is_active = payload.is_active
    if changes:
        _audit(db, admin, user.id, "USER_UPDATED", "؛ ".join(changes) + ".")
        db.commit()
    return user


@router.post("/users/{user_id}/reset-password", status_code=204, summary="تعيين كلمة مرور جديدة لمستخدم")
def reset_password(
    user_id: int, payload: PasswordReset, admin: User = Depends(_ADMIN), db: Session = Depends(get_db)
) -> None:
    user = _get_user(db, user_id)
    try:
        validate_password(payload.new_password, user.username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user.password_hash = hash_password(payload.new_password)
    _audit(db, admin, user.id, "PASSWORD_RESET", "تعيين كلمة مرور جديدة بواسطة المدير.")
    db.commit()


# --------------------------------------------------------------- audit trail
class AuditRow(BaseModel):
    at: datetime
    source: Literal["banking", "kyc"]
    entity: str
    entity_id: int
    action: str
    actor: str | None
    details: str


class AuditPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[AuditRow]


@router.get("/audit-events", response_model=AuditPage, summary="سجل التدقيق (للمدير)")
def audit_events(
    source: Literal["all", "banking", "kyc"] = "all",
    action: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1, le=1000),
    page_size: int = Query(default=25, ge=1, le=100),
    admin: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
) -> AuditPage:
    """Merged view of both audit tables, newest first. Texts are cut to a safe length."""
    window = page * page_size
    rows: list[AuditRow] = []
    total = 0
    if source in ("all", "banking"):
        query = select(BankingAuditEvent)
        if action:
            query = query.where(BankingAuditEvent.action == action)
        total += db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows += [
            AuditRow(
                at=e.created_at, source="banking", entity=e.entity_type, entity_id=e.entity_id,
                action=e.action, actor=e.actor_username, details=e.details_ar[:DETAIL_LIMIT],
            )
            for e in db.scalars(query.order_by(BankingAuditEvent.created_at.desc(), BankingAuditEvent.id.desc()).limit(window))
        ]
    if source in ("all", "kyc"):
        query = select(AuditEvent)
        if action:
            query = query.where(AuditEvent.action == action)
        total += db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows += [
            AuditRow(
                at=e.created_at, source="kyc", entity="kyc_application", entity_id=e.application_id,
                action=e.action, actor=e.actor_name, details=e.details_ar[:DETAIL_LIMIT],
            )
            for e in db.scalars(query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(window))
        ]
    rows.sort(key=lambda r: r.at, reverse=True)
    start = (page - 1) * page_size
    return AuditPage(total=total, page=page, page_size=page_size, items=rows[start : start + page_size])


# ------------------------------------------------------------------- system
class SystemStatus(BaseModel):
    version: str
    environment: str
    database: str
    users_by_role: dict[str, int]
    active_policies: int
    llm_provider: str
    llm_model: str | None
    ocr_engine_installed: bool
    pdf_rasterizer_installed: bool
    docs_enabled: bool


@router.get("/system", response_model=SystemStatus, summary="حالة النظام (للمدير)")
def system_status(admin: User = Depends(_ADMIN), db: Session = Depends(get_db)) -> SystemStatus:
    settings = get_settings()
    roles = {role: 0 for role in ALLOWED_ROLES}
    try:
        for role, count in db.execute(select(User.role, func.count()).where(User.is_active.is_(True)).group_by(User.role)):
            roles[role] = count
        policies = db.scalar(select(func.count()).select_from(PolicyDocument).where(PolicyDocument.is_active.is_(True))) or 0
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="قاعدة البيانات غير متاحة.") from exc
    return SystemStatus(
        version="1.0.0",
        environment=settings.app_env,
        database=settings.database_url.split(":", 1)[0].split("+")[0],  # backend name only, never credentials
        users_by_role=roles,
        active_policies=policies,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model if settings.llm_provider != "none" else None,
        ocr_engine_installed=shutil.which(settings.tesseract_cmd) is not None,
        pdf_rasterizer_installed=shutil.which("pdftoppm") is not None,
        docs_enabled=settings.docs_enabled,
    )
