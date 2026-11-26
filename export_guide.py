"""How a clinic's biller gets the payment (835) and claim (837) files Althais reads: a public page at /help/payment-files.

Public on purpose: it holds no clinic data, and it's the page to send a biller before the first report. Menu names in billing
systems change often, so the page says where to look and gives a request to copy to the vendor's support, rather than click paths
that go stale.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

import lost_money as lm

router = APIRouter()

REQUEST = """Hello,

Please send us our raw electronic remittance (ERA / 835) files for all payers, for payments from [START DATE] to [END DATE].
We need the original X12 835 files (they open as text starting with "ISA"), not PDF or printed EOBs. A zip file is fine.

If you can, please also include the 837 claim files we sent for dates of service in the same period.

Thank you,
[Your name], [Practice name]"""

PAGE = """<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Getting your payment files | Althais</title><style>""" + lm.STYLE + """
main{max-width:780px;margin:0 auto;padding:24px 18px 60px}body{font-size:14px}h1{font-size:20px}h2{font-size:15px;margin-top:26px}
li{margin:4px 0}pre{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:3px;padding:12px;font:13px/1.5 "IBM Plex Mono",monospace}
code{font-family:"IBM Plex Mono",monospace;background:var(--chip);padding:0 4px;border-radius:2px}
.warn{background:var(--warn-soft);border:1px solid var(--line);border-radius:3px;padding:10px 12px}
</style></head><body><main>
<h1>Getting your payment and claim files</h1>
<p class=sub>For the biller or office manager. Takes about 10 minutes, or one email to your vendor's support.</p>

<div class=warn><b>Don't email these files.</b> They contain patient information. Upload them only inside Althais
(Lost Money page), and only after your practice and Althais have signed the business associate agreement (BAA).</div>

<h2>What Althais needs</h2>
<ul>
<li><b>Payment files (835, also called ERA or electronic remittance advice).</b> Required. One file per payment or batch is normal;
upload as many as you have, or a zip.</li>
<li><b>Claim files (837).</b> Optional, but they let Althais find claims that never got an answer.</li>
<li><b>How far back:</b> at least 90 days of payments. Twelve months finds more, especially claims with no answer.</li>
</ul>
<p>The right files are plain text that start with <code>ISA*</code> (open one in Notepad to check). Common names end in
<code>.835</code>, <code>.era</code>, <code>.x12</code>, <code>.edi</code> or <code>.txt</code>. PDF EOBs, screenshots and printed
remittances can't be read.</p>
<p>No 837 files? A claims report saved as CSV or Excel-to-CSV works instead, with columns for claim number, date of service, payer,
procedure code, charge and amount paid.</p>

<h2>Where they come from</h2>
<ol>
<li><b>Your clearinghouse</b> (the company that sends your claims to insurers) keeps both. In its website, look under
<b>ERA</b>, <b>Remittance</b>, <b>835</b> or <b>Reports</b> for a download of the raw 835 files, choose the date range and all
payers. Sent claim batches (837) are usually under <b>Claims</b>, <b>Batches</b> or <b>Sent files</b>.</li>
<li><b>Claim.MD users:</b> no export needed. Connect your Claim.MD account on the Lost Money page and new payment files arrive every
hour.</li>
<li><b>Billing done inside your EHR or practice management system:</b> the system receives the 835s from its clearinghouse. Menu
names vary by system and version, so the quickest way is to send support the request below.</li>
<li><b>Outside billing company:</b> send them the request below. They receive your 835s and can export them.</li>
</ol>

<h2>Request to send your vendor or billing company</h2>
<p class=sub>Copy, fill in the dates, and send it to your clearinghouse, EHR support or billing company.</p>
<pre id=req>__REQUEST__</pre>
<p><button id=copy>Copy the request</button> <span class=sub id=ok></span></p>

<h2>Then</h2>
<ol>
<li>Sign in to Althais and open <b>Lost Money</b>.</li>
<li>Drop the files (or the zip) on the page. Each file shows what was found in it; a file it can't read is listed with the reason.</li>
<li>Read the report, then set your appeal windows on <b>Clinic setup</b> so deadlines come from your contracts.</li>
</ol>
<p class=more>Stuck on a file? Write to kyankarla@althais.com with the name of your clearinghouse or billing system
(not the file itself) and we'll find where it lives.</p>
<script>
document.getElementById('copy').onclick=async()=>{try{await navigator.clipboard.writeText(document.getElementById('req').textContent);
document.getElementById('ok').textContent='Copied.'}catch(e){document.getElementById('ok').textContent='Select the text above and copy it.'}};
</script>
</main></body></html>"""


@router.get("/help/payment-files", response_class=HTMLResponse)
def page():
    import html
    return HTMLResponse(PAGE.replace("__REQUEST__", html.escape(REQUEST)))
