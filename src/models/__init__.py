"""Central model imports for SQLAlchemy metadata registration."""

from src.models.kyc import (
    ApplicationStatus,
    AuditEvent,
    DocumentStatus,
    KYCApplication,
    KYCDocument,
    ValidationResult,
    ValidationSeverity,
)
from src.models.user import User
from src.models.documents import DocumentFile, ExtractedField
from src.models.policies import PolicyChunk, PolicyDocument
from src.models.banking import (
    AccountOpeningRequest,
    BankAccount,
    BankingAuditEvent,
    CardIssuanceRequest,
    Complaint,
    Customer,
)

__all__ = [
    "ApplicationStatus",
    "AuditEvent",
    "DocumentStatus",
    "KYCApplication",
    "KYCDocument",
    "ValidationResult",
    "ValidationSeverity",
    "User",
    "DocumentFile",
    "ExtractedField",
    "PolicyChunk",
    "PolicyDocument",
    "AccountOpeningRequest",
    "BankAccount",
    "BankingAuditEvent",
    "CardIssuanceRequest",
    "Complaint",
    "Customer",
]