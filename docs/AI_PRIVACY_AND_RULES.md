# How Althais uses AI on claims: identity stays out, rules check everything

```
visit note + header ──► deid.py ──► Gemini (Vertex, under Google's BAA): suggests codes, modifiers, note additions
   (full, local)        removes name, DOB (keeps age), dates (keeps year), phone, email, address, city, ZIP, SSN,
                        MRN, member/group/policy/account numbers, NPI, URLs, IPs
        │
        └──────────────► deterministic rules on the full note, in Althais's own code (no model, same input = same output):
                         services.py (procedures, tests and modifier 25 with minor procedures), _em_floor, ICD-10-CM
                         validity (icd10.py), NCCI pair edits, time-based codes (coding_rules.py), and claim_rules.py:
                         QW, VACCINE, NEW-EST, TIME, LACERATION, EXT-CAUSE, INJURY-7TH, DUPLICATE
        │
        ▼
claim + audit trail: every rule's change or warning names the rule and the reference entry that explains it
(rag_corpus.py), e.g. "[QW] Added modifier QW to 87880 ... (ref: mod-qw)"
```

## Where de-identification applies

- Claim building (`/api/claim-auto`, `/api/claim-build`), code suggestions (`/api/code-note`), the claim check
  (`/api/validate-claim`), and appeal letters (the model writes around `[PATIENT]` and `[MRN]`; the real values go
  in afterwards).
- Not the scribe: turning a recorded visit into a note needs the recording itself.
- `DEID_NOTES=0` turns it off. What was removed is returned as `privacy` with each claim.

It is minimum-necessary de-identification on top of the BAA, not a certified Safe Harbor guarantee: a name that
appears only inside free text, never in a labeled field or after Mr./Mrs./Ms., can still get through.

## Rules: what changes, what's only flagged

Changed automatically (mechanical): QW on CLIA-waived tests for Medicare and Medicaid; external cause codes moved
after the injury. Everything else is a warning for the biller on the claim check, never a silent change.

Tests: `tests/test_privacy_rules.py` (no identifier in anything sent to the model; each rule; determinism; every rule
cites a real reference entry).
