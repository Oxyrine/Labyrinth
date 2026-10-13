/* Tab 2: Lender Explanation Card & Stage Check Inspector (lender.js) */
(function () {
  "use strict";

  var UI = window.TesseraUI;
  var currentCaseId = "ring_real_firms";
  var currentCard = null;

  function renderExplanationCard(card) {
    if (!card || !card.decision) return;
    currentCard = card;
    currentCaseId = card.case_id || currentCaseId;

    var dec = card.decision;
    var inv = card.invoice || {};
    var req = card.request || {};
    var ver = card.version || {};

    UI.updateGlobalPropertyBanner(card.three_properties);

    var reqPill = document.getElementById("lender-req-id-pill");
    if (reqPill) reqPill.textContent = req.request_id + " · " + inv.invoice_no;

    // Outcome badge
    var outBadge = document.getElementById("lender-outcome-badge");
    if (outBadge) {
      outBadge.textContent = String(dec.outcome || "").replace(/_/g, " ").toUpperCase();
      outBadge.className = "outcome-badge";
      if (dec.outcome === "conditionally_approved") {
        outBadge.classList.add("outcome-approved");
      } else if (dec.outcome === "review") {
        outBadge.classList.add("outcome-review");
      } else {
        outBadge.classList.add("outcome-reject");
      }
    }

    var stateBadge = document.getElementById("lender-state-badge");
    if (stateBadge) {
      stateBadge.textContent = "STATE: " + String(dec.state || req.state || "submitted").toUpperCase();
    }

    var gradeLetter = document.getElementById("lender-grade-letter");
    var gradePoints = document.getElementById("lender-grade-points");
    if (gradeLetter) gradeLetter.textContent = dec.grade || "B";
    if (gradePoints) gradePoints.textContent = (dec.score_points || 0) + " / 100 pts";

    var tierBadge = document.getElementById("lender-tier-badge");
    if (tierBadge) {
      var tier = String(dec.network_tier || "low").toUpperCase();
      tierBadge.textContent = "NETWORK TIER: " + tier;
      tierBadge.className = "badge " + (tier === "HIGH" ? "badge-fail" : (tier === "MEDIUM" ? "badge-warn" : "badge-pass"));
    }

    // Meta strip
    var metaStrip = document.getElementById("lender-invoice-summary");
    UI.clear(metaStrip);
    var sellerParty = (card.parties && card.parties[req.seller_id]) || {};
    var buyerParty = (card.parties && card.parties[req.buyer_id]) || {};
    var metaItems = [
      { label: "Seller", val: (sellerParty.name || req.seller_id) + " (" + inv.seller_gstin + ")" },
      { label: "Buyer", val: (buyerParty.name || req.buyer_id) + " (" + inv.buyer_gstin + ")" },
      { label: "Invoice Grand Total", val: UI.formatInr(inv.grand_total_paise) },
      { label: "Tenor & Dates", val: (req.tenor_days || 60) + "d · " + inv.invoice_date + " -> " + inv.due_date },
    ];
    metaItems.forEach(function (m) {
      metaStrip.appendChild(
        UI.el("div", {}, [
          UI.el("div", { className: "meta-cell-label", text: m.label }),
          UI.el("div", { className: "meta-cell-val", text: m.val }),
        ])
      );
    });

    // Top reasons & conditions
    var reasonsList = document.getElementById("lender-top-reasons");
    UI.clear(reasonsList);
    (dec.top_reasons || []).forEach(function (r) {
      reasonsList.appendChild(UI.el("li", { text: r }));
    });
    if ((dec.overridden_by || []).length > 0) {
      var lastOvr = dec.overridden_by[dec.overridden_by.length - 1];
      reasonsList.appendChild(
        UI.el("li", {
          text:
            "OVERRIDE APPLIED by " +
            lastOvr.reviewer_id +
            (lastOvr.second_reviewer_id ? " + " + lastOvr.second_reviewer_id : "") +
            ": " +
            lastOvr.reason,
        })
      );
    }

    var condRow = document.getElementById("lender-conditions");
    UI.clear(condRow);
    (dec.conditions || []).forEach(function (c) {
      condRow.appendChild(UI.el("span", { className: "badge badge-warn", text: "CONDITION: " + c }));
    });

    // Pricing box
    var pricingBody = document.getElementById("lender-pricing-body");
    var policyNotice = document.getElementById("lender-policy-notice");
    UI.clear(pricingBody);
    var quote = dec.pricing || card.pricing;
    if (quote) {
      var statsGrid = UI.el("div", { className: "pricing-metrics-grid" }, [
        UI.el("div", { className: "pricing-stat" }, [
          UI.el("div", { className: "pricing-stat-label", text: "Advance Rate (" + quote.advance_rate_bps + " bps)" }),
          UI.el("div", { className: "pricing-stat-val", text: (quote.advance_rate_bps / 100) + "% (" + UI.formatInr(quote.advance_amount_paise) + ")" }),
        ]),
        UI.el("div", { className: "pricing-stat" }, [
          UI.el("div", { className: "pricing-stat-label", text: "Annual Discount Rate (" + quote.annual_rate_bps + " bps)" }),
          UI.el("div", { className: "pricing-stat-val", text: (quote.annual_rate_bps / 100) + "% p.a. for " + quote.tenor_days + "d" }),
        ]),
        UI.el("div", { className: "pricing-stat" }, [
          UI.el("div", { className: "pricing-stat-label", text: "Discount Fee (paise: " + quote.discount_paise + ")" }),
          UI.el("div", { className: "pricing-stat-val", text: UI.formatInr(quote.discount_paise) }),
        ]),
        UI.el("div", { className: "pricing-stat" }, [
          UI.el("div", { className: "pricing-stat-label", text: "Net Disbursement to Seller" }),
          UI.el("div", { className: "pricing-stat-val", text: UI.formatInr(quote.net_disbursement_paise) }),
        ]),
      ]);
      pricingBody.appendChild(statsGrid);
      pricingBody.appendChild(
        UI.el("div", {
          className: "arithmetic-trace-box",
          text: quote.arithmetic_trace,
        })
      );
      if (policyNotice) {
        policyNotice.textContent = quote.policy_notice || "Illustrative prototype policy, not validated lending prices.";
      }
    } else {
      pricingBody.appendChild(
        UI.el("div", {
          className: "status-banner error",
          text: "Grade E Decline — No financing quote issued for rejected request.",
        })
      );
    }

    // Ring Graph
    var ringBadge = document.getElementById("ring-summary-badge");
    var rg = card.ring_graph || {};
    if (ringBadge) {
      if (rg.ring_id) {
        ringBadge.textContent = rg.ring_id + " · SCORE " + Number(rg.score || 0).toFixed(2) + " (" + String(rg.tier || "low").toUpperCase() + ")";
        ringBadge.className = "badge " + (rg.tier === "high" ? "badge-fail" : "badge-warn");
      } else {
        ringBadge.textContent = "ACYCLIC · SCORE " + Number(rg.score || 0.02).toFixed(2);
        ringBadge.className = "badge badge-pass";
      }
    }
    UI.renderRingSvg(
      document.getElementById("ring-svg-container"),
      rg,
      document.getElementById("ring-signals-bars")
    );

    // Version Diff & Evidence Table
    var verBadge = document.getElementById("lender-version-kind-badge");
    if (verBadge) {
      verBadge.textContent = String(ver.kind || "initial").toUpperCase() + " (v" + (ver.version_no || 1) + ")";
      verBadge.className = "badge " + (ver.kind === "unexplained_change" ? "badge-warn" : "badge-info");
    }

    var diffWrap = document.getElementById("lender-version-diff-wrap");
    UI.clear(diffWrap);
    var diffs = card.version_diff || [];
    if (diffs.length > 0) {
      var diffTbody = UI.el("tbody");
      diffs.forEach(function (d) {
        diffTbody.appendChild(
          UI.el("tr", {}, [
            UI.el("td", { text: d.field }),
            UI.el("td", { text: d.prior }),
            UI.el("td", { text: d.current }),
            UI.el("td", { text: d.delta }),
          ])
        );
      });
      diffWrap.appendChild(
        UI.el("table", { className: "data-table" }, [
          UI.el("thead", {}, [
            UI.el("tr", {}, [
              UI.el("th", { text: "Field / Dimension" }),
              UI.el("th", { text: "Prior Version / Match" }),
              UI.el("th", { text: "Current Submission" }),
              UI.el("th", { text: "Delta / Anomaly" }),
            ]),
          ]),
          diffTbody,
        ])
      );
    } else {
      diffWrap.appendChild(
        UI.el("div", {
          className: "helper-copy",
          text: "HMAC Identity " + String(ver.invoice_identity || "").slice(0, 20) + "… has no prior version discrepancies.",
        })
      );
    }

    var evWrap = document.getElementById("lender-evidence-table-wrap");
    UI.clear(evWrap);
    var evTbody = UI.el("tbody");
    (card.evidence || []).forEach(function (ev) {
      evTbody.appendChild(
        UI.el("tr", {}, [
          UI.el("td", { text: ev.doc_id }),
          UI.el("td", { text: ev.doc_type }),
          UI.el("td", { text: ev.doc_no }),
          UI.el("td", { text: ev.source }),
          UI.el("td", { text: ev.verification_status }),
          UI.el("td", { text: Number(ev.evidence_weight || 1.0).toFixed(1) }),
        ])
      );
    });
    evWrap.appendChild(
      UI.el("table", { className: "data-table" }, [
        UI.el("thead", {}, [
          UI.el("tr", {}, [
            UI.el("th", { text: "Doc ID" }),
            UI.el("th", { text: "Type" }),
            UI.el("th", { text: "Document No" }),
            UI.el("th", { text: "Source" }),
            UI.el("th", { text: "Verification" }),
            UI.el("th", { text: "Weight" }),
          ]),
        ]),
        evTbody,
      ])
    );

    // Stage-by-Stage Checks
    var checksContainer = document.getElementById("lender-checks-container");
    UI.clear(checksContainer);
    var stageMeta = [
      { key: "extract", title: "Stage 1 · Document Extraction & Arithmetic (extract.*)" },
      { key: "match", title: "Stage 2a · Three-Way Cross-Document Matching & E-Way Bill (match.*)" },
      { key: "dedup", title: "Stage 2b · Identity Versioning, Near-Duplicate & Consortium Registry (dedup.*)" },
      { key: "network", title: "Stage 2c · Supply-Chain Network & Ring Topology (network.*)" },
      { key: "risk", title: "Stage 3 · Buyer Payment History & Risk Scorecard (risk.*)" },
    ];

    var grouped = card.checks_by_stage || {};
    stageMeta.forEach(function (stg) {
      var list = grouped[stg.key] || [];
      if (list.length === 0) return;

      var latencyMs = (dec.stage_latencies_ms && dec.stage_latencies_ms[stg.key]) || 8;
      var groupDiv = UI.el("div", { className: "stage-group" }, [
        UI.el("div", { className: "stage-group-header" }, [
          UI.el("span", { text: stg.title }),
          UI.el("span", { className: "mono-id", text: list.length + " checks · " + latencyMs + " ms" }),
        ]),
      ]);

      list.forEach(function (chk) {
        var src = chk.source || "live";
        var row = UI.el("div", { className: "check-row" }, [
          UI.el("div", { className: "check-id-mono", text: chk.check }),
          UI.el("div", {}, [
            UI.el("span", { className: "badge badge-" + chk.status, text: chk.status }),
          ]),
          UI.el("div", {}, [
            UI.el("span", {
              className: "source-chip" + (src === "fixture" ? " fixture" : ""),
              text: "source: " + src + " · w=" + Number(chk.evidence_weight || 1.0).toFixed(1),
            }),
          ]),
          UI.el("div", {
            className: "check-evidence-text",
            text: (chk.evidence || []).join(" "),
          }),
        ]);
        groupDiv.appendChild(row);
      });

      checksContainer.appendChild(groupDiv);
    });
  }

  function setLenderFeedback(text, isError) {
    var box = document.getElementById("lender-action-feedback");
    if (!box) return;
    box.classList.remove("hidden", "error", "success");
    box.classList.add(isError ? "error" : "success");
    box.textContent = text;
  }

  function loadCase(caseId) {
    currentCaseId = caseId;
    var selects = [
      document.getElementById("lender-case-select"),
      document.getElementById("seller-fixture-select"),
      document.getElementById("override-case-select"),
    ];
    selects.forEach(function (s) {
      if (s && s.value !== caseId) s.value = caseId;
    });

    return UI.apiFetch("/requests/" + encodeURIComponent(caseId)).then(function (card) {
      renderExplanationCard(card);
      if (window.TesseraSeller && window.TesseraSeller.renderPreview) {
        window.TesseraSeller.renderPreview(card);
      }
      return card;
    });
  }

  function populateCaseSelectors(casesList) {
    var selects = [
      document.getElementById("lender-case-select"),
      document.getElementById("seller-fixture-select"),
      document.getElementById("override-case-select"),
    ];
    selects.forEach(function (sel) {
      if (!sel) return;
      UI.clear(sel);
      casesList.forEach(function (c) {
        sel.appendChild(
          UI.el("option", {
            value: c.case_id,
            text: (c.step_label || c.case_id) + " — [" + String(c.outcome).toUpperCase() + "]",
          })
        );
      });
      sel.value = currentCaseId;
    });
  }

  function initLenderTab() {
    var caseSelect = document.getElementById("lender-case-select");
    if (caseSelect) {
      caseSelect.addEventListener("change", function () {
        var box = document.getElementById("lender-action-feedback");
        if (box) box.classList.add("hidden");
        loadCase(caseSelect.value);
      });
    }

    var acceptBtn = document.getElementById("lender-accept-btn");
    if (acceptBtn) {
      acceptBtn.addEventListener("click", function () {
        var reqId = (currentCard && currentCard.request_id) || currentCaseId;
        UI.apiFetch("/requests/" + encodeURIComponent(reqId) + "/accept", {
          method: "POST",
          roleOverride: "buyer:P-0002",
          jsonBody: {
            decision: "accept",
          },
        })
          .then(function (res) {
            if (res.card) renderExplanationCard(res.card);
            setLenderFeedback(
              "BuyerAcceptance signed (demo buyer key) and verified -> Request state is now BUYER_ACCEPTED.",
              false
            );
          })
          .catch(function (err) {
            setLenderFeedback("Buyer acceptance failed: " + err.message, true);
          });
      });
    }

    var confirmBtn = document.getElementById("lender-confirm-btn");
    if (confirmBtn) {
      confirmBtn.addEventListener("click", function () {
        var reqId = (currentCard && currentCard.request_id) || currentCaseId;
        UI.apiFetch("/reservations/confirm", {
          method: "POST",
          roleOverride: "lender:L1",
          jsonBody: {
            request_id: reqId,
            lender_id: "L1",
            disburse: true,
          },
        })
          .then(function (res) {
            if (res.card) renderExplanationCard(res.card);
            setLenderFeedback(
              "Consortium reservation confirmed (state: financed) and net proceeds disbursed -> Request state: " +
                String(res.state || "disbursed").toUpperCase() +
                ".",
              false
            );
          })
          .catch(function (err) {
            setLenderFeedback("Reservation confirmation error: " + err.message, true);
          });
      });
    }

    UI.apiFetch("/cases").then(function (data) {
      populateCaseSelectors(data.cases || []);
      loadCase(currentCaseId);
      if (window.TesseraVerifier && window.TesseraVerifier.renderEvalSummary && data.eval_summary) {
        window.TesseraVerifier.renderEvalSummary(data.eval_summary);
      }
    });
  }

  window.TesseraLender = {
    init: initLenderTab,
    loadCase: loadCase,
    renderExplanationCard: renderExplanationCard,
  };
})();
