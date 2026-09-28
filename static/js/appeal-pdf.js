/* Appeal letters as PDFs (Revenue > Appeals and the EMR's appeal letter window).
 *   AppealPDF.save([{ text: "the letter" }, ...], "appeal_CLM123.pdf")
 * One letter per page run (each letter starts on a new page), US Letter, 1-inch margins, page numbers.
 * Uses jsPDF from cdnjs, loaded the first time it's needed. If it can't load, the letter opens in the
 * browser's print dialog instead, where "Save as PDF" does the same job. */
(function () {
  "use strict";
  var SRC = "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js";
  var loading = null;

  function lib() {
    if (window.jspdf && window.jspdf.jsPDF) return Promise.resolve(window.jspdf.jsPDF);
    if (!loading) {
      loading = new Promise(function (resolve, reject) {
        var s = document.createElement("script");
        s.src = SRC; s.async = true;
        s.onload = function () { window.jspdf && window.jspdf.jsPDF ? resolve(window.jspdf.jsPDF) : reject(new Error("PDF library missing")); };
        s.onerror = function () { loading = null; reject(new Error("Could not load the PDF library")); };
        document.head.appendChild(s);
      });
    }
    return loading;
  }

  /* jsPDF's built-in fonts only cover Latin-1: swap the usual typographic characters for plain ones */
  function clean(t) {
    return String(t || "").replace(/\r\n?/g, "\n").replace(/[‘’‛]/g, "'").replace(/[“”‟]/g, '"')
      .replace(/[–—]/g, "-").replace(/…/g, "...").replace(/ /g, " ").replace(/[•●]/g, "-")
      .replace(/[^\x09\x0A\x20-\x7E\xA0-\xFF]/g, "");
  }

  function build(JsPDF, letters) {
    var doc = new JsPDF({ unit: "pt", format: "letter" });
    var W = doc.internal.pageSize.getWidth(), H = doc.internal.pageSize.getHeight();
    var M = 72, SIZE = 11, LEAD = SIZE * 1.45, maxW = W - M * 2;
    doc.setFont("times", "normal"); doc.setFontSize(SIZE); doc.setTextColor(26, 29, 36);
    letters.forEach(function (l, i) {
      if (i > 0) doc.addPage();
      var y = M;
      clean(l.text).split("\n").forEach(function (para) {
        var lines = para.trim() === "" ? [""] : doc.splitTextToSize(para, maxW);
        lines.forEach(function (line) {
          if (y + LEAD > H - M) { doc.addPage(); y = M; }
          doc.text(line, M, y + SIZE);
          y += LEAD;
        });
      });
    });
    var n = doc.getNumberOfPages();
    for (var p = 1; p <= n; p++) {
      doc.setPage(p); doc.setFont("helvetica", "normal"); doc.setFontSize(8.5); doc.setTextColor(139, 144, 156);
      doc.text("Page " + p + " of " + n, W - M, H - 36, { align: "right" });
    }
    return doc;
  }

  function printFallback(letters) {
    var w = window.open("", "_blank");
    if (!w) { alert("Allow pop-ups for Althais to save the letter as a PDF."); return; }
    var esc = function (t) { return String(t).replace(/[&<>]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]; }); };
    w.document.write('<!DOCTYPE html><html><head><title>Appeal Letter</title><style>@page{size:letter;margin:1in}body{font:11pt/1.45 "Times New Roman",serif;color:#1a1d24}' +
      'pre{white-space:pre-wrap;font:inherit;margin:0}.pg{page-break-after:always}.pg:last-child{page-break-after:auto}</style></head><body>' +
      letters.map(function (l) { return '<div class="pg"><pre>' + esc(l.text) + "</pre></div>"; }).join("") + "</body></html>");
    w.document.close(); w.focus(); setTimeout(function () { w.print(); }, 250);
  }

  window.AppealPDF = {
    save: function (letters, filename) {
      letters = (letters || []).filter(function (l) { return l && l.text; });
      if (!letters.length) return Promise.resolve(false);
      return lib().then(function (JsPDF) { build(JsPDF, letters).save(filename || "appeal_letter.pdf"); return true; })
        .catch(function () { printFallback(letters); return true; });
    }
  };
})();
