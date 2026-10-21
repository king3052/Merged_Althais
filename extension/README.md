# Althais for EMRs (browser extension prototype)

A small Althais badge that sits on top of the clinic's EMR. Its card has three tabs:

- **Check**: checks each claim as the biller opens it. Shows **Ready to submit**, **Worth a look** or **Fix first**, outlines the fields to fix, and warns if someone presses the EMR's Submit button while the claim still has problems.
- **Code note**: reads the visit note on screen (or the text you select) and suggests CPT and ICD-10 codes, each with the line from the note that supports it. **Fill into EMR** types the picked codes into empty code fields on the claim screen; **Use in claim** carries them to the next tab.
- **Create claim**: builds a claim from the screen (codes, charges, patient, insurance) and lets the biller fix any field. **Preview and save draft** or **Preview and approve** first shows the whole claim laid out like the claim form (patient, insurance, provider, diagnoses A–L, services and total) with anything that would stop it; nothing is saved or approved until the biller confirms on that preview. Once started, the form follows the screen: open another claim and it refills for that claim. Approved claims go out with Althais's end-of-day batch to the clearinghouse, and Althais holds them, with the reason, if the clinic isn't ready to send yet.

A side panel holds the full check breakdown, manual editing and settings.

## Install (Chrome or Edge)
1. Open `chrome://extensions` (Edge: `edge://extensions`) and turn on **Developer mode**.
2. Press **Load unpacked** and choose this `extension` folder. Pin the Althais icon from the puzzle-piece menu.
3. In Althais, open **Revenue › Connect EMR › Browser Extension** and press **Connect This Browser**. That's it: no token to copy.
   (If the button isn't there, reload the Althais page. You can also press **Create Token** and paste it under the gear in the side panel.)

## Use
- Open a claim in your EMR. The first time on a new site, click the Althais icon, press **Read page** and choose **Allow**. From then on the badge appears on that site by itself.
- Click the badge for the problems and how to fix each one. **Show on page** scrolls to the field and flashes its outline.
- Drag the badge anywhere; it remembers the spot for each site. **Alt+Shift+A** shows or hides it.
- **Hide on this site** turns it off for that site. Turn it back on under the gear in the side panel.
- The toolbar icon shows the number of problems on the current claim.

## Test it with fake patients
Open `/static/mock-emr/index.html` on your Althais server. Seven synthetic claims each carry a known problem (no diagnosis, an ICD-9 code, NCCI bundling, a $0 charge, an aging visit, no payer). Click through them and watch the badge.

To try the other tabs: open claim 50002 (no diagnosis), switch the mock EMR to **Visit note**, and press **Code note › Code this note**. Switch back to **Claim** and press **Fill into EMR**: the diagnosis goes into the first empty row and the badge turns to Ready to submit. Then **Use in claim** to build the claim, and **Approve for sending**.

## How it works
- `content.js` runs in each frame of a site you allowed (EMRs like athenaOne put claim screens inside frames). It notices page changes, reads the page with `parse.js` when asked, outlines fields, and draws the badge in an isolated layer that can't change the EMR's styling.
- `background.js` merges what every frame found, asks Althais to check it (only when the claim changed), and sends the result to the page, the toolbar badge and the side panel.
- `pair.js` runs only on Althais's own pages and handles the one-click connection.
- `panel.html` / `panel.js` is the side panel: full results, manual edits, Fetch by claim ID (athenahealth), the page report, and settings, including turning the on-page overlay off.

## What it does and doesn't do
- Reads only sites you allowed, plus Althais's own test screens.
- **Check** looks for claim ID, date of service, payer, CPT/HCPCS, ICD-10, modifiers and charge. It never reads or sends the patient's name or date of birth, and Althais stores nothing about the check.
- **Code note** sends the note text to Althais's coder only when you press the button. Every suggestion needs a person's review.
- **Create claim** reads the patient's name, date of birth, sex and member ID only when you press **Start claim from this screen**, because a claim can't be sent without them. It saves the claim to your Althais account.
- Nothing is sent to a payer or clearinghouse without a person pressing **Approve for sending**, and even then Althais checks the claim again before its batch.
- The extension only changes the EMR when you press **Fill into EMR**, and then only empty code fields. You review and save in the EMR as usual. It never blocks a click, including Submit.
- Calls act as the person who connected the browser. Browsers connected before version 0.6 need to connect again for Code note and Create claim.
- Page reading is pattern based. On a layout it hasn't seen it may miss fields; the side panel's page report (every value masked) helps tune it.

## Before real patient data
Test on sandbox or test data only until the practice has a signed agreement with Althais (and Althais has its own agreements with the services it uses). Drawing on an EMR's screens and typing into them should also be cleared against that vendor's terms for third-party tools before a clinic uses it.

**Don't use Create claim for clinics whose EMR sends claims itself** (athenaOne, Tebra and similar): the payer would get the claim twice. For those clinics, use Code note and Fill into EMR, then submit from the EMR.
