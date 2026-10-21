// Shared by the background worker (importScripts) and the side panel (<script>).
(function (g) {
  const DEFAULT_SERVER = "https://web-production-8fe4.up.railway.app";

  // Which part of the claim each check is about, so the overlay can outline it on the EMR page.
  const KIND_OF = {
    no_dx: "icd", bad_icd: "icd", icd_unknown: "icd", icd_header: "icd",
    no_cpt: "cpt", bad_cpt: "cpt", ncci: "cpt", multi_em: "cpt",
    no_payer: "payer",
    no_dos: "dos", future_dos: "dos", stale: "dos", aging: "dos",
    no_charge: "amount",
    dup_in_file: "claim_id", dup_existing: "claim_id",
  };

  // One plain next step per check, shown under the message.
  const FIX = {
    no_dx: "Add the ICD-10 code that supports each procedure, then save the claim.",
    bad_icd: "Retype the code as a letter, two characters, then an optional decimal, e.g. R07.9.",
    icd_unknown: "Look the code up again. It's probably a typo.",
    icd_header: "Pick one of the more specific codes under this category.",
    no_cpt: "Add the visit or procedure code for this encounter.",
    bad_cpt: "Use a 5-character CPT or HCPCS code, e.g. 99213 or J1885.",
    ncci: "If both services were separate and documented, add modifier 25 or 59. If not, remove the bundled code.",
    multi_em: "Keep only the visit level the note supports.",
    no_payer: "Attach the patient's insurance, or mark the claim self-pay.",
    no_dos: "Enter the date of service.",
    future_dos: "Correct the date of service.",
    stale: "Send this claim today and check the payer's filing deadline first.",
    aging: "Send this claim soon. It's getting close to some payers' deadlines.",
    no_charge: "Add the charge from your fee schedule.",
    dup_in_file: "Make sure this isn't the same visit billed twice.",
    dup_existing: "This claim number was checked before. Make sure it isn't a duplicate.",
  };

  const KIND_LABEL = { icd: "Diagnosis", cpt: "Procedure", payer: "Payer", dos: "Date of service", amount: "Charge", claim_id: "Claim number" };

  // Frames with labelled fields beat frames that only guessed from page text.
  function merge(outs) {
    const f = { claim_id: "", dos: "", payer: "", pos: "", cpt: [], icd: [], modifiers: [], amount: "", lines: [] };
    const found = {}, guessed = new Set(), hosts = new Set();
    outs.slice().sort((a, b) => a.guessed.length - b.guessed.length).forEach((o) => {
      hosts.add(o.host);
      for (const k of ["claim_id", "dos", "payer", "pos", "amount"]) if (!f[k] && o.fields[k]) f[k] = o.fields[k];
      for (const k of ["cpt", "icd", "modifiers"]) if (!f[k].length || !o.guessed.includes(k)) o.fields[k].forEach((x) => { if (!f[k].includes(x)) f[k].push(x); });
      if (!f.lines.length && o.fields.lines && o.fields.lines.length) f.lines = o.fields.lines;
      o.guessed.forEach((k) => guessed.add(k));
      Object.keys(o.found).forEach((k) => { if (o.found[k]) found[k] = true; });
    });
    return { fields: f, found, guessed: [...guessed], host: [...hosts].filter(Boolean).join(", "), frames: outs.length };
  }

  const fingerprint = (f) => JSON.stringify([f.claim_id, f.dos, f.payer, f.cpt, f.icd, f.modifiers, f.amount]);
  const sev = (s) => (s === "error" || s === "warn" ? s : "info");

  g.AlthaisShared = { DEFAULT_SERVER, KIND_OF, FIX, KIND_LABEL, merge, fingerprint, sev };
})(typeof self !== "undefined" ? self : globalThis);
