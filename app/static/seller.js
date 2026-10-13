/* Seller view: bring in an invoice PDF, read its printed text through the real extractor, show what was read. */
(function () {
  "use strict";

  var UI = window.LabyrinthUI;
  var MAX_BYTES = 10 * 1024 * 1024;
  var file = null;
  var demoMode = false;

  function $(id) { return document.getElementById(id); }
  function pct(v) { return v == null ? null : Math.round(Number(v) * 100); }
  function human(key) { return String(key).replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); }); }

  /* ───── preview ───── */
  function renderSellerPreview(card) {
    if (!card || !card.invoice) return;
    var inv = card.invoice, prov = (card.version || {}).field_provenance || {};
    var ext = card.extraction || null;
    var fromPdf = card.source === "upload" && ext;

    var badge = $("seller-extract-badge");
    badge.textContent = fromPdf ? "EXTRACTED FROM PDF" : "SAMPLE DATA";
    badge.className = "badge " + (fromPdf ? "badge-pass" : "badge-info");
    $("seller-details-subtitle").textContent = fromPdf
      ? "Read from the uploaded file's printed text. Each value shows its page and confidence."
      : "A demo scenario, not an upload. Submit a PDF to see real extraction.";

    var notes = $("seller-notes");
    UI.clear(notes);
    if (fromPdf) {
      notes.appendChild(UI.el("p", { text: "Read by the " + String(ext.method || "text-layer").replace(/_/g, " ") + " parser. File fingerprint " + String(ext.file_sha256 || "").slice(0, 16) + "…" }));
      var printed = Object.keys(ext.printed || {});
      if (printed.length) {
        notes.appendChild(UI.el("p", { text: "Also printed on the invoice: " + printed.map(function (k) { return human(k) + " " + ext.printed[k]; }).join(" · ") }));
      }
      var kept = (ext.attached || []).map(function (k) { return { po: "purchase order", grn: "goods receipt", cnt: "contract", ewb: "e-way bill" }[k] || k; });
      notes.appendChild(UI.el("p", { text: kept.length ? "Supporting documents attached: " + kept.join(", ") + "." : "No supporting documents attached. Checks that need them will say unavailable and the request goes to review." }));
    }

    var grid = $("seller-extracted-fields");
    UI.clear(grid);
    var money = function (v) { return UI.formatInr(v); };
    [
      ["invoice_no", "Invoice number", inv.invoice_no],
      ["invoice_date", "Invoice date", inv.invoice_date],
      ["due_date", "Due date", inv.due_date],
      ["seller_gstin", "Seller GSTIN", inv.seller_gstin],
      ["buyer_gstin", "Buyer GSTIN", inv.buyer_gstin],
      ["subtotal_paise", "Subtotal", money(inv.subtotal_paise)],
      ["total_tax_paise", "Total GST", money(inv.total_tax_paise)],
      ["grand_total_paise", "Grand total", money(inv.grand_total_paise)],
    ].forEach(function (f) {
      var p = prov[f[0]] || {}, c = pct(p.confidence);
      var chip = c == null ? "No confidence reported" : c + "% · page " + (p.page || 1);
      grid.appendChild(UI.el("div", { className: "extracted-item" }, [
        UI.el("div", { className: "extracted-item-head" }, [
          UI.el("span", { className: "extracted-key", text: f[1] }),
          UI.el("span", { className: "confidence-chip" + (c != null && c < 90 ? " low" : ""), text: chip, title: c != null && c < 90 ? "Low confidence: check this value against the document" : "" }),
        ]),
        UI.el("div", { className: "extracted-val", text: f[2] == null || f[2] === "" ? "Not found" : f[2] }),
      ]));
    });

    var wrap = $("seller-line-items-wrap");
    UI.clear(wrap);
    var tbody = UI.el("tbody");
    (inv.line_items || []).forEach(function (li) {
      tbody.appendChild(UI.el("tr", {}, [
        UI.el("td", { text: String(li.line_no) }),
        UI.el("td", { text: li.description }),
        UI.el("td", { className: "mono-id", text: li.hsn_sac }),
        UI.el("td", { text: li.qty + " " + li.unit }),
        UI.el("td", { text: money(li.rate_paise) }),
        UI.el("td", { text: money(li.taxable_paise) }),
        UI.el("td", { text: money(li.tax_paise) }),
        UI.el("td", { text: money(li.total_paise) }),
      ]));
    });
    wrap.appendChild(UI.el("table", { className: "data-table" }, [
      UI.el("thead", {}, [UI.el("tr", {}, ["#", "Description", "HSN/SAC", "Qty", "Rate", "Taxable", "GST", "Line total"].map(function (t) { return UI.el("th", { text: t, scope: "col" }); }))]),
      tbody,
    ]));

    var checks = $("seller-extraction-checks");
    UI.clear(checks);
    if (fromPdf && (ext.validators || []).length) {
      checks.appendChild(UI.el("h3", { className: "section-heading", text: "Extraction checks" }));
      ext.validators.forEach(function (v) {
        var cls = v.status === "pass" ? "badge-pass" : v.status === "fail" ? "badge-fail" : v.status === "warn" ? "badge-warn" : "badge-info";
        checks.appendChild(UI.el("div", { className: "ec-row" }, [
          UI.el("span", { className: "badge " + cls, text: v.status }),
          UI.el("span", { text: human(String(v.check).replace(/^extract\./, "")) + (v.evidence && v.evidence[0] ? " — " + v.evidence[0] : "") }),
        ]));
      });
    }
  }

  /* ───── banner + errors ───── */
  function banner(kind, text, action) {
    var msg = $("seller-status-msg");
    msg.classList.remove("hidden", "error", "success");
    if (kind) msg.classList.add(kind);
    UI.clear(msg);
    msg.appendChild(document.createTextNode(text));
    if (action) msg.appendChild(UI.el("div", { className: "banner-action" }, [action]));
  }
  function fileError(text) {
    var box = $("seller-file-error");
    box.textContent = text || "";
    box.classList.toggle("hidden", !text);
  }

  /* ───── file handling ───── */
  function setFile(f) {
    fileError("");
    file = null;
    $("pdf-dropzone").classList.remove("has-file");
    $("seller-submit-btn").disabled = true;
    $("dropzone-filename").textContent = "Drop an invoice PDF here or click to browse";
    if (!f) return Promise.resolve();
    if (f.size > MAX_BYTES) { fileError("That file is " + (f.size / 1048576).toFixed(1) + " MB. The limit is 10 MB."); return Promise.resolve(); }
    if (!/\.pdf$/i.test(f.name) && f.type !== "application/pdf") { fileError("Only PDF files can be submitted. Choose an invoice saved as a PDF."); return Promise.resolve(); }
    return f.slice(0, 5).text().then(function (head) {
      if (head !== "%PDF-") { fileError("This file is named like a PDF but does not start like one, so it was not accepted."); return; }
      file = f;
      $("dropzone-filename").textContent = f.name + " · " + (f.size / 1024).toFixed(0) + " KB";
      $("pdf-dropzone").classList.add("has-file");
      $("seller-submit-btn").disabled = false;
    });
  }

  function generateSample(layout) {
    var bad = $("sample-bad-arith").checked ? "bad_arith" : "clean";
    $("seller-fixture-select").value = "clean_goods"; // the sample is printed for the clean-goods parties
    banner("", "Generating a sample invoice…");
    return fetch("/demo/sample-invoice?layout=" + encodeURIComponent(layout) + "&variant=" + bad)
      .then(function (r) { if (!r.ok) throw new Error("Sample generator returned HTTP " + r.status); return r.blob(); })
      .then(function (blob) {
        var f = new File([blob], "sample-" + layout + (bad === "bad_arith" ? "-bad-total" : "") + ".pdf", { type: "application/pdf" });
        return setFile(f);
      })
      .then(function () { $("seller-status-msg").classList.add("hidden"); })
      .catch(function (e) { banner("error", e.message); });
  }

  function attachedKeys() {
    return Array.prototype.filter.call(document.querySelectorAll("[data-attach]"), function (c) { return c.checked; })
      .map(function (c) { return c.dataset.attach; }).join(",");
  }

  function submit() {
    if (!file) return;
    var btn = $("seller-submit-btn");
    btn.classList.add("busy"); btn.disabled = true;
    $("seller-status-msg").classList.add("hidden");
    var fd = new FormData();
    fd.append("file", file);
    fd.append("case_id", $("seller-fixture-select").value || "clean_goods");
    fd.append("attach", attachedKeys());
    UI.apiFetch("/requests", { method: "POST", body: fd })
      .then(function (card) {
        renderSellerPreview(card);
        var outcome = UI.outcomeLabel(card.decision.outcome);
        var view = UI.el("button", { type: "button", className: "btn btn-secondary btn-sm", text: "View decision →", onClick: function () {
          UI.switchTab("lender");
          if (window.LabyrinthLender) window.LabyrinthLender.loadCase(card.case_id);
        } });
        banner("success", "Submitted as " + card.invoice.invoice_no + ". Outcome: " + outcome + ".", view);
        if (window.LabyrinthLender && window.LabyrinthLender.refreshCases) window.LabyrinthLender.refreshCases(card.case_id);
      })
      .catch(function (err) {
        var missing = err.payload && err.payload.missing;
        if (err.status === 422) {
          banner("error", "This PDF could not be read as an invoice. " + (missing && missing.length ? "Missing: " + missing.map(human).join(", ") + ". " : "") + "Fix the document or enter the invoice manually.");
        } else if (err.status === 503) {
          banner("error", "Submitting needs the server. Start it with LABYRINTH_DEMO=1 python run.py demo.");
        } else {
          banner("error", "Submission failed (" + (err.status || "network") + "): " + err.message);
        }
      })
      .then(function () { btn.classList.remove("busy"); btn.disabled = !file; });
  }

  /* ───── init ───── */
  function initSellerTab() {
    var zone = $("pdf-dropzone"), input = $("pdf-file-input");
    zone.addEventListener("click", function () { input.click(); });
    zone.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    input.addEventListener("change", function () { setFile(input.files && input.files[0]); input.value = ""; });
    ["dragenter", "dragover"].forEach(function (n) { zone.addEventListener(n, function (e) { e.preventDefault(); zone.classList.add("drag-over"); }); });
    ["dragleave", "drop"].forEach(function (n) { zone.addEventListener(n, function (e) { e.preventDefault(); zone.classList.remove("drag-over"); }); });
    zone.addEventListener("drop", function (e) { var f = e.dataTransfer && e.dataTransfer.files[0]; if (f) setFile(f); });

    $("seller-submit-btn").addEventListener("click", submit);
    $("seller-submit-btn").disabled = true;
    $("seller-sample-open-btn").addEventListener("click", function () {
      UI.switchTab("lender");
      if (window.LabyrinthLender) window.LabyrinthLender.loadCase($("seller-fixture-select").value);
    });
    $("seller-fixture-select").addEventListener("change", function () {
      UI.apiFetch("/requests/" + encodeURIComponent(this.value)).then(renderSellerPreview).catch(function () {});
    });
    document.querySelectorAll("[data-sample-layout]").forEach(function (b) {
      b.addEventListener("click", function () { generateSample(b.dataset.sampleLayout); });
    });

    if (window.location.protocol !== "file:") {
      fetch("/health").then(function (r) { return r.json(); }).then(function (h) {
        demoMode = !!h.demo_mode;
        $("sample-tools").classList.toggle("hidden", !demoMode);
      }).catch(function () {});
    }
  }

  window.LabyrinthSeller = { init: initSellerTab, renderPreview: renderSellerPreview };
})();
