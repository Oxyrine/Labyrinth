/* Tab 3: Reviewer Queue, Concurrent Reservation Race & Audited Overrides (queue.js) */
(function () {
  "use strict";

  var UI = window.TesseraUI;

  function renderQueueTable(items) {
    var wrap = document.getElementById("queue-table-wrap");
    UI.clear(wrap);

    var tbody = UI.el("tbody");
    (items || []).forEach(function (item, idx) {
      var tier = String(item.network_tier || "low").toUpperCase();
      var tierBadgeClass = tier === "HIGH" ? "badge-fail" : (tier === "MEDIUM" ? "badge-warn" : "badge-pass");
      var outBadgeClass =
        item.outcome === "conditionally_approved"
          ? "badge-pass"
          : item.outcome === "review"
          ? "badge-warn"
          : "badge-fail";

      var inspectBtn = UI.el("button", {
        type: "button",
        className: "btn btn-secondary",
        text: "Inspect",
        onClick: function () {
          var ovrSel = document.getElementById("override-case-select");
          if (ovrSel) ovrSel.value = item.case_id;
          if (window.TesseraLender && window.TesseraLender.loadCase) {
            window.TesseraLender.loadCase(item.case_id);
          }
          UI.switchTab("lender");
        },
      });

      tbody.appendChild(
        UI.el("tr", {}, [
          UI.el("td", { text: "#" + (idx + 1) }),
          UI.el("td", { text: item.request_id + " (" + item.case_id + ")" }),
          UI.el("td", {}, [UI.el("span", { className: "badge " + tierBadgeClass, text: tier })]),
          UI.el("td", { text: item.formatted_exposure + " (" + item.exposure_paise + "p)" }),
          UI.el("td", {}, [UI.el("span", { className: "badge " + outBadgeClass, text: item.outcome })]),
          UI.el("td", { text: item.requires_dual_reviewer ? "Dual Reviewer Required" : "Single Reviewer" }),
          UI.el("td", {}, [inspectBtn]),
        ])
      );
    });

    wrap.appendChild(
      UI.el("table", { className: "data-table" }, [
        UI.el("thead", {}, [
          UI.el("tr", {}, [
            UI.el("th", { text: "Rank" }),
            UI.el("th", { text: "Request / Case" }),
            UI.el("th", { text: "Network Tier" }),
            UI.el("th", { text: "Exposure (Paise)" }),
            UI.el("th", { text: "Outcome" }),
            UI.el("th", { text: "Override Policy" }),
            UI.el("th", { text: "Action" }),
          ]),
        ]),
        tbody,
      ])
    );
  }

  function refreshQueue() {
    return UI.apiFetch("/queue?all_cases=true").then(function (data) {
      renderQueueTable(data.all_prioritized || data.queue || []);
      return data;
    });
  }

  function renderRaceResult(res) {
    var box = document.getElementById("race-result-box");
    UI.clear(box);
    var winnerId = (res.winner && res.winner.lender_id) || "L1";
    var winnerResId = (res.winner && res.winner.reservation_id) || "RSV-RACE-L1";
    var loserId = (res.loser && res.loser.lender_id) || "L2";
    var loserReason =
      (res.loser && res.loser.result && res.loser.result.reason) ||
      "held by another participant (atomic unique index lock)";

    box.appendChild(
      UI.el("div", { className: "extracted-grid" }, [
        UI.el("div", { className: "extracted-item" }, [
          UI.el("div", { className: "extracted-item-head" }, [
            UI.el("span", { className: "extracted-key", text: "WINNER THREAD (" + winnerId + ")" }),
            UI.el("span", { className: "badge badge-pass", text: "RESERVED" }),
          ]),
          UI.el("div", { className: "extracted-val", text: winnerResId + " · Active Count = " + res.active_reservation_count }),
        ]),
        UI.el("div", { className: "extracted-item" }, [
          UI.el("div", { className: "extracted-item-head" }, [
            UI.el("span", { className: "extracted-key", text: "LOSER THREAD (" + loserId + ")" }),
            UI.el("span", { className: "badge badge-fail", text: "409 CONFLICT" }),
          ]),
          UI.el("div", { className: "extracted-val", text: loserReason }),
        ]),
      ])
    );
    box.appendChild(
      UI.el("p", {
        className: "helper-copy",
        style: "margin-top:8px;margin-bottom:0;",
        text: res.explanation || "",
      })
    );
  }

  function setOverrideBanner(text, isError) {
    var fb = document.getElementById("override-feedback");
    if (!fb) return;
    fb.classList.remove("hidden", "error", "success");
    fb.classList.add(isError ? "error" : "success");
    fb.textContent = text;
  }

  function initQueueTab() {
    refreshQueue();

    var raceBtn = document.getElementById("trigger-race-btn");
    if (raceBtn) {
      raceBtn.addEventListener("click", function () {
        UI.apiFetch("/reservations/concurrent-demo", {
          method: "POST",
          jsonBody: { case_id: "concurrent_reservation" },
        }).then(function (res) {
          renderRaceResult(res);
        });
      });
    }

    var settledBtn = document.getElementById("inspect-settled-btn");
    if (settledBtn) {
      settledBtn.addEventListener("click", function () {
        if (window.TesseraLender && window.TesseraLender.loadCase) {
          window.TesseraLender.loadCase("refinance_settled");
        }
        UI.switchTab("lender");
      });
    }

    var ovrBtn = document.getElementById("submit-override-btn");
    if (ovrBtn) {
      ovrBtn.addEventListener("click", function () {
        var cid = document.getElementById("override-case-select").value;
        var newOut = document.getElementById("override-outcome-select").value;
        var rev1 = document.getElementById("override-reviewer1").value.trim();
        var rev2 = document.getElementById("override-reviewer2").value.trim();
        var reason = document.getElementById("override-reason").value.trim();

        UI.apiFetch("/overrides", {
          method: "POST",
          roleOverride: "reviewer:" + (rev1 || "REV-1"),
          jsonBody: {
            case_id: cid,
            new_outcome: newOut,
            reviewer_id: rev1 || "REV-1",
            second_reviewer_id: rev2 || null,
            reason: reason,
          },
        })
          .then(function (res) {
            setOverrideBanner(
              "Override " +
                res.override.override_id +
                " recorded for " +
                res.request_id +
                " -> new outcome: " +
                res.decision.outcome.toUpperCase(),
              false
            );
            refreshQueue();
            if (window.TesseraLender && window.TesseraLender.loadCase) {
              window.TesseraLender.loadCase(cid);
            }
          })
          .catch(function (err) {
            setOverrideBanner("Override rejected (" + (err.status || 400) + "): " + err.message, true);
          });
      });
    }
  }

  window.TesseraQueue = {
    init: initQueueTab,
    refreshQueue: refreshQueue,
    triggerRace: function () {
      return UI.apiFetch("/reservations/concurrent-demo", {
        method: "POST",
        jsonBody: { case_id: "concurrent_reservation" },
      }).then(function (res) {
        renderRaceResult(res);
        return res;
      });
    },
  };
})();
