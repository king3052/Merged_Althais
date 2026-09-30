"""
staff_extraction.py, reading a staff document: what it is, how usable it is, and what it says.

The analyzer only *observes*. It classifies the document, reports quality problems and anything that looks
unusual, and extracts typed fields with a confidence and where on the page each came from. It never decides
whether a document is acceptable: staff_doc_review.py does that with deterministic rules, and anything it can't
confirm goes to a person.

Analyzers are pluggable (register()). The one built in reads documents with Claude and is used only when
ANTHROPIC_API_KEY is set; without it, analysis is "unavailable" and every document goes to the clinic's manager.
"""

import base64
import os
import warnings
from dataclasses import dataclass, field
from typing import Callable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict


# ──────────────────────────────────────────────────────────────────────────
#  Document types and their typed field schemas
# ──────────────────────────────────────────────────────────────────────────
DOC_TYPES = {
    "medical_license": "Medical License", "nursing_license": "Nursing License",
    "professional_credential": "Professional Credential", "bls": "BLS Card", "acls": "ACLS Card", "pals": "PALS Card",
    "training_certificate": "Training Certificate", "dea": "DEA Registration", "immunization_record": "Immunization Record",
    "government_id": "Identification", "background_check": "Background Check", "clinic_agreement": "Clinic Agreement",
    "employment_form": "Employment Form", "other": "Other", "unreadable": "Unreadable",
}

_LICENSE = [("holder_name", "Name"), ("license_number", "License Number"), ("state", "State"), ("credential_type", "Credential Type"),
            ("issuing_authority", "Issuing Authority"), ("issue_date", "Issue Date"), ("expiration_date", "Expiration Date")]
_LIFE_SUPPORT = [("holder_name", "Name"), ("credential_type", "Credential Type"), ("issuer", "Issuer"), ("issue_date", "Issue Date"),
                 ("expiration_date", "Expiration Date"), ("credential_number", "Credential Number")]
FIELD_SCHEMAS = {
    "medical_license": _LICENSE, "nursing_license": _LICENSE, "professional_credential": _LICENSE,
    "bls": _LIFE_SUPPORT, "acls": _LIFE_SUPPORT, "pals": _LIFE_SUPPORT,
    "training_certificate": [("holder_name", "Employee Name"), ("training_type", "Training Type"), ("completion_date", "Completion Date"),
                             ("expiration_date", "Expiration Date"), ("provider", "Training Provider")],
    "dea": [("holder_name", "Registrant Name"), ("dea_number", "DEA Number"), ("state", "State"), ("expiration_date", "Expiration Date")],
    "immunization_record": [("holder_name", "Name"), ("vaccines", "Vaccines Listed"), ("record_date", "Date Of Record"),
                            ("expiration_date", "Next Due")],
    # an ID's number is never extracted: the name, date of birth and expiration are all verification needs
    "government_id": [("holder_name", "Name On ID"), ("id_type", "ID Type"), ("date_of_birth", "Date Of Birth"),
                      ("expiration_date", "Expiration Date")],
    "background_check": [("holder_name", "Name"), ("provider", "Screening Provider"), ("completion_date", "Completion Date"),
                         ("result", "Result")],
    "other": [("holder_name", "Name"), ("document_date", "Document Date"), ("expiration_date", "Expiration Date")],
}
DATE_FIELDS = {"issue_date", "expiration_date", "completion_date", "record_date", "date_of_birth", "document_date"}

# the document type an onboarding requirement asks for (requirement docType -> analyzer document type)
REQUIREMENT_TYPES = {"license": "medical_license", "bls": "bls", "dea": "dea", "immunizations": "immunization_record",
                     "gov_id": "government_id", "training_certificate": "training_certificate", "other": "other"}


def schema_type(doc_type: str, credential_type: str = "") -> str:
    """The field schema for a requirement's docType (or an analyzer document type)."""
    if doc_type == "license":
        return "nursing_license" if credential_type == "nursing_license" else "medical_license"
    return REQUIREMENT_TYPES.get(doc_type, doc_type if doc_type in FIELD_SCHEMAS else "other")


def fields_for(doc_type: str, credential_type: str = "") -> list:
    """(key, label) pairs shown on the employee's and the manager's forms."""
    return FIELD_SCHEMAS[schema_type(doc_type, credential_type)]


# ──────────────────────────────────────────────────────────────────────────
#  What an analyzer returns (also the structured-output schema the Claude analyzer asks for)
# ──────────────────────────────────────────────────────────────────────────
QUALITY_CODES = ("BLURRY", "CUT_OFF_EDGES", "GLARE", "OBSTRUCTED", "LOW_RESOLUTION", "MISSING_PAGES", "UNREADABLE_TEXT",
                 "INCOMPLETE", "CORRUPTED", "OTHER")
ANOMALY_CODES = ("VISUAL_INCONSISTENCY", "ALTERED_LOOKING_TEXT", "MISMATCHED_FONTS", "UNEXPECTED_LAYOUT", "CROPPED_CRITICAL_AREA",
                 "DATE_LOOKS_MODIFIED", "CONFLICTING_CONTENT", "PAGES_UNRELATED", "OTHER")


class ExtractedField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str                      # one of the field keys for the detected document type
    value: str                    # normalized; dates as YYYY-MM-DD only when unambiguous; "" when absent or unreadable
    raw_text: str                 # exactly as printed
    confidence: float             # 0.0-1.0: how sure the value is read correctly
    page: int                     # 1-based page; 0 when unknown
    location: str                 # where on the page, e.g. "lower right, next to 'Expires'"
    other_values_seen: List[str]  # a different value for this same field elsewhere in the document


class QualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal[QUALITY_CODES]
    severity: Literal["minor", "major"]   # major = it stops a field being read confidently
    detail: str


class Anomaly(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal[ANOMALY_CODES]
    severity: Literal["low", "medium", "high"]
    detail: str


class PageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int
    description: str
    holder_names: List[str]       # every person named as the holder/recipient on this page


class DocumentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_type: Literal[tuple(DOC_TYPES)]
    document_type_confidence: float
    title_text: str               # the document's own title/heading as printed
    page_count: int
    readable: bool                # can the key information be read at all
    looks_complete: bool          # all pages/sides present, nothing cut off
    quality_issues: List[QualityIssue]
    pages: List[PageSummary]
    fields: List[ExtractedField]
    anomalies: List[Anomaly]


@dataclass
class AnalysisResult:
    status: str                                   # "analyzed" | "unavailable" | "failed"
    analysis: Optional[DocumentAnalysis] = None
    processor: str = ""                           # which analyzer, and its model
    note: str = ""


Analyzer = Callable[[str, str, str, bytes], Optional[AnalysisResult]]
_ANALYZERS: list = []


def register(analyzer: Analyzer) -> None:
    _ANALYZERS.append(analyzer)


def analyze(expected_type: str, filename: str, content_type: str, data: bytes) -> AnalysisResult:
    """Run the registered analyzers in order; the first one that handles the document wins."""
    for analyzer in _ANALYZERS:
        try:
            result = analyzer(expected_type, filename, content_type, data)
        except Exception as e:   # a failing analyzer must never lose the upload
            return AnalysisResult(status="failed", processor=getattr(analyzer, "__name__", "analyzer"),
                                  note=f"Automatic review failed ({type(e).__name__}).")
        if result is not None:
            return result
    return AnalysisResult(status="unavailable", note="Automatic document review isn't set up for this clinic.")


def available() -> bool:
    return any(getattr(a, "is_available", lambda: True)() for a in _ANALYZERS)


# ──────────────────────────────────────────────────────────────────────────
#  Claude
# ──────────────────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-opus-5-5"
_CLAUDE_MEDIA = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
_CLAUDE_MAX_IMAGE = 5 * 1024 * 1024   # the API's per-image limit

_SYSTEM = """You inspect onboarding documents that clinic staff upload (licenses, certification cards, training certificates, \
IDs and similar) and report exactly what you observe. A separate rules engine decides what to do with your report, so describe; \
don't judge whether the document is acceptable.

Classify the document from its own content, not from what the clinic asked for. Then extract the fields for the type you \
detected, using only these keys for that type:
{schemas}

For every field:
- value: what the document says, normalized. Write dates as YYYY-MM-DD only when the date is unambiguous; if the order of \
day and month can't be told, or any digit is unclear, leave value empty and put the printed text in raw_text. Never infer a \
value that isn't printed, and never fill in a field from what you'd expect.
- confidence: how sure you are that value is exactly what's printed. Use below 0.9 for anything partly obscured, blurry, \
handwritten or otherwise uncertain.
- page and location: where you read it.
- other_values_seen: any different value for the same field elsewhere in the document (another page, a second date, a \
different number). Leave empty when there's only one.
Include a field with an empty value when the document type normally has it but you can't find or read it.

Quality: list anything that stops information being read with confidence - blur, glare, cut-off edges, obstructions, low \
resolution, missing pages or sides, a damaged file.

Anomalies: note anything about the document itself that a reviewer should look at - visual or layout inconsistencies, text \
that looks altered or set in a different font from its surroundings, cropping around key information, a date that looks \
modified, pages that don't belong together, content that doesn't add up. Describe what you see in neutral terms; you cannot \
determine intent, so never state or imply that anyone committed fraud. Report nothing when nothing stands out.

List every person named as the holder on each page in pages[].holder_names."""


def _schema_text() -> str:
    return "\n".join(f"- {t}: " + ", ".join(k for k, _ in FIELD_SCHEMAS[t]) for t in FIELD_SCHEMAS)


def claude_analyzer(expected_type: str, filename: str, content_type: str, data: bytes) -> Optional[AnalysisResult]:
    if not claude_analyzer.is_available():
        return None
    processor = f"claude:{CLAUDE_MODEL}"
    if content_type not in _CLAUDE_MEDIA:
        return AnalysisResult(status="failed", processor=processor, note="UNSUPPORTED_FOR_ANALYSIS")
    if content_type != "application/pdf" and len(data) > _CLAUDE_MAX_IMAGE:
        return AnalysisResult(status="failed", processor=processor, note="IMAGE_TOO_LARGE_FOR_ANALYSIS")
    import anthropic

    b64 = base64.standard_b64encode(data).decode("ascii")
    block = ({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
             if content_type == "application/pdf" else
             {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": b64}})
    ask = (f"The clinic asked this person for: {DOC_TYPES.get(expected_type, expected_type)}. "
           "Classify what this document actually is, then report on it.")
    client = anthropic.Anthropic(timeout=180.0, max_retries=2)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)   # output_format is the SDK's documented Pydantic path
        response = client.beta.messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            system=_SYSTEM.format(schemas=_schema_text()),
            messages=[{"role": "user", "content": [block, {"type": "text", "text": ask}]}],
            output_config={"effort": "high"},             # careful inspection; Claude Opus 5.5 defaults to medium
            output_format=DocumentAnalysis,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},          # on a safety decline, retry on Anthropic's recommended model
        )
    if response.stop_reason == "refusal":
        return AnalysisResult(status="failed", processor=processor, note="MODEL_DECLINED")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        return AnalysisResult(status="failed", processor=processor, note="INCOMPLETE_ANALYSIS")
    return AnalysisResult(status="analyzed", analysis=response.parsed_output, processor=f"claude:{response.model}")


claude_analyzer.is_available = lambda: bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
register(claude_analyzer)
