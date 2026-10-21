// Synthetic dental claims for testing the Althais extension. No real patients. Dates are relative to today.
// Procedure descriptions are short labels written for these test screens, not the ADA's CDT descriptors.
(function () {
  function d(daysAgo) { var t = new Date(Date.now() - daysAgo * 864e5); return ("0" + (t.getMonth() + 1)).slice(-2) + "/" + ("0" + t.getDate()).slice(-2) + "/" + t.getFullYear(); }
  // procedures: [code, tooth, surface, quadrant, label, fee]
  window.MOCK_DENTAL = [
    { id: "60001", expect: "Clean", note: "Recall visit: exam, cleaning, bitewings", patient: "DENTALTEST, ALPHA", dob: "04/12/1986", sex: "F", memberId: "DD-4410273", payer: "Delta Dental PPO", dentist: "Test Dentist DDS", dos: d(3),
      procs: [["D0120", "", "", "", "Periodic exam", 65], ["D1110", "", "", "", "Adult cleaning", 110], ["D0274", "", "", "", "Bitewings, four images", 75]],
      soap: "Recall visit.\nS: No complaints. Brushes twice daily, flosses occasionally.\nO: Periodic exam completed. Light plaque and calculus. Bitewings, four images, taken: no new caries. Gingiva pink, no bleeding.\nA: Healthy, routine recall.\nP: Adult prophylaxis completed. Next recall in 6 months." },
    { id: "60002", expect: "Ready, but two fillings never made it onto the claim", note: "Fillings in the note, not on the claim", patient: "DENTALTEST, BRAVO", dob: "09/30/1979", sex: "M", memberId: "MET-88120934", payer: "MetLife Dental", dentist: "Test Dentist DDS", dos: d(2),
      procs: [["D0120", "", "", "", "Periodic exam", 65], ["D1110", "", "", "", "Adult cleaning", 110]],
      soap: "Recall visit with restorative.\nS: Patient reports sensitivity to cold upper left.\nO: Periodic exam. Caries on #14 mesial and occlusal, and on #3 occlusal, confirmed on existing bitewings from March.\nTreatment: Local anesthetic, 2% lidocaine 1:100k. #14 MO composite restoration placed. #3 O composite restoration placed. Occlusion checked.\nAdult prophylaxis completed.\nA: Caries #14 MO and #3 O, restored today.\nP: Recall 6 months." },
    { id: "60003", expect: "Fix first: filling code doesn't match the surfaces", note: "1-surface code billed for 3 surfaces", patient: "DENTALTEST, CHARLIE", dob: "01/08/1992", sex: "F", memberId: "CIG-55017821", payer: "Cigna Dental", dentist: "Test Dentist DDS", dos: d(4),
      procs: [["D0140", "", "", "", "Limited exam", 85], ["D2391", "19", "MOD", "", "Posterior composite", 190]],
      soap: "Emergency visit.\nS: Broken filling lower left.\nO: Limited exam. #19 large fractured restoration, recurrent decay mesial, occlusal and distal.\nTreatment: #19 MOD composite restoration placed.\nA: Recurrent caries #19 MOD.\nP: Monitor; crown may be needed later." },
    { id: "60004", expect: "Fix first: no quadrant on scaling and root planing", note: "SRP with no quadrant, plus a cleaning", patient: "DENTALTEST, DELTA", dob: "11/22/1965", sex: "M", memberId: "AET-30199245", payer: "Aetna Dental", dentist: "Test Dentist DDS", dos: d(5),
      procs: [["D4341", "", "", "", "Scaling and root planing, 4+ teeth", 240], ["D1110", "", "", "", "Adult cleaning", 110]],
      soap: "Periodontal therapy.\nO: Probing depths 5-6 mm with bleeding on probing, upper right quadrant, teeth #2-#5. Radiographic bone loss.\nTreatment: Scaling and root planing, upper right quadrant, under local anesthetic. Adult prophylaxis also done on remaining areas.\nA: Generalized moderate periodontitis.\nP: Re-evaluate in 6 weeks; SRP other quadrants next visit." },
    { id: "60005", expect: "Worth a look: crown and filling on the same tooth", note: "Crown day, filling also billed", patient: "DENTALTEST, ECHO", dob: "06/14/1971", sex: "F", memberId: "GUA-77310450", payer: "Guardian Dental", dentist: "Test Dentist DDS", dos: d(1),
      procs: [["D2740", "30", "", "", "Porcelain/ceramic crown", 1150], ["D2391", "30", "O", "", "Posterior composite", 160]],
      soap: "Crown prep.\nO: #30 fractured cusp with large failing restoration.\nTreatment: Prepared #30 for all-ceramic crown, buildup placed, final impression taken, temporary crown cemented.\nA: Fractured tooth #30.\nP: Seat crown in 2 weeks." },
    { id: "60006", expect: "Fix first: extraction and filling on the same tooth, bad tooth number", note: "Tooth billed for an extraction and a filling", patient: "DENTALTEST, FOXTROT", dob: "02/02/1998", sex: "M", memberId: "UHC-90021637", payer: "UnitedHealthcare Dental", dentist: "Test Dentist DDS", dos: d(6),
      procs: [["D7140", "17", "", "", "Extraction, erupted tooth", 185], ["D2391", "17", "O", "", "Posterior composite", 160], ["D2140", "33", "O", "", "Amalgam, one surface", 120]],
      soap: "Extraction visit.\nO: #17 non-restorable caries.\nTreatment: Simple extraction #17 under local anesthetic. Hemostasis achieved.\nA: Non-restorable #17.\nP: Post-op instructions given." },
    { id: "60007", expect: "Fix first: front tooth billed with an occlusal surface; two exams", note: "Wrong surface on a front tooth", patient: "DENTALTEST, GOLF", dob: "07/19/2004", sex: "F", memberId: "", payer: "Humana Dental", dentist: "Test Dentist DDS", dos: d(7),
      procs: [["D0150", "", "", "", "Comprehensive exam", 95], ["D0120", "", "", "", "Periodic exam", 65], ["D2331", "8", "MO", "", "Anterior composite", 175]],
      soap: "New patient.\nO: Comprehensive exam completed. #8 mesial-incisal fracture.\nTreatment: #8 MI composite restoration placed.\nA: Fractured #8.\nP: Recall 6 months." }
  ];
})();
