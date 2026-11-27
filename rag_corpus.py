"""The reference knowledge Althais's AI is grounded in (policy_knowledge.py searches it).

Written in our own words from public guidance: CMS manuals and the NCCI policy manual (U.S. government works), the
ICD-10-CM Official Guidelines, the X12 claim and payment formats, and Texas law. No AMA CPT descriptor text and no
commercial payer's policy text is copied here: CPT codes appear only as numbers, described in our own words.

Each entry: id (stable, cited in prompts and answers), topic, text (a few sentences: enough to ground an answer, not a
manual), source (where the guidance comes from, for the reader to check), tags (extra search words that the text doesn't
use, such as code numbers and abbreviations). Payer rules change: entries say "check the payer" where they vary.
"""

CORPUS = [
    # ── E/M visits ──
    {"id": "em-time", "topic": "E/M office visits: choosing the level by time",
     "text": "Office and outpatient E/M visits (99202-99215) can be leveled by the total time the clinician spent on the patient that day, "
             "including reviewing records, ordering tests, counseling and documenting, not only face-to-face time. The note must state the "
             "total minutes. Minimum times: new patient 99202 15, 99203 30, 99204 45, 99205 60; established 99212 10, 99213 20, 99214 30, "
             "99215 40. Time spent on a separately billed procedure does not count toward the visit's time.",
     "source": "AMA 2021+ office E/M guidelines, adopted by CMS", "tags": "minutes total time 99213 99214 99215 leveling"},
    {"id": "em-mdm", "topic": "E/M office visits: choosing the level by medical decision making (MDM)",
     "text": "MDM has three elements: the number and complexity of problems addressed, the data reviewed or ordered, and the risk of the "
             "management plan. The level is set by two of the three, not an average. Low (99213): one acute uncomplicated illness or injury, "
             "or one stable chronic illness, with low risk such as over-the-counter drugs. Moderate (99214): an acute illness with systemic "
             "symptoms, a chronic illness getting worse, or two stable chronic illnesses; prescription drug management is moderate risk. "
             "High (99215): a severe exacerbation or threat to life, or a decision about hospitalization.",
     "source": "AMA 2021+ office E/M guidelines, adopted by CMS", "tags": "medical decision making level 99212 99213 99214 99215 problems data risk prescription"},
    {"id": "em-new-established", "topic": "New vs established patient",
     "text": "A patient is new if they have not received any professional face-to-face service from the same physician, or another physician "
             "of the same specialty and subspecialty in the same group, in the past three years. Otherwise they are established. Billing a "
             "new-patient code (99202-99205) for an established patient is a frequent denial and audit finding.",
     "source": "CMS Medicare Claims Processing Manual, E/M section", "tags": "three years 3-year rule new patient established 99203 99204"},
    {"id": "em-prolonged", "topic": "Prolonged office visits",
     "text": "When total time goes well beyond the highest office visit level, an add-on code for prolonged service can be billed. Most "
             "commercial payers follow the AMA add-on (99417) in 15-minute units; Medicare uses its own HCPCS code (G2212) with a later "
             "start time. Check which one the payer accepts before billing either.",
     "source": "CMS Physician Fee Schedule rules; AMA guidelines", "tags": "99417 G2212 prolonged service long visit"},
    {"id": "em-critical-care", "topic": "Critical care time (99291/99292)",
     "text": "99291 covers the first 30 to 74 minutes of critical care on a date and 99292 each additional 30 minutes. Under 30 minutes is a "
             "regular E/M visit. The note must give total minutes spent exclusively on that patient, not only start and end times.",
     "source": "CMS Medicare Claims Processing Manual, critical care", "tags": "critical care 99291 99292 emergency"},
    {"id": "em-after-hours", "topic": "After-hours and urgent care visit codes",
     "text": "Add-on codes for services after regular hours or on weekends and holidays (99050, 99051) are paid only by some commercial plans "
             "and not by Medicare. Some commercial payers contract urgent care centers on a per-visit global code (S9083) or an urgent "
             "care add-on (S9088) instead of E/M codes. Bill these only when the clinic's contract with that payer says so.",
     "source": "Payer contracts; CMS (not payable by Medicare)", "tags": "99050 99051 S9083 S9088 urgent care global fee weekend evening"},

    # ── Modifiers ──
    {"id": "mod-25", "topic": "Modifier 25: a separate E/M on the same day as a procedure",
     "text": "Modifier 25 goes on the E/M code when a significant, separately identifiable evaluation was done on the same day as a "
             "procedure or other service. The note must show work beyond the usual check before and after the procedure, such as a "
             "separate problem that was evaluated and treated. The diagnosis can be the same. A visit that was only the procedure gets no E/M. "
             "Missing 25 is a common reason for a bundling denial (CO-97) on the visit.",
     "source": "CMS NCCI Policy Manual, chapter on E/M and minor procedures", "tags": "modifier 25 E/M same day procedure separately identifiable laceration injection bundled"},
    {"id": "mod-59-x", "topic": "Modifier 59 and the X modifiers (XE, XS, XP, XU)",
     "text": "Modifier 59 marks a procedure as distinct from another that would normally be bundled with it: a different session, site, organ "
             "system, incision or injury. CMS prefers the more specific X modifiers where they fit: XE separate encounter, XS separate "
             "structure, XP separate practitioner, XU unusual non-overlapping service. They can bypass an NCCI edit only when its "
             "modifier indicator is 1, and only when the note supports it. Overuse of 59 is a common audit target.",
     "source": "CMS NCCI Policy Manual; CMS MLN guidance on modifier 59", "tags": "59 XE XS XP XU distinct procedural service bypass unbundle"},
    {"id": "mod-76-77-91", "topic": "Repeat procedures and tests (modifiers 76, 77, 91)",
     "text": "Modifier 76 marks a procedure repeated on the same day by the same clinician, 77 by a different clinician, and 91 a repeat "
             "clinical lab test needed for a new result (not a retest because of an error). Without them, the second line is usually "
             "denied as a duplicate. Repeated nebulizer treatments on one visit are a common case.",
     "source": "CMS Medicare Claims Processing Manual", "tags": "76 77 91 repeat duplicate same day nebulizer"},
    {"id": "mod-qw", "topic": "CLIA-waived tests and modifier QW",
     "text": "Simple point-of-care tests the FDA classifies as waived (such as many rapid strep, flu, COVID, pregnancy and urine dipstick "
             "tests) can be run under a CLIA certificate of waiver. Medicare and many other payers require modifier QW on those test lines "
             "and the clinic's CLIA number on the claim. A missing CLIA number or QW is a frequent denial for urgent care labs.",
     "source": "CMS CLIA program; Medicare Claims Processing Manual, lab chapter", "tags": "QW CLIA waived rapid strep 87880 flu covid point of care lab test certificate"},
    {"id": "mod-26-tc", "topic": "X-rays: professional and technical parts (modifiers 26 and TC)",
     "text": "An imaging code covers two parts: taking the image (technical, modifier TC) and reading it (professional, modifier 26). A clinic "
             "that owns the machine and whose clinician reads the image bills the code with no modifier (global). If a radiologist outside "
             "the clinic reads it, the clinic bills TC and the radiologist bills 26. X-ray codes are chosen by body part and number of views.",
     "source": "CMS Medicare Claims Processing Manual, radiology", "tags": "26 TC x-ray radiology imaging read interpretation views global"},
    {"id": "mod-abn", "topic": "ABN and modifiers GA, GZ, GY (Medicare)",
     "text": "Before giving a Medicare patient a service the clinic expects Medicare to deny as not medically necessary, the clinic must give "
             "the patient an Advance Beneficiary Notice (ABN) to be able to bill them. GA means a signed ABN is on file, GZ means a denial "
             "is expected and no ABN was signed (the clinic can't bill the patient), and GY means the service is excluded by law from "
             "Medicare coverage.",
     "source": "CMS Medicare Claims Processing Manual, ABN chapter", "tags": "ABN advance beneficiary notice GA GZ GY not covered medicare patient liability"},
    {"id": "mod-telehealth", "topic": "Telehealth claims",
     "text": "Telehealth visits use place of service 02 or 10 (patient at home) and, for many commercial payers, modifier 95 for audio-video "
             "or 93 for audio-only. Medicare's telehealth rules have changed several times since 2020, so check the current Medicare and "
             "payer policy for the date of service before billing.",
     "source": "CMS telehealth guidance; payer policies", "tags": "telehealth telemedicine virtual video 95 93 POS 02 10"},

    # ── NCCI and units ──
    {"id": "ncci-ptp", "topic": "NCCI procedure-to-procedure (PTP) edits",
     "text": "An NCCI PTP edit is a pair of codes that shouldn't be paid together for the same patient and date because one is part of "
             "the other. The column 2 code is denied unless a modifier applies. The modifier indicator says whether one can: 0 means never, "
             "1 means an appropriate modifier (25, 59, X modifiers, sometimes anatomic modifiers) can bypass it when the note supports it, "
             "9 means the edit no longer applies. CMS updates the tables every quarter; many commercial payers use them too.",
     "source": "CMS National Correct Coding Initiative (NCCI) policy manual and tables", "tags": "NCCI PTP bundling column 1 column 2 modifier indicator 0 1 9 edit pair"},
    {"id": "ncci-mue", "topic": "Medically unlikely edits (MUEs): unit limits",
     "text": "An MUE is the most units of a code one patient would normally get on one date. Units above it are denied. Some MUEs are "
             "per line, others per day, and the per-day clinical ones can be appealed with documentation. Drug units follow the dose in the "
             "HCPCS code's descriptor, so a 1 g dose of a drug coded per 250 mg is 4 units.",
     "source": "CMS NCCI MUE tables", "tags": "MUE units limit per day medically unlikely edit drug units dosage"},
    {"id": "minor-procedure-global", "topic": "Global periods and minor procedures",
     "text": "Each surgical procedure has a global period of 0, 10 or 90 days. For minor procedures (0 or 10 days, such as most laceration "
             "repairs, incision and drainage, and foreign body removal), the decision to do the procedure and the usual care that day are "
             "included in it. A separate E/M that day needs modifier 25. Within the global period, an unrelated visit uses modifier 24 and "
             "an unrelated procedure modifier 79; modifier 57 marks the decision for a major surgery.",
     "source": "CMS Medicare Claims Processing Manual, global surgery", "tags": "global period 0 10 90 day minor procedure 24 57 79 laceration I&D postoperative"},

    # ── Common urgent care services ──
    {"id": "svc-laceration", "topic": "Laceration repairs",
     "text": "Repairs are coded by type (simple, intermediate with layered closure or heavy cleaning, or complex), by body area group, and by "
             "total length in centimeters. Lengths of repairs of the same type in the same body area group are added together and billed "
             "as one code. Simple repairs of the scalp, neck, armpits, trunk, arms, legs, hands and feet are 12001-12007; the face, ears, "
             "eyelids, nose and lips are 12011-12018; intermediate repairs start at 12031. Local anesthesia is included.",
     "source": "CMS NCCI Policy Manual, integumentary chapter (in our words)", "tags": "laceration repair sutures stitches 12001 12002 12004 12011 12031 length cm simple intermediate"},
    {"id": "svc-injections", "topic": "Injections and drug codes",
     "text": "A therapeutic injection under the skin or into a muscle (96372) is billed with the drug's own HCPCS code (usually a J-code) and "
             "the units its descriptor sets. Medicaid and some payers also require the drug's 11-digit NDC with its quantity on the line. "
             "Vaccines are billed differently (see vaccines).",
     "source": "CMS Medicare Claims Processing Manual; Texas Medicaid (TMHP) NDC requirement", "tags": "96372 injection IM SC J-code J1885 J3301 ceftriaxone J0696 NDC units drug"},
    {"id": "svc-vaccines", "topic": "Vaccines: product and administration codes",
     "text": "Bill the vaccine product code and an administration code. For most payers the first administration is 90471 and each additional "
             "one 90472; for patients under 19 with clinician counseling, 90460 and 90461 apply. Medicare uses its own administration codes "
             "for flu, pneumonia, hepatitis B and COVID vaccines (G0008, G0009, G0010 and the COVID administration codes). Add Z23 as the "
             "diagnosis.",
     "source": "CMS Medicare Claims Processing Manual, vaccines; ICD-10-CM guidelines", "tags": "vaccine immunization Tdap flu shot 90471 90472 90460 90715 G0008 Z23 tetanus"},
    {"id": "svc-nebulizer-ecg", "topic": "Nebulizer treatments and ECGs",
     "text": "A nebulizer treatment (94640) is billed per treatment with the inhaled drug's HCPCS code and units (for example albuterol); a "
             "second treatment the same visit takes modifier 76. An ECG with tracing and interpretation by the clinic is 93000; tracing only "
             "is 93005 and interpretation only is 93010.",
     "source": "CMS Medicare Claims Processing Manual", "tags": "nebulizer neb breathing treatment 94640 albuterol J7613 ECG EKG 93000 93005 93010"},
    {"id": "svc-labs", "topic": "Point-of-care labs and venipuncture",
     "text": "Only tests run in the clinic are billed by the clinic; a sample sent to an outside lab is billed by the lab. A single test that "
             "detects several targets at once (for example flu A, flu B and COVID together) uses the combined code, not one code per target. "
             "Drawing blood (36415) is paid separately by Medicare but bundled by some commercial payers.",
     "source": "CMS lab billing guidance; payer policies", "tags": "lab test in-house outside reference lab venipuncture 36415 multiplex flu covid combined"},
    {"id": "svc-workers-comp", "topic": "Workers' compensation and occupational medicine",
     "text": "A work injury is billed to the employer's workers' compensation carrier, not the patient's health plan. In Texas, the Division "
             "of Workers' Compensation sets its own fee guideline and forms, and the health plan will deny the claim as work-related. Ask "
             "every injured patient whether it happened at work.",
     "source": "Texas Department of Insurance, Division of Workers' Compensation", "tags": "workers comp work injury occupational employer DWC Texas"},

    # ── Diagnosis coding ──
    {"id": "dx-specificity", "topic": "ICD-10-CM: specificity, laterality and 7th characters",
     "text": "Code to the highest specificity the note supports, including laterality (right, left, both) where the code has it. Injury "
             "codes need a 7th character: A for the initial (active) treatment, D for later healing visits, S for a sequela. Codes that "
             "aren't billable because a more specific code exists (a category or subcategory) are rejected.",
     "source": "ICD-10-CM Official Guidelines for Coding and Reporting", "tags": "ICD-10 specificity laterality 7th character A D S initial encounter injury billable"},
    {"id": "dx-outpatient", "topic": "ICD-10-CM: outpatient rules (symptoms, rule-outs, order)",
     "text": "In outpatient care, don't code a diagnosis described as probable, suspected or rule-out: code the signs and symptoms instead. "
             "List first the main reason for the visit. Code chronic conditions only when they affect the care given that day. External "
             "cause codes (how an injury happened, such as W26.0 for a knife) are never listed first.",
     "source": "ICD-10-CM Official Guidelines, Section IV (outpatient)", "tags": "rule out suspected probable symptoms first-listed external cause W codes sequencing"},
    {"id": "dx-combination", "topic": "ICD-10-CM: combination codes",
     "text": "Some conditions must be coded together as one combination code: hypertension with chronic kidney disease (I12.x), with heart "
             "disease (I11.x) or both (I13.x); diabetes with complications (E11.2x for kidney, E11.4x for nerves, and so on). Coding them "
             "separately is an error that payers' edits catch.",
     "source": "ICD-10-CM Official Guidelines", "tags": "combination code hypertension CKD I12 I11 diabetes complications E11.22"},
    {"id": "dx-pointers", "topic": "Diagnosis pointers on the claim",
     "text": "A professional claim holds up to 12 diagnoses, and each service line points to up to 4 of them, the first being the main reason "
             "for that service. A line pointing to a diagnosis that doesn't support it (a strep test pointing to a laceration) is a common "
             "reason for a medical-necessity denial even when the right diagnosis is on the claim.",
     "source": "X12 837P implementation guide; CMS-1500 instructions", "tags": "diagnosis pointer linking CMS-1500 box 24E 837P medical necessity"},

    # ── Claims and the files ──
    {"id": "claim-837p", "topic": "The 837P professional claim file",
     "text": "The 837P is the electronic version of the CMS-1500. It carries the billing provider (group NPI and tax ID), the subscriber and "
             "patient, the claim (total charge, place of service, frequency code, diagnoses) and service lines (code, modifiers, charge, "
             "units, diagnosis pointers, rendering provider). The clearinghouse checks the format before sending it to the payer.",
     "source": "X12 005010X222A1 (837P) implementation guide", "tags": "837 837P claim file EDI X12 CMS-1500 electronic loops"},
    {"id": "claim-corrected", "topic": "Corrected and voided claims (frequency codes 7 and 8)",
     "text": "To fix a claim the payer already processed, send a replacement claim with frequency code 7 and the payer's original claim number; "
             "to cancel one, send frequency code 8. Sending the fixed claim as a brand-new claim usually gets it denied as a duplicate "
             "(CO-18). Some payers ask for a corrected claim instead of an appeal for coding fixes such as a missing modifier.",
     "source": "X12 837P implementation guide (CLM05-3, REF*F8); payer manuals", "tags": "corrected claim replacement frequency code 7 void 8 resubmit original claim number"},
    {"id": "claim-npi", "topic": "Billing vs rendering NPI and enrollment",
     "text": "The billing provider is usually the clinic's group (Type 2) NPI; each line's rendering provider is the clinician's individual "
             "(Type 1) NPI. Each must be enrolled and credentialed with the payer for the date of service. A clinician not yet enrolled, a "
             "wrong taxonomy or a group/individual mix-up produces denials such as CO-B7 or rejections with remark N290.",
     "source": "CMS NPI and PECOS enrollment guidance", "tags": "NPI type 1 type 2 rendering billing enrollment credentialing PECOS taxonomy B7 N290"},
    {"id": "claim-pos", "topic": "Place of service codes",
     "text": "Place of service tells the payer where the visit happened and changes the rate: 11 office, 20 urgent care facility, 22 hospital "
             "outpatient, 23 emergency room, 02 and 10 telehealth. An urgent care should bill the place of service its payer contracts "
             "expect; a mismatch causes underpayments or denials.",
     "source": "CMS Place of Service code set", "tags": "POS place of service 11 20 22 23 office urgent care facility rate"},
    {"id": "claim-835", "topic": "The 835 payment file (ERA)",
     "text": "The 835 is the payer's electronic explanation of payment. Each claim (CLP) shows the status, charge and payment; each service "
             "line (SVC) shows what was billed, allowed and paid; adjustments (CAS) give a group code and a reason code (CARC) with the "
             "amount, plus remark codes (RARC). Provider-level adjustments (PLB) are money taken back or added outside any one claim, such "
             "as recoupments of earlier overpayments.",
     "source": "X12 005010X221A1 (835) implementation guide", "tags": "835 ERA remittance EOB CLP SVC CAS PLB payment file recoupment"},

    # ── Denials and adjustments ──
    {"id": "adj-groups", "topic": "Adjustment group codes (CO, PR, OA, PI, CR)",
     "text": "Every adjustment on a payment carries a group code. CO (contractual obligation) is the clinic's write-off or a denial the clinic "
             "can't bill the patient for. PR (patient responsibility) is the deductible (PR-1), coinsurance (PR-2) or copay (PR-3) to "
             "collect from the patient; these are not denials. OA is other, PI payer-initiated, CR a correction or reversal.",
     "source": "X12 Claim Adjustment Group Codes", "tags": "CO PR OA PI CR group code deductible coinsurance copay patient responsibility write-off"},
    {"id": "carc-97", "topic": "Denial CO-97: bundled into another service",
     "text": "CO-97 means the payer considers the service included in another one paid on the same date. Check the NCCI pair: if the "
             "modifier indicator is 1 and the note shows the services were separate, resubmit as a corrected claim with the right modifier "
             "(25 on a separate E/M, 59 or an X modifier on a distinct procedure). If the indicator is 0, or the note doesn't support it, "
             "the denial stands.",
     "source": "X12 CARC 97; CMS NCCI", "tags": "CO-97 97 bundled included denial modifier 25 59 corrected claim"},
    {"id": "carc-16", "topic": "Denial CO-16: missing or invalid information",
     "text": "CO-16 means the claim lacks information the payer needs; the remark code (RARC) on the same line says what. Common causes: "
             "missing CLIA number, missing NDC, invalid subscriber ID, missing referring provider. Fix the field and send a corrected "
             "claim; this isn't an appeal.",
     "source": "X12 CARC 16 with RARC", "tags": "CO-16 16 missing information remark code RARC CLIA NDC member ID"},
    {"id": "carc-18", "topic": "Denial CO-18: duplicate claim",
     "text": "CO-18 means the payer already has this claim or line. Before resubmitting anything, check the original claim's status. If the "
             "original was paid or denied and something needs to change, send a corrected claim (frequency code 7) instead of a new one.",
     "source": "X12 CARC 18", "tags": "CO-18 18 duplicate claim resubmission status"},
    {"id": "carc-29", "topic": "Denial CO-29: filed after the deadline",
     "text": "CO-29 means the claim arrived after the payer's filing limit. It can be overturned only with proof the claim was sent on time "
             "(a clearinghouse acceptance report) or a reason the payer accepts. The fix is prevention: send claims within days and watch "
             "claims that never get an answer well before the limit.",
     "source": "X12 CARC 29", "tags": "CO-29 29 timely filing late deadline proof acceptance report"},
    {"id": "carc-50", "topic": "Denial CO-50: not medically necessary",
     "text": "CO-50 means the payer decided the service wasn't necessary for the diagnoses on the claim. First check the diagnosis pointers "
             "and whether a more specific diagnosis in the note supports the service; if so, send a corrected claim. Otherwise appeal with "
             "the note and the payer's own coverage policy. For Medicare, check the local coverage determination (LCD) for that service.",
     "source": "X12 CARC 50; CMS coverage determinations", "tags": "CO-50 50 medical necessity LCD NCD diagnosis not supported"},
    {"id": "carc-4-11", "topic": "Denials CO-4 and CO-11: modifier or diagnosis doesn't fit the code",
     "text": "CO-4 means a modifier is missing or doesn't fit the procedure (for example QW missing on a waived test, or a laterality "
             "modifier missing). CO-11 means the diagnosis doesn't fit the procedure. Both are coding fixes sent as corrected claims, "
             "not appeals.",
     "source": "X12 CARC 4 and 11", "tags": "CO-4 CO-11 modifier inconsistent diagnosis inconsistent procedure corrected claim"},
    {"id": "carc-197", "topic": "Denials CO-197 and CO-15: missing prior authorization",
     "text": "CO-197 (and CO-15) mean the service needed an authorization that wasn't on the claim. If one was obtained, add its number and "
             "send a corrected claim. If not, some payers accept a retroactive authorization request for urgent services within a short "
             "window. Advanced imaging and some drugs and procedures commonly need authorization; routine urgent care visits usually don't.",
     "source": "X12 CARC 197 and 15; payer policies", "tags": "CO-197 197 CO-15 prior authorization precertification retro auth"},
    {"id": "carc-22-27", "topic": "Denials CO-22 and CO-27: other insurance or no coverage",
     "text": "CO-22 means another plan should pay first (coordination of benefits): bill the primary plan, then this one with the primary's "
             "payment. For a child on two parents' plans, the parent whose birthday comes first in the year is usually primary. CO-27 means "
             "coverage had ended on the date of service: verify eligibility and bill the right plan or the patient.",
     "source": "X12 CARC 22 and 27; NAIC coordination of benefits rules", "tags": "CO-22 COB coordination of benefits primary secondary birthday rule CO-27 eligibility terminated"},
    {"id": "carc-45", "topic": "CO-45: charge above the fee schedule (not a denial)",
     "text": "CO-45 is the normal write-off of the difference between the clinic's charge and the payer's allowed amount. It isn't a denial. "
             "It becomes a problem only when the allowed amount is below the clinic's contracted rate: then it's an underpayment to dispute.",
     "source": "X12 CARC 45", "tags": "CO-45 45 contractual adjustment fee schedule allowed amount write-off underpayment"},

    # ── Getting paid ──
    {"id": "pay-underpayment", "topic": "Finding and disputing underpayments",
     "text": "Compare each line's allowed amount with the rate in the clinic's contract for that payer, code and modifiers. A payment below "
             "the contract is disputed with the payer's provider dispute or reconsideration process, quoting the contract rate. Check "
             "first for legitimate reductions: multiple procedure reductions, modifier-based pricing, and patient responsibility.",
     "source": "Payer contracts; general revenue cycle practice", "tags": "underpayment underpaid contract rate fee schedule dispute reconsideration allowed"},
    {"id": "pay-recoupment", "topic": "Recoupments and offsets",
     "text": "A payer that decides it overpaid can take the money back by reducing a later payment; the 835 shows it as a provider-level "
             "adjustment (PLB), often with reason WO, against the original claim number. Check each takeback against the original claim: "
             "payers can be wrong about the overpayment, and the clinic can dispute it.",
     "source": "X12 835 PLB adjustments; payer manuals", "tags": "recoupment takeback offset overpayment PLB WO clawback"},
    {"id": "law-tx-prompt-pay", "topic": "Texas prompt pay law",
     "text": "For PPO and HMO plans regulated by the Texas Department of Insurance, a clean claim must be paid or denied within 30 days if "
             "sent electronically and 45 days on paper, with penalties for late payment. Self-funded employer plans (ERISA) and Medicare "
             "aren't covered by it; check the back of the member's card.",
     "source": "Texas Insurance Code chapters 843 and 1301", "tags": "Texas prompt pay clean claim 30 days 45 days TDI penalty HMO PPO"},

    # ── Filing limits and appeals ──
    {"id": "file-limits", "topic": "Timely filing limits",
     "text": "Medicare: 12 months from the date of service. Texas Medicaid (TMHP): generally 95 days from the date of service. Medicare "
             "Advantage and commercial plans: whatever the contract says, often 90 to 180 days. Always use the payer's actual limit, and "
             "keep the clearinghouse acceptance report as proof the claim was sent on time.",
     "source": "CMS; Texas Medicaid Provider Procedures Manual; payer contracts", "tags": "timely filing limit 365 days 12 months 95 days Medicaid TMHP Medicare Advantage commercial"},
    {"id": "appeal-medicare", "topic": "Medicare appeals: the five levels",
     "text": "1) Redetermination by the Medicare contractor, within 120 days of the initial decision. 2) Reconsideration by a Qualified "
             "Independent Contractor, within 180 days. 3) Administrative Law Judge, within 60 days, above a minimum amount in dispute. "
             "4) Medicare Appeals Council. 5) Federal court. Simple clerical errors can instead be fixed by a reopening, usually within "
             "a year.",
     "source": "CMS Original Medicare appeals process", "tags": "Medicare appeal redetermination reconsideration QIC ALJ 120 days 180 days reopening"},
    {"id": "appeal-commercial", "topic": "Commercial and Medicare Advantage appeals",
     "text": "Appeal deadlines and levels are set by the payer's provider manual and the clinic's contract, commonly 60 to 180 days from the "
             "payment or denial. Many payers have a first-level reconsideration before a formal appeal, and some require a corrected claim "
             "instead for coding fixes. Medicare Advantage plans run their own appeals under CMS rules.",
     "source": "Payer provider manuals; CMS Medicare Advantage rules", "tags": "commercial appeal Medicare Advantage reconsideration provider dispute deadline 60 90 120 180"},
    {"id": "appeal-letter", "topic": "Writing an effective appeal letter",
     "text": "State the claim number, patient, date of service, codes and the exact denial reason code. Answer that reason directly: quote "
             "the note where it shows the separate problem, the medical necessity or the correct code, and cite the rule (NCCI, the "
             "payer's own policy, the contract rate). Attach the note and any supporting records. Ask for a specific outcome: reprocess "
             "and pay the line. Generic letters that don't address the reason code rarely succeed.",
     "source": "General revenue cycle practice", "tags": "appeal letter reconsideration request documentation argument attach records"},
    {"id": "appeal-records", "topic": "Medical record requests (ADR)",
     "text": "When a payer asks for records before paying (an additional documentation request), send exactly what was asked, by the "
             "deadline (often 30 to 45 days); a missed deadline becomes a denial. Include the signed note for that date and anything it "
             "refers to, such as test results.",
     "source": "CMS ADR guidance; payer policies", "tags": "ADR additional documentation request records request medical records deadline"},

    # ── Other rules ──
    {"id": "rule-incident-to", "topic": "Incident-to billing (Medicare)",
     "text": "Medicare lets a nurse practitioner's or physician assistant's service be billed under a physician's NPI (at the full rate) "
             "only in an office setting, for an established problem the physician started treating, with the physician in the office suite "
             "during the visit. New problems and new patients are billed under the NP's or PA's own NPI at 85% of the rate.",
     "source": "CMS Medicare Benefit Policy Manual, incident-to", "tags": "incident to NP PA nurse practitioner physician assistant supervision 85%"},
    {"id": "rule-medicare-advantage", "topic": "Medicare Advantage vs Original Medicare",
     "text": "Medicare Advantage plans (sold by insurers such as UnitedHealthcare, Humana and Aetna) are billed to the insurer, not to the "
             "Medicare contractor, and follow their own authorization rules, filing limits and appeal steps. Check the card: a red, white "
             "and blue Medicare card means Original Medicare.",
     "source": "CMS Medicare Advantage guidance", "tags": "Medicare Advantage MA Part C Original Medicare card insurer"},
    {"id": "rule-gfe", "topic": "Good faith estimates for self-pay patients",
     "text": "Under the No Surprises Act, uninsured and self-pay patients are entitled to a good faith estimate of expected charges when they "
             "ask for one or schedule a service at least three business days ahead. A bill far above the estimate (by $400 or more) can be "
             "disputed by the patient.",
     "source": "No Surprises Act; CMS guidance", "tags": "good faith estimate self-pay uninsured No Surprises Act price"},
    {"id": "form-cms1500", "topic": "CMS-1500 vs UB-04",
     "text": "Physician offices, urgent care centers and other non-institutional providers bill on the CMS-1500 (837P electronically). "
             "Hospitals and other institutions bill on the UB-04 (837I), with revenue codes and different rules. Freestanding emergency "
             "centers may bill either way depending on licensing and payer.",
     "source": "CMS claim form instructions", "tags": "CMS-1500 UB-04 837P 837I professional institutional form"},
]
