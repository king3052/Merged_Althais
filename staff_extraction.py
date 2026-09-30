"""
staff_extraction.py — reading details (license number, state, expiration…) out of staff documents.

Staff onboarding (staff_onboarding.py) calls extract() after an employee uploads a document. Whatever comes back
is only ever a suggestion: the employee confirms it and a manager verifies it before it becomes part of the staff
record. Nothing here marks anything verified.

Althais has no document-reading (OCR / vision) service today, so no extractor is registered and every document
comes back "unavailable": the employee types the details in themselves. To add one, write a function with the
Extractor signature and register it:

    def my_extractor(doc_type, filename, content_type, data) -> ExtractionResult | None: ...
    register(my_extractor)

Return None for documents it can't handle, so the next extractor gets a turn.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional


# The details each kind of document can carry. The portal's "Confirm Information" form and the manager's review
# panel show these fields, whether they were filled in by an extractor or typed by the employee.
DOC_FIELDS = {
    "license": [("identifier", "License Number"), ("state", "State"), ("issuing_authority", "Issuing Board"),
                ("issue_date", "Issue Date"), ("expiration_date", "Expiration Date"), ("name", "Name On License")],
    "bls": [("identifier", "Card Or eCard Number"), ("issuing_authority", "Issued By"),
            ("issue_date", "Issue Date"), ("expiration_date", "Expiration Date"), ("name", "Name On Card")],
    "dea": [("identifier", "DEA Number"), ("state", "State"), ("expiration_date", "Expiration Date"), ("name", "Registrant Name")],
    "immunizations": [("issue_date", "Date Of Record"), ("expiration_date", "Next Due (If Any)")],
    "gov_id": [("expiration_date", "Expiration Date"), ("name", "Name On ID")],
    "other": [("identifier", "Document Number"), ("expiration_date", "Expiration Date")],
}


def fields_for(doc_type: str) -> list:
    return DOC_FIELDS.get(doc_type) or DOC_FIELDS["other"]


@dataclass
class ExtractionResult:
    status: str                                   # "extracted" | "unavailable" | "failed"
    fields: dict = field(default_factory=dict)    # field key -> {"value": str, "confidence": 0..1}
    provider: str = ""                            # which extractor produced it, kept with the document
    note: str = ""                                # shown to the employee and the manager


Extractor = Callable[[str, str, str, bytes], Optional[ExtractionResult]]
_EXTRACTORS: list = []


def register(extractor: Extractor) -> None:
    _EXTRACTORS.append(extractor)


def extract(doc_type: str, filename: str, content_type: str, data: bytes) -> ExtractionResult:
    """Run the registered extractors in order; the first one that handles the document wins."""
    for extractor in _EXTRACTORS:
        try:
            result = extractor(doc_type, filename, content_type, data)
        except Exception as e:   # a broken extractor must never lose the upload
            return ExtractionResult(status="failed", provider=getattr(extractor, "__name__", "extractor"),
                                    note=f"Althais couldn't read this document ({type(e).__name__}). Enter the details yourself.")
        if result is not None:
            allowed = {k for k, _ in fields_for(doc_type)}
            result.fields = {k: v for k, v in (result.fields or {}).items() if k in allowed and str((v or {}).get("value") or "").strip()}
            return result
    return ExtractionResult(status="unavailable",
                            note="Automatic reading isn't available for this document yet. Enter the details from it below.")
