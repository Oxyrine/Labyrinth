/* Tab 4: Hybrid Cryptographic Audit Verifier & Interactive Judge Break-It (verifier.js) */
(function () {
  "use strict";

  var UI = window.TesseraUI;

  function renderAuditVerification(proof) {
    if (!proof) return;

    var intBox = document.getElementById("audit-integrity-box");
    var intStatus = document.getElementById("audit-integrity-status");
    var intDetail = document.getElementById("audit-integrity-detail");

    var ancBox = document.getElementById("audit-anchoring-box");
    var ancStatus = document.getElementById("audit-anchoring-status");
    var ancDetail = document.getElementById("audit-anchoring-detail");

    var isVerified = proof.integrity === "verified";
    if (intBox && intStatus && intDetail) {
      intBox.className = "answer-box " + (isVerified ? "verified" : "failed");
      intStatus.textContent = isVerified
        ? "VERIFIED"
        : "FAILED (" + (proof.failed_file || proof.location || proof.target) + ")";
      intDetail.textContent = proof.integrity_detail || "";
    }

    var isAnchored = proof.anchoring === "anchored";
    if (ancBox && ancStatus && ancDetail) {
      ancBox.className = "answer-box " + (isAnchored ? "verified" : "unavailable");
      ancStatus.textContent = String(proof.anchoring || "anchored").toUpperCase();
      ancDetail.textContent = proof.anchoring_detail || "";
    }

    // Also update the global header banner badge #2
    var globalInt = document.getElementById("banner-badge-integrity");
    if (globalInt) {
      globalInt.textContent =
        "INTEGRITY: " +
        (isVerified ? "VERIFIED" : "FAILED (" + (proof.failed_file || "BLOB") + ")") +
        " · ANCHOR: " +
        String(proof.anchoring || "anchored").toUpperCase();
      globalInt.className = "badge " + (isVerified ? (isAnchored ? "badge-pass" : "badge-warn") : "badge-fail");
    }

    var discBox = document.getElementById("audit-disclosure-box");
    UI.clear(discBox);
    var sd = proof.selective_disclosure || {};
    var rows = [
      { k: "Disclosed Field", v: sd.disclosed_field + " = " + sd.disclosed_formatted + " (" + sd.disclosed_value_paise + " paise)" },
      { k: "Single Disclosed Salt", v: sd.disclosed_salt || "—" },
      { k: "RFC-6962 Leaf Hash (0x00)", v: sd.leaf_hash || "—" },
      { k: "Invoice Field Root (0x01)", v: sd.field_root || proof.field_root || "—" },
      { k: "Anchored Batch Merkle Root", v: proof.merkle_root || "—" },
      { k: "Redacted / Unrevealed Fields", v: (sd.hidden_fields || []).join(", ") + " (salts withheld)" },
    ];
    rows.forEach(function (r) {
      discBox.appendChild(
        UI.el("div", { style: "margin-bottom:6px;" }, [
          UI.el("span", { className: "extracted-key", text: r.k + ": " }),
          UI.el("span", { style: "font-family:var(--font-mono);font-size:11.5px;color:var(--text-primary);", text: r.v }),
        ])
      );
    });
  }

  function setOfflineNotice() {
    var d = document.getElementById("audit-integrity-detail");
    if (d) d.textContent = "Tamper and network-off controls need the live server; this is a read-only snapshot.";
  }

  function populateTargets() {
    var sel = document.getElementById("audit-target-select");
    if (!sel) return Promise.resolve();
    return Promise.all(
      ["clean_goods", "ring_real_firms"].map(function (cid) {
        return UI.apiFetch("/requests/" + cid).catch(function () { return null; });
      })
    ).then(function (cards) {
      UI.clear(sel);
      cards.forEach(function (c) {
        if (!c || !c.audit_proof) return;
        (c.audit_proof.encrypted_blobs || []).forEach(function (b) {
          sel.appendChild(UI.el("option", { value: b.doc_id, text: b.doc_id + " (" + b.doc_type + " · " + c.case_id + ")" }));
        });
        sel.appendChild(UI.el("option", { value: c.case_id, text: "Case bundle · " + c.case_id + " (" + c.request_id + ")" }));
      });
    });
  }

  function verifyCurrentTarget() {
    var sel = document.getElementById("audit-target-select");
    var target = sel ? sel.value : "D-PO-0001";
    return UI.apiFetch("/audit/verify/" + encodeURIComponent(target), {
      roleOverride: "auditor:AUD-1",
    }).then(function (proof) {
      renderAuditVerification(proof);
      return proof;
    });
  }

  function buildBreakitPayload() {
    return {
      ewb_speed_kmh: Number(document.getElementById("breakit-speed-input").value || 410),
      retained_pct: Number(document.getElementById("breakit-retained-input").value || 0.7),
      links: Number(document.getElementById("breakit-links-input").value || 2),
      total_days: Number(document.getElementById("breakit-days-input").value || 2),
      hsn_transformation: document.getElementById("breakit-transform-select").value === "true",
    };
  }

  function renderBreakitResult(res) {
    var panel = document.getElementById("breakit-result-panel");
    UI.clear(panel);
    var tier = String(res.tier || "low").toUpperCase();
    var badgeClass = tier === "HIGH" ? "badge-fail" : (tier === "MEDIUM" ? "badge-warn" : "badge-pass");

    panel.appendChild(
      UI.el("div", { style: "display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;" }, [
        UI.el("strong", { text: "Live Network Score: " + Number(res.score || 0).toFixed(4) + " -> " + String(res.outcome_recommendation || "").toUpperCase() }),
        UI.el("span", { className: "badge " + badgeClass, text: "TIER: " + tier }),
      ])
    );

    var ul = UI.el("ul", { className: "reasons-list" });
    (res.evidence || []).forEach(function (line) {
      ul.appendChild(UI.el("li", { text: line }));
    });
    panel.appendChild(ul);

    var svgBox = document.getElementById("breakit-ring-svg");
    if (svgBox && res.ring_graph) {
      UI.renderRingSvg(svgBox, res.ring_graph, null);
    }
  }

  function pct(x) {
    return (Number(x || 0) * 100).toFixed(1) + "%";
  }

  function renderEvalSummary(report) {
    var box = document.getElementById("eval-summary-box");
    if (!box) return;
    UI.clear(box);
    if (!report || !report.invoice_metrics) {
      box.appendChild(UI.el("div", { className: "helper-copy", text: "No evaluation report found. Run: python run.py report" }));
      return;
    }
    var m = report.invoice_metrics;
    var meta = report.metadata || {};
    box.appendChild(
      UI.el("p", {
        className: "helper-copy",
        text: "Synthetic benchmark results (regime " + meta.regime + ", thresholds frozen before the held-out run). Real-world validation is future work.",
      })
    );
    box.appendChild(
      UI.el("div", { className: "pricing-metrics-grid" }, [
        UI.el("div", { className: "pricing-stat" }, [
          UI.el("div", { className: "pricing-stat-label", text: "Rules-only baseline detection" }),
          UI.el("div", { className: "pricing-stat-val", text: pct(m.baseline_overall_detection_rate) }),
        ]),
        UI.el("div", { className: "pricing-stat" }, [
          UI.el("div", { className: "pricing-stat-label", text: "Baseline + network detection" }),
          UI.el("div", { className: "pricing-stat-val", text: pct(m.hybrid_overall_detection_rate) + " (+" + Number(m.overall_lift_pct || 0).toFixed(1) + " pts)" }),
        ]),
        UI.el("div", { className: "pricing-stat" }, [
          UI.el("div", { className: "pricing-stat-label", text: "False-review rate on legitimate invoices" }),
          UI.el("div", { className: "pricing-stat-val", text: pct(m.invoice_false_review_rate_hybrid) }),
        ]),
        UI.el("div", { className: "pricing-stat" }, [
          UI.el("div", { className: "pricing-stat-label", text: "Plausible forgeries missed (reported, not hidden)" }),
          UI.el("div", { className: "pricing-stat-val", text: String((report.evasive_misses || []).length) }),
        ]),
      ])
    );

    var tbody = UI.el("tbody");
    (report.per_case_type || []).forEach(function (r) {
      tbody.appendChild(
        UI.el("tr", {}, [
          UI.el("td", { text: r.case_type }),
          UI.el("td", { text: String(r.n) }),
          UI.el("td", { text: pct(r.baseline_detection_rate) }),
          UI.el("td", { text: pct(r.hybrid_detection_rate) }),
          UI.el("td", { text: r.is_positive ? "detection rate" : "clean approval rate" }),
        ])
      );
    });
    box.appendChild(
      UI.el("table", { className: "data-table" }, [
        UI.el("thead", {}, [
          UI.el("tr", {}, [
            UI.el("th", { text: "Case type" }),
            UI.el("th", { text: "n" }),
            UI.el("th", { text: "Rules baseline" }),
            UI.el("th", { text: "Hybrid (+network)" }),
            UI.el("th", { text: "Measure" }),
          ]),
        ]),
        tbody,
      ])
    );
  }

  function initVerifierTab() {
    var targetSelect = document.getElementById("audit-target-select");
    if (targetSelect) {
      targetSelect.addEventListener("change", verifyCurrentTarget);
    }

    var verifyBtn = document.getElementById("audit-verify-btn");
    if (verifyBtn) {
      verifyBtn.addEventListener("click", verifyCurrentTarget);
    }

    var tamperBtn = document.getElementById("demo-tamper-btn");
    if (tamperBtn) {
      tamperBtn.addEventListener("click", function () {
        var target = targetSelect ? targetSelect.value : "D-PO-0001";
        UI.apiFetch("/demo/tamper", {
          method: "POST",
          jsonBody: { target: target },
        })
          .then(function () {
            return verifyCurrentTarget();
          })
          .catch(function () {
            setOfflineNotice();
          });
      });
    }

    var netoffBtn = document.getElementById("demo-netoff-btn");
    if (netoffBtn) {
      netoffBtn.addEventListener("click", function () {
        UI.apiFetch("/demo/network-off", {
          method: "POST",
          jsonBody: {},
        })
          .then(function () {
            return verifyCurrentTarget();
          })
          .catch(function () {
            setOfflineNotice();
          });
      });
    }

    var restoreBtn = document.getElementById("demo-restore-btn");
    if (restoreBtn) {
      restoreBtn.addEventListener("click", function () {
        var target = targetSelect ? targetSelect.value : "D-PO-0001";
        UI.apiFetch("/demo/tamper", {
          method: "POST",
          jsonBody: { target: target, reset: true },
        })
          .then(function () {
            return UI.apiFetch("/demo/network-off", {
              method: "POST",
              jsonBody: { online: true },
            });
          })
          .then(function () {
            return verifyCurrentTarget();
          })
          .catch(function () {
            verifyCurrentTarget();
          });
      });
    }

    var presetSelect = document.getElementById("breakit-preset-select");
    if (presetSelect) {
      presetSelect.addEventListener("change", function () {
        var v = presetSelect.value;
        if (v === "carousel_5") {
          document.getElementById("breakit-speed-input").value = "410";
          document.getElementById("breakit-retained-input").value = "0.7";
          document.getElementById("breakit-links-input").value = "2";
          document.getElementById("breakit-days-input").value = "2";
          document.getElementById("breakit-transform-select").value = "false";
        } else if (v === "single_link") {
          document.getElementById("breakit-speed-input").value = "55";
          document.getElementById("breakit-retained-input").value = "4.5";
          document.getElementById("breakit-links-input").value = "1";
          document.getElementById("breakit-days-input").value = "10";
          document.getElementById("breakit-transform-select").value = "false";
        } else if (v === "legit_mfg") {
          document.getElementById("breakit-speed-input").value = "44";
          document.getElementById("breakit-retained-input").value = "18.4";
          document.getElementById("breakit-links-input").value = "0";
          document.getElementById("breakit-days-input").value = "24";
          document.getElementById("breakit-transform-select").value = "true";
        }
      });
    }

    var breakitBtn = document.getElementById("run-breakit-btn");
    if (breakitBtn) {
      breakitBtn.addEventListener("click", function () {
        var payload = buildBreakitPayload();
        UI.apiFetch("/breakit", {
          method: "POST",
          jsonBody: payload,
        }).then(function (res) {
          renderBreakitResult(res);
        });
      });
    }

    populateTargets().then(verifyCurrentTarget);
    UI.apiFetch("/breakit", {
      method: "POST",
      jsonBody: buildBreakitPayload(),
    }).then(function (res) {
      renderBreakitResult(res);
    });
  }

  window.TesseraVerifier = {
    init: initVerifierTab,
    verifyCurrentTarget: verifyCurrentTarget,
    renderEvalSummary: renderEvalSummary,
  };
})();
