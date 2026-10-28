"""
records_network.py: getting a patient's outside records from a records network (lab results, hospital and specialist
documents), behind one interface so the vendor can change without touching the app.

Real networks (Health Gorilla, Kno2 and similar) reach Quest and Labcorp results, Direct secure messaging and the
national exchanges (Carequality, CommonWell, TEFCA). Each needs a contract and a BAA with Althais before any real patient
is queried, so the only connector built today is the mock, which answers for synthetic patients only and never leaves
Althais. Add a vendor by subclassing Network and registering it in _REGISTRY; pick it with RECORDS_NETWORK.

  RECORDS_NETWORK=""      off (the default): the app says the network isn't connected
  RECORDS_NETWORK=mock    synthetic records for synthetic patients (demos and tests)
"""
import os
from dataclasses import dataclass, field


class NetworkError(Exception):
    pass


@dataclass
class Found:
    """One document a network has for the patient."""
    ref: str                     # the network's id for it
    title: str
    doc_type: str                # lab | imaging | referral | records | other
    source: str                  # who sent it (lab, hospital, practice)
    date: str                    # YYYY-MM-DD
    mime: str = "application/pdf"
    data: bytes = b""
    extra: dict = field(default_factory=dict)


class Network:
    name = "none"
    label = "Not connected"

    def find(self, patient: dict) -> list:
        """Documents the network holds for this patient (name, dob, sex, member, zip)."""
        raise NetworkError("No records network is connected yet.")


def _synthetic(patient: dict) -> bool:
    return bool(patient.get("synthetic")) or str(patient.get("name", "")).upper().startswith(("TESTPATIENT", "TEST,", "SAMPLE"))


def _pdf(lines):
    from fpdf import FPDF
    pdf = FPDF(format="Letter")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, lines[0], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for ln in lines[1:]:
        pdf.multi_cell(0, 5.5, ln, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


class MockNetwork(Network):
    """Synthetic records for synthetic patients. Refuses anyone else, so a real patient can't be sent anywhere by mistake."""
    name, label = "mock", "Test network (synthetic records only)"

    def find(self, patient: dict) -> list:
        if not _synthetic(patient):
            raise NetworkError("The test network only answers for synthetic (test) patients.")
        nm, dob = str(patient.get("name") or ""), str(patient.get("dob") or "")
        lab = _pdf(["SAMPLE REFERENCE LABORATORY - SYNTHETIC RESULT", f"Patient: {nm}    DOB: {dob}", "Collected: 2026-10-20    Reported: 2026-10-21",
                    "Test                      Result     Units     Reference range", "Hemoglobin A1c            6.9        %         4.0 - 5.6   H",
                    "Glucose, fasting          131        mg/dL     70 - 99     H", "Creatinine                0.9        mg/dL     0.6 - 1.2",
                    "Specimen: whole blood, serum. This is a synthetic test report. No real patient."])
        img = _pdf(["SAMPLE IMAGING CENTER - SYNTHETIC REPORT", f"Patient: {nm}    DOB: {dob}", "Exam: X-ray chest, 2 views    Date: 2026-10-18",
                    "Findings: Lungs are clear. Heart size normal. No acute bony abnormality.", "Impression: No acute cardiopulmonary process.",
                    "Radiologist: Synthetic Reader MD. This is a synthetic test report. No real patient."])
        return [Found("mock-lab-1", "Hemoglobin A1c and chemistry", "lab", "Sample Reference Laboratory", "2026-10-21", data=lab),
                Found("mock-img-1", "Chest X-ray report", "imaging", "Sample Imaging Center", "2026-10-18", data=img)]


_REGISTRY = {"mock": MockNetwork}


def current() -> Network:
    name = (os.environ.get("RECORDS_NETWORK") or "").strip().lower()
    return _REGISTRY.get(name, Network)()


def status() -> dict:
    n = current()
    return {"connected": n.name != "none", "name": n.name, "label": n.label, "available": sorted(_REGISTRY)}
