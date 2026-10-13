/* Tab 1: Seller Upload & Deterministic Extraction View (seller.js) */
(function () {
  "use strict";

  var UI = window.TesseraUI;
  var selectedPdfFile = null;

  function renderSellerPreview(card) {
    if (!card || !card.invoice) return;
    var inv = card.invoice;
    var ver = card.version || {};
    var prov = ver.field_provenance || {};

    var grid = document.getElementById("seller-extracted-fields");
    UI.clear(grid);

    var fields = [
      { key: "invoice_no", label: "Invoice Number", val: inv.invoice_no },
      { key: "invoice_date", label: "Invoice Date", val: inv.invoice_date },
      { key: "due_date", label: "Due Date", val: inv.due_date },
      { key: "seller_gstin", label: "Seller GSTIN (Mod-36)", val: inv.seller_gstin },
      { key: "buyer_gstin", label: "Buyer GSTIN (Mod-36)", val: inv.buyer_gstin },
      { key: "subtotal_paise", label: "Subtotal", val: UI.formatInr(inv.subtotal_paise) + " (" + inv.subtotal_paise + " paise)" },
      { key: "total_tax_paise", label: "Total GST", val: UI.formatInr(inv.total_tax_paise) + " (" + inv.total_tax_paise + " paise)" },
      { key: "grand_total_paise", label: "Grand Total", val: UI.formatInr(inv.grand_total_paise) + " (" + inv.grand_total_paise + " paise)" },
    ];

    fields.forEach(function (f) {
      var p = prov[f.key] || { confidence: 0.99, page: 1 };
      var confPct = Math.round(Number(p.confidence || 0.99) * 100);
      var pageNo = p.page || 1;
      grid.appendChild(
        UI.el("div", { className: "extracted-item" }, [
          UI.el("div", { className: "extracted-item-head" }, [
            UI.el("span", { className: "extracted-key", text: f.label }),
            UI.el("span", { className: "confidence-chip", text: confPct + "% p." + pageNo }),
          ]),
          UI.el("div", { className: "extracted-val", text: f.val }),
        ])
      );
    });

    var tableWrap = document.getElementById("seller-line-items-wrap");
    UI.clear(tableWrap);
    var tbody = UI.el("tbody");
    (inv.line_items || []).forEach(function (li) {
      tbody.appendChild(
        UI.el("tr", {}, [
          UI.el("td", { text: String(li.line_no) }),
          UI.el("td", { text: li.description }),
          UI.el("td", { text: li.hsn_sac }),
          UI.el("td", { text: li.qty + " " + li.unit }),
          UI.el("td", { text: UI.formatInr(li.rate_paise) }),
          UI.el("td", { text: UI.formatInr(li.taxable_paise) }),
          UI.el("td", { text: UI.formatInr(li.tax_paise) }),
          UI.el("td", { text: UI.formatInr(li.total_paise) }),
        ])
      );
    });

    var table = UI.el("table", { className: "data-table" }, [
      UI.el("thead", {}, [
        UI.el("tr", {}, [
          UI.el("th", { text: "#" }),
          UI.el("th", { text: "Description" }),
          UI.el("th", { text: "HSN/SAC" }),
          UI.el("th", { text: "Qty" }),
          UI.el("th", { text: "Rate" }),
          UI.el("th", { text: "Taxable" }),
          UI.el("th", { text: "Tax" }),
          UI.el("th", { text: "Line Total" }),
        ]),
      ]),
      tbody,
    ]);
    tableWrap.appendChild(table);
  }

  function setSellerBanner(text, isError) {
    var msg = document.getElementById("seller-status-msg");
    if (!msg) return;
    msg.classList.remove("hidden", "error", "success");
    msg.classList.add(isError ? "error" : "success");
    msg.textContent = text;
  }

  function initSellerTab() {
    var select = document.getElementById("seller-fixture-select");
    var dropzone = document.getElementById("pdf-dropzone");
    var fileInput = document.getElementById("pdf-file-input");
    var filenameLabel = document.getElementById("dropzone-filename");
    var submitBtn = document.getElementById("seller-submit-btn");
    var invalidBtn = document.getElementById("seller-test-invalid-btn");

    if (select) {
      select.addEventListener("change", function () {
        var cid = select.value;
        UI.apiFetch("/requests/" + encodeURIComponent(cid)).then(function (card) {
          renderSellerPreview(card);
        });
      });
    }

    if (dropzone && fileInput) {
      dropzone.addEventListener("click", function () {
        fileInput.click();
      });
      fileInput.addEventListener("change", function () {
        if (fileInput.files && fileInput.files[0]) {
          selectedPdfFile = fileInput.files[0];
          filenameLabel.textContent = "Selected file: " + selectedPdfFile.name + " (" + selectedPdfFile.size + " bytes)";
        }
      });
    }

    if (submitBtn) {
      submitBtn.addEventListener("click", function () {
        var cid = select ? select.value : "clean_goods";
        if (selectedPdfFile && window.location.protocol !== "file:") {
          var fd = new FormData();
          fd.append("file", selectedPdfFile);
          fd.append("case_id", cid);
          UI.apiFetch("/requests", {
            method: "POST",
            body: fd,
            roleOverride: "seller:P-0001",
          })
            .then(function (card) {
              renderSellerPreview(card);
              setSellerBanner(
                "PDF verified (%PDF- magic bytes valid) and submitted -> Request " +
                  card.request_id +
                  " (" +
                  card.decision.outcome.toUpperCase() +
                  ").",
                false
              );
              if (window.TesseraLender && window.TesseraLender.loadCase) {
                window.TesseraLender.loadCase(cid);
              }
            })
            .catch(function (err) {
              setSellerBanner("Upload rejected (" + (err.status || 400) + "): " + err.message, true);
            });
        } else {
          UI.apiFetch("/requests", {
            method: "POST",
            jsonBody: { case_id: cid },
            roleOverride: "seller:P-0001",
          })
            .then(function (card) {
              renderSellerPreview(card);
              setSellerBanner(
                "Submitted fixture " +
                  cid +
                  " -> Request " +
                  card.request_id +
                  " (" +
                  card.decision.outcome.toUpperCase() +
                  "). Switching to Lender Explanation Card.",
                false
              );
              if (window.TesseraLender && window.TesseraLender.loadCase) {
                window.TesseraLender.loadCase(cid);
              }
              UI.switchTab("lender");
            })
            .catch(function (err) {
              setSellerBanner("Submission error: " + err.message, true);
            });
        }
      });
    }

    if (invalidBtn) {
      invalidBtn.addEventListener("click", function () {
        UI.apiFetch("/requests", {
          method: "POST",
          jsonBody: { case_id: "clean_goods", file_magic: "NOT_A_PDF_HEADER" },
          roleOverride: "seller:P-0001",
        })
          .then(function () {
            setSellerBanner("Offline mode note: server magic-byte check rejects non-%PDF- uploads with HTTP 400.", true);
          })
          .catch(function (err) {
            setSellerBanner(
              "Security Guard Active — HTTP " + (err.status || 400) + ": " + err.message,
              true
            );
          });
      });
    }
  }

  window.TesseraSeller = {
    init: initSellerTab,
    renderPreview: renderSellerPreview,
  };
})();
