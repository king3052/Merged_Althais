"""Claim.MD behind the dental EMR's vendor interfaces (dental_vendors.py): eligibility (270/271), claims and
predeterminations (837D), remittances (835) and attachments. Turn on with DENTAL_VENDOR_CLEARINGHOUSE=claimmd and
DENTAL_VENDOR_ATTACHMENTS=claimmd.

  eligibility     the 270 built by dental_x12 goes to /services/elig/; the answer is <eligibility data="ISA...IEA">,
                  the payer's 271, which dental_x12.parse_271 reads (remaining max, deductible, coinsurance, HSD limits)
  claims          the 837D goes to /services/upload/; the reply has the claim's status (A accepted, R rejected) and
                  Claim.MD's claim ID
  remittances     /services/eralist/ filtered to the clinic's NPI and Tax ID, then /services/era835/ for each ERA (the 835
                  is in <data>); posting is idempotent per check, so asking again is safe
  attachments     the claim carries one attachment control number in PWK06; after Claim.MD accepts the claim, the
                  documents go up as <ACN>.pdf (Claim.MD's automated PDF attachments)

Uses Althais's own Claim.MD account (connected in /admin, clearinghouse.PLATFORM). Every interchange is addressed to
CLAIMMD and marked production ("P"), or test ("T") with CLAIMMD_TEST=1. Synthetic patients (member IDs starting SYN)
are refused before anything leaves Althais. Endpoint and field names follow Claim.MD's published API reference
(https://api.claim.md); not yet exercised against a live account.
"""
import datetime as dt
import os
import re
import secrets

import clearinghouse as chx
import dental_vendors as v
import dental_x12 as x12


def _cmd() -> chx.ClaimMD:
    conn = chx.load_connection(chx.PLATFORM)
    if conn.get("partner") != "claimmd" or not conn.get("key"):
        raise v.VendorError("Althais's Claim.MD account isn't connected yet (Admin > Billing).")
    try:
        return chx.ClaimMD(chx.unseal(conn["key"]))
    except Exception:
        raise v.VendorError("Althais's Claim.MD key can't be read. Connect it again in Admin > Billing.")


def _production() -> bool:
    return os.environ.get("CLAIMMD_TEST", "").strip() not in ("1", "true", "yes")


def _synthetic(member_id) -> bool:
    return str(member_id or "").upper().startswith("SYN")


def _payer_id(cmd, payer, payer_id):
    if payer_id and payer_id.upper() != "MOCK":
        return payer_id
    pid, attrs = cmd.find_payer(payer)
    if not pid:
        raise v.VendorError(f"{attrs.get('matches')} Claim.MD payers match {payer}; add the payer ID to the patient's plan."
                            if attrs.get("matches") else f"Claim.MD has no payer named {payer}; add the payer ID to the patient's plan.")
    return pid


class ClaimMDClearinghouse(v.DentalClearinghouse):
    name = "claimmd"

    def eligibility(self, x270, req):
        if _synthetic(req.get("member_id")):
            raise v.VendorError("Synthetic patients are never sent to a real clearinghouse.")
        cmd = _cmd()
        pid = _payer_id(cmd, req.get("payer"), req.get("payer_id"))
        x = x12.for_receiver(x12.build_270(dict(req, payer_id=pid)), "CLAIMMD", "CLAIMMD", _production())
        try:
            code, text = cmd._post("elig/", files={"File": ("elig.270", x.encode(), "application/edi-x12")}, timeout=60)
        except Exception as e:
            raise v.VendorError(f"Couldn't reach Claim.MD ({type(e).__name__}).")
        root = cmd._xml(text)
        el = root.find(".//eligibility") if root is not None else None
        if code != 200 or el is None or not (el.get("data") or "").strip().startswith("ISA"):
            raise v.VendorError("Claim.MD didn't return the payer's answer: " + (cmd._error(root, text) or f"HTTP {code}"))
        return el.get("data").strip()

    def _upload(self, x837, claim, what):
        if _synthetic((claim.get("patient") or {}).get("member_id")):
            return v.VendorResult(False, "refused", "Synthetic patients are never sent to a real clearinghouse.", environment="claimmd")
        try:
            cmd = _cmd()
            pid = _payer_id(cmd, claim.get("payer"), claim.get("payer_id"))
        except v.VendorError as e:
            return v.VendorResult(False, "not_sent", str(e), environment="claimmd")
        x = x12.for_receiver(x837, "CLAIMMD", "CLAIMMD", _production(), payer_id=pid)
        errs = x12.validate_837d(x)
        if errs:
            return v.VendorResult(False, "rejected", "Not sent: " + "; ".join(errs[:3]), environment="claimmd", data={"errors": errs})
        name = re.sub(r"[^A-Za-z0-9_-]", "_", str(claim.get("id") or "claim")) + ".837d"
        try:
            code, text = cmd._post("upload/", {"Filename": name}, files={"File": (name, x.encode(), "application/edi-x12")}, timeout=60)
        except Exception as e:
            return v.VendorResult(False, "not_sent", f"Couldn't reach Claim.MD ({type(e).__name__}). Nothing was sent.", environment="claimmd")
        root = cmd._xml(text)
        cl = root.find(".//claim") if root is not None else None
        if code != 200 or cl is None:
            return v.VendorResult(False, "rejected", "Claim.MD didn't accept the file: " + (cmd._error(root, text) or f"HTTP {code}"), environment="claimmd")
        msgs = "; ".join((m.get("message") or "").strip() for m in cl.findall(".//message") if (m.get("message") or "").strip())
        ref = cl.get("claimmd_id") or cl.get("claimid") or ""
        if (cl.get("status") or "").upper() == "R":
            return v.VendorResult(False, "rejected", "Claim.MD rejected the " + what + (": " + msgs if msgs else "."), ref=ref, environment="claimmd")
        return v.VendorResult(True, "accepted" if what == "claim" else "submitted", f"{what.capitalize()} accepted by Claim.MD" + (f" ({msgs})" if msgs else "."),
                              ref=ref, environment="claimmd", data={"payer_id": pid})

    def submit_claim(self, x837, claim):
        return self._upload(x837, claim, "claim")

    def preauth(self, x837, claim):
        return self._upload(x837, claim, "predetermination")

    def preauth_status(self, ref):
        # Claim.MD returns the payer's predetermination decision as a claim response (and on paper/ERA for some payers);
        # until it arrives the request stays pending. Nothing here guesses an approved amount.
        try:
            rows, _ = _cmd().responses("0")
        except Exception:
            return v.VendorResult(True, "pending", "Waiting for the payer's decision", ref=ref, environment="claimmd")
        mine = [r for r in rows if r.get("claimmd_id") == ref]
        st = mine[-1]["status"] if mine else ""
        if st == "R":
            return v.VendorResult(True, "denied", "; ".join(m["message"] for m in mine[-1]["messages"]) or "Rejected", ref=ref, environment="claimmd")
        return v.VendorResult(True, "pending", "Waiting for the payer's decision", ref=ref, environment="claimmd")

    def fetch_remittances(self, since, npi="", tin=""):
        if not (npi or tin):
            raise v.VendorError("Add the office's NPI and Tax ID in Practice setup before fetching ERAs.")
        cmd = _cmd()
        start = dt.date.fromisoformat(since[:10]) if since else dt.date.today() - dt.timedelta(days=90)
        out, page = [], 1
        while page <= 20:
            fields = {"ReceivedAfterDate": start.strftime("%m-%d-%Y"), "Page": str(page)}
            if npi:
                fields["NPI"] = npi
            if tin:
                fields["TaxID"] = re.sub(r"\D", "", tin)
            code, text = cmd._post("eralist/", fields, timeout=60)
            root = cmd._xml(text)
            if code != 200 or root is None or root.find(".//error") is not None:
                raise v.VendorError("Claim.MD didn't list the ERAs: " + (cmd._error(root, text) or f"HTTP {code}"))
            eras = root.findall(".//era")
            for e in eras:
                # belt and braces: only this clinic's ERAs, even if a filter were ignored
                if (npi and e.get("prov_npi") and e.get("prov_npi") != npi) or (tin and e.get("prov_taxid") and re.sub(r"\D", "", e.get("prov_taxid")) != re.sub(r"\D", "", tin)):
                    continue
                c2, t2 = cmd._post("era835/", {"eraid": e.get("eraid")}, timeout=60)
                r2 = cmd._xml(t2)
                d = r2.find(".//data") if r2 is not None else None
                if c2 == 200 and d is not None and (d.text or "").strip().startswith("ISA"):
                    out.append(d.text.strip())
            if len(eras) < 100:
                break
            page += 1
        return out


class ClaimMDAttachments(v.AttachmentService):
    """Claim.MD automated PDF attachments: one control number per claim, one PDF named after it."""

    def send(self, claim_id, files):
        return self.prepare(claim_id, files)

    def prepare(self, claim_id, files):
        if not files:
            return v.VendorResult(False, "empty", "Nothing to attach", environment="claimmd")
        return v.VendorResult(True, "prepared", "Control number assigned; the documents go right after the claim", ref="A" + secrets.token_hex(7).upper(), environment="claimmd")

    def deliver(self, claim_id, files, ref, pdf):
        if not pdf:
            return v.VendorResult(False, "empty", "No documents to send", ref=ref, environment="claimmd")
        try:
            res = _cmd().send_attachment(ref, pdf)
        except v.VendorError as e:
            return v.VendorResult(False, "not_sent", str(e), ref=ref, environment="claimmd")
        return v.VendorResult(res.ok, "sent" if res.ok else "failed", res.message, ref=ref, environment="claimmd")


v._REGISTRY["clearinghouse"]["claimmd"] = ClaimMDClearinghouse
v._REGISTRY["attachments"]["claimmd"] = ClaimMDAttachments
