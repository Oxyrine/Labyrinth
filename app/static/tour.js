/* 6-Step Live Judge Demo Tour & Application Bootstrapper (tour.js) */
(function () {
  "use strict";

  var UI = window.LabyrinthUI;
  var currentStepIndex = 0;

  var TOUR_STEPS = [
    {
      step: 1,
      duration: "90 s",
      title: "Consistent 5-Firm Ring (ring_real_firms)",
      narrative:
        "Every document check passes, so a document-by-document review would approve this. The network check looks across all firms instead: it finds the loop, how little value each firm keeps, whether the same vehicle could have made both trips, and which firms share directors, bank accounts or addresses. The result is a review, not a rejection.",
      quote:
        "Stage Line: \"E-way bills and buyer confirmations in this demo come from deterministic fixtures (source: fixture).\"",
      tab: "lender",
      caseId: "ring_real_firms",
    },
    {
      step: 2,
      duration: "45 s",
      title: "Double Financing & Atomic Concurrency Race",
      narrative:
        "The double-financing case is rejected because another lender already holds the invoice. On the race case, two lenders reserve at the same instant on the real registry (SQLite BEGIN IMMEDIATE plus a partial unique index) and exactly one wins. The settled-invoice case is refused permanently.",
      quote:
        "Stage Line: \"Partial unique index WHERE state IN ('reserved','financed','settled') enforces atomic consortium exclusivity.\"",
      tab: "queue",
      caseId: "concurrent_reservation",
      onActivate: function () {
        if (window.LabyrinthQueue && window.LabyrinthQueue.triggerRace) {
          window.LabyrinthQueue.triggerRace();
        }
      },
    },
    {
      step: 3,
      duration: "30 s",
      title: "Edited Resubmission & Homoglyph Near-Duplicate",
      narrative:
        "Both cases route to review with the evidence shown: the edited resubmission reuses an invoice identity with a different file and amount and no linked credit note; the homoglyph case (INV-O042 against INV-0042) is a near-duplicate. Neither is treated as fraud automatically.",
      quote:
        "Stage Line: \"Both unexplained version mutations and homoglyph resubmissions route to human review with a field-by-field diff.\"",
      tab: "lender",
      caseId: "altered_amount_same_identity",
    },
    {
      step: 4,
      duration: "45 s",
      title: "Clean End-to-End Financing (clean_goods)",
      narrative:
        "Real extraction and the three-way match pass. The grade sets the advance and discount, with the arithmetic shown. Then the buyer's signed acceptance and the registry confirmation are required; only then does the real disbursement invariant check let it finance. Try it from the lender tab.",
      quote:
        "Stage Line: \"The scorecard and bps discount table are illustrative prototype policy, not validated commercial lending prices.\"",
      tab: "lender",
      caseId: "clean_goods",
    },
    {
      step: 5,
      duration: "30 s",
      title: "Tamper Catch & Degraded RPC Independence",
      narrative:
        "Flip 1 byte in encrypted blob D-PO-0001 (POST /demo/tamper) -> Audit Verifier pinpoints Integrity: FAILED naming the exact file (D-PO-0001). Toggle RPC offline (POST /demo/network-off) -> Audit Verifier reports Integrity: VERIFIED · Anchoring: UNAVAILABLE — distinct statuses, never conflated.",
      quote:
        "Stage Line: \"Confidential commercial invoices never touch the chain; only salted RFC-6962 Merkle roots are anchored asynchronously.\"",
      tab: "verifier",
      caseId: "clean_goods",
    },
    {
      step: 6,
      duration: "30 s",
      title: "Synthetic Benchmark & Interactive Judge Break-It",
      narrative:
        "The report shows per-case-type detection and false-review rates on synthetic data, the rules-only baseline against baseline plus network, and the plausible forgeries we miss on purpose. The Break-It box re-runs the real network check on a ring you shape yourself.",
      quote:
        "Stage Line: \"No software proves that physical goods moved if seller, buyer, and transporter collude — Labyrinth raises fabrication cost and routes anomalies to human reviewers with exact numerical evidence.\"",
      tab: "verifier",
      caseId: "legit_circular",
    },
  ];

  function renderTourStep() {
    var s = TOUR_STEPS[currentStepIndex];
    var tag = document.getElementById("tour-step-tag");
    var title = document.getElementById("tour-step-title");
    var narrative = document.getElementById("tour-step-narrative");
    var quote = document.getElementById("tour-step-quote");
    var pillsWrap = document.getElementById("tour-step-pills");

    if (tag) tag.textContent = "STEP " + s.step + " OF 6 (" + s.duration + ")";
    if (title) title.textContent = s.title;
    if (narrative) {
      narrative.textContent = s.narrative;
      if (s.caseId) {
        UI.apiFetch("/requests/" + encodeURIComponent(s.caseId))
          .then(function (card) {
            var checks = ((card.decision && card.decision.checks) || []).filter(function (c) {
              return c.status === "fail" || c.status === "warn" || c.status === "unavailable";
            });
            if (checks.length && TOUR_STEPS[currentStepIndex] === s) {
              narrative.textContent = s.narrative + "  Live evidence (" + checks[0].check + "): " + (checks[0].evidence || [""])[0];
            }
          })
          .catch(function () {});
      }
    }
    if (quote) quote.textContent = s.quote;

    if (pillsWrap) {
      UI.clear(pillsWrap);
      TOUR_STEPS.forEach(function (stepObj, idx) {
        var btn = UI.el("button", {
          type: "button",
          className: "tour-pill-btn" + (idx === currentStepIndex ? " active" : ""),
          text: stepObj.step + ". " + stepObj.title.split("(")[0].trim(),
          onClick: function () {
            currentStepIndex = idx;
            renderTourStep();
            activateTourStep();
          },
        });
        pillsWrap.appendChild(btn);
      });
    }
  }

  function activateTourStep() {
    var s = TOUR_STEPS[currentStepIndex];
    if (window.LabyrinthLender && window.LabyrinthLender.loadCase && s.caseId) {
      window.LabyrinthLender.loadCase(s.caseId);
    }
    UI.switchTab(s.tab);
    if (typeof s.onActivate === "function") {
      s.onActivate();
    }
  }

  function initTourAndNavigation() {
    // Tab click handlers
    var tabBtns = document.querySelectorAll(".tab-btn");
    tabBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var t = btn.getAttribute("data-tab");
        UI.switchTab(t);
      });
    });

    var startBtn = document.getElementById("start-tour-btn");
    var drawer = document.getElementById("tour-drawer");
    var closeBtn = document.getElementById("tour-close-btn");
    var prevBtn = document.getElementById("tour-prev-btn");
    var nextBtn = document.getElementById("tour-next-btn");
    var actionBtn = document.getElementById("tour-action-btn");

    if (startBtn && drawer) {
      startBtn.addEventListener("click", function () {
        drawer.classList.toggle("hidden");
        if (!drawer.classList.contains("hidden")) {
          renderTourStep();
          activateTourStep();
        }
      });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && drawer && !drawer.classList.contains("hidden")) { drawer.classList.add("hidden"); if (startBtn) startBtn.focus(); }
    });
    if (closeBtn && drawer) {
      closeBtn.addEventListener("click", function () {
        drawer.classList.add("hidden");
      });
    }
    if (prevBtn) {
      prevBtn.addEventListener("click", function () {
        currentStepIndex = (currentStepIndex + TOUR_STEPS.length - 1) % TOUR_STEPS.length;
        renderTourStep();
        activateTourStep();
      });
    }
    if (nextBtn) {
      nextBtn.addEventListener("click", function () {
        currentStepIndex = (currentStepIndex + 1) % TOUR_STEPS.length;
        renderTourStep();
        activateTourStep();
      });
    }
    if (actionBtn) {
      actionBtn.addEventListener("click", activateTourStep);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTourAndNavigation();
    if (window.LabyrinthSeller && window.LabyrinthSeller.init) window.LabyrinthSeller.init();
    if (window.LabyrinthLender && window.LabyrinthLender.init) window.LabyrinthLender.init();
    if (window.LabyrinthQueue && window.LabyrinthQueue.init) window.LabyrinthQueue.init();
    if (window.LabyrinthVerifier && window.LabyrinthVerifier.init) window.LabyrinthVerifier.init();
  });
})();
