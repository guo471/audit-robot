from .audit_models import AuditImage, AuditRequest, AuditResponse, normalize_decision
from .category_classifier import CategoryResult, classify_audit_category
from .code_extractor import CodeExtractor
from .image_role import classify_image_role, group_images_by_role, required_roles_present
from .privacy import redact_text, safe_report_row, remove_temp_dir
from .report_writer import append_report_row

__all__ = [
    "OCREngine",
    "IDCardParser",
    "CodeExtractor",
    "ImageForensics",
    "AddressChecker",
    "RuleEngine",
    "AuditImage",
    "AuditRequest",
    "AuditResponse",
    "normalize_decision",
    "classify_image_role",
    "group_images_by_role",
    "required_roles_present",
    "redact_text",
    "safe_report_row",
    "remove_temp_dir",
    "append_report_row",
    "CategoryResult",
    "classify_audit_category",
    "AuditDependencies",
    "audit_request",
]


def __getattr__(name):
    if name == "OCREngine":
        from .ocr_engine import OCREngine

        return OCREngine
    if name == "IDCardParser":
        from .id_card_parser import IDCardParser

        return IDCardParser
    if name == "ImageForensics":
        from .image_forensics import ImageForensics

        return ImageForensics
    if name == "AddressChecker":
        from .address_checker import AddressChecker

        return AddressChecker
    if name == "RuleEngine":
        from .rule_engine import RuleEngine

        return RuleEngine
    if name in {"AuditDependencies", "audit_request"}:
        from .audit_runner import AuditDependencies, audit_request

        return {"AuditDependencies": AuditDependencies, "audit_request": audit_request}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
