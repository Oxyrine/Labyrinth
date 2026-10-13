/* Labyrinth workspace: live request inbox, navigation and decision state. */
(function () {
  "use strict";
  var UI = window.LabyrinthUI;
  var cases = [], activeCase = "ring_real_firms", filter = "all", query = "";
  var viewCopy = {
    lender: ["Financing", "Financing workspace", "A clear view of every invoice, every check, and your next decision."],
    seller: ["New invoice", "Start with an invoice", "Bring the invoice and its supporting evidence together."],
    queue: ["Review queue", "A closer look", "Resolve flagged requests with the evidence in front of you."],
    network: ["Network", "The whole market", "Every firm, who invoiced whom, who is related to whom, and the loops that were flagged."],
    verifier: ["Audit & sandbox", "Trust you can trace", "Verify a record, explore its history, or challenge the network check."]
  };
  function text(id, value) { var node = document.getElementById(id); if (node) node.textContent = value; }
  function compactMoney(paise) {
    var rupees = Number(paise || 0) / 100;
    return rupees >= 10000000 ? "₹" + (rupees / 10000000).toFixed(2) + " Cr" : rupees >= 100000 ? "₹" + (rupees / 100000).toFixed(2) + " L" : UI.formatInr(paise);
  }
  function updateStats() {
    text("stat-value", compactMoney(cases.reduce(function (sum,c) {return sum + Number(c.amount_paise || 0);},0)));
    text("stat-total", cases.length);
    text("stat-review", cases.filter(function (c) {return c.outcome === "review";}).length);
    text("stat-approved", cases.filter(function (c) {return c.outcome === "conditionally_approved";}).length);
    text("inbox-count", cases.length);
    text("gs-total", cases.length);
    text("gs-value", compactMoney(cases.reduce(function (sum,c) {return sum + Number(c.amount_paise || 0);},0)));
    text("gs-review", cases.filter(function (c) {return c.outcome === "review";}).length);
  }
  function caseLabel(c) { return String(c.step_label || c.case_id || "Invoice").replace(/^(Step\s+[\da-z]+|Extra)\s*·\s*/i, "").replace(/_/g," "); }
  function renderRequests() {
    var wrap = document.getElementById("request-list");
    if (!wrap) return;
    UI.clear(wrap);
    var visible = cases.filter(function(c) {
      return (filter === "all" || c.outcome === filter) && [c.case_id,c.invoice_no,c.request_id,c.step_label].join(" ").toLowerCase().includes(query);
    });
    if (!visible.length) {
      wrap.appendChild(UI.el("div", {className:"empty-state", text:"No matching requests. Try another search or filter."}));
    } else {
      var body = UI.el("tbody");
      visible.forEach(function(c) {
        var badgeClass = c.outcome === "review" ? "badge-warn" : c.outcome === "reject" ? "badge-fail" : "badge-pass";
        var inspect = UI.el("button", {type:"button", className:"request-open", text:"View", "aria-label":"View invoice " + c.invoice_no, onClick:function() { activeCase=c.case_id; renderRequests(); window.LabyrinthLender.loadCase(c.case_id).then(function(card) { if (!card) return; var target=document.getElementById("selected-invoice-title"); target.tabIndex=-1; target.scrollIntoView({behavior:"instant",block:"start"}); target.focus({preventScroll:true}); }); }});
        body.appendChild(UI.el("tr", {className:c.case_id === activeCase ? "selected-request" : "", "data-case-id":c.case_id},[
          UI.el("td", {}, [UI.el("div", {className:"invoice-cell"}, [UI.el("span", {className:"invoice-icon", "aria-hidden":"true",text:"↗"}),UI.el("div",{},[UI.el("strong",{text:c.invoice_no || c.request_id}),UI.el("small",{text:caseLabel(c)})])])]),
          UI.el("td", {className:"request-amount",text:UI.formatInr(c.amount_paise)}),
          UI.el("td", {},[UI.el("span", {className:"badge " + badgeClass,text:UI.outcomeLabel(c.outcome)})]),
          UI.el("td", {className:"tier-cell",text:String(c.network_tier || "unavailable").replace(/^./,function(s){return s.toUpperCase();})}),
          UI.el("td", {},[inspect])
        ]));
      });
      wrap.appendChild(UI.el("table",{className:"data-table inbox-table"},[
        UI.el("thead",{},[UI.el("tr",{},["Invoice / scenario","Invoice value","Decision","Network tier",""].map(function(label){return UI.el("th",{text:label,scope:"col"});}))]),body
      ]));
    }
    text("request-count-label",visible.length + " of " + cases.length + " requests");
  }
  function updateCases(list) {cases = list.slice(); updateStats(); renderRequests();}
  function updateCard(card) {
    activeCase = card.case_id || activeCase;
    var dec = card.decision || {}, inv = card.invoice || {}, req = card.request || {};
    var found = cases.find(function(c){return c.case_id === activeCase;});
    if (found) {found.outcome = dec.outcome; found.state = dec.state || req.state; found.grade = dec.grade;}
    text("selected-invoice-title", inv.invoice_no || "Invoice details");
    var summary = dec.outcome === "review" ? "This request needs a closer look. Review the flagged evidence before making a financing decision." : dec.outcome === "reject" ? "This request does not meet the current financing requirements. Review the checks for the specific reasons." : "Verification is complete. Buyer acceptance and a confirmed reservation are required before financing.";
    var state = dec.state || req.state || "submitted";
    if (state === "disbursed") summary = "The buyer accepted this invoice and the reservation was confirmed. Demo financing is complete.";
    text("decision-summary", summary);
    var accept = document.getElementById("lender-accept-btn"), confirm = document.getElementById("lender-confirm-btn");
    var accepted = ["buyer_accepted","reservation_confirmed","disbursed","repaid","settled"].includes(state);
    if (accept) {accept.disabled = dec.outcome !== "conditionally_approved" || accepted; accept.textContent = accepted ? "Buyer accepted ✓" : "Record buyer acceptance";}
    if (confirm) {confirm.disabled = !accepted || ["disbursed","repaid","settled"].includes(state); confirm.textContent = state === "disbursed" ? "Financing confirmed ✓" : "Confirm financing →";}
    text("workflow-hint", state === "disbursed" ? "Demo financing complete" : dec.outcome !== "conditionally_approved" ? "Resolve the decision before financing." : accepted ? "Buyer accepted. Ready to confirm financing." : "Next step: record buyer acceptance.");
    updateStats(); renderRequests();
    document.getElementById("workspace-error").classList.add("hidden");
  }
  function setLoading(value) {
    document.getElementById("panel-lender").setAttribute("aria-busy", String(value));
    if (value) {document.getElementById("lender-accept-btn").disabled = true; document.getElementById("lender-confirm-btn").disabled = true; text("workflow-hint","Loading request…");}
  }
  function showError(message) {
    var node=document.getElementById("workspace-error"); node.textContent="Unable to load this request. " + message; node.classList.remove("hidden");
  }
  function switchView(name) {
    window.scrollTo({top:0,behavior:"instant"});
    var copy=viewCopy[name] || viewCopy.lender;
    document.title="Labyrinth — "+copy[0];
    var title=document.getElementById("page-title"); title.replaceChildren(document.createTextNode(copy[1]),UI.el("span",{text:"."}));
    text("page-description",copy[2]);
    document.getElementById("new-invoice-btn").classList.toggle("hidden",name === "seller");
    if (name === "network" && window.LabyrinthNetwork) window.LabyrinthNetwork.show();
  }

  /* The role picker is real: every action belongs to a role, and the server refuses the wrong one. */
  var ROLE_TOKENS = {seller:"seller:P-0001", buyer:"buyer:P-0002", lender:"lender:L1", reviewer:"reviewer:REV-1", auditor:"auditor:AUD-1"};
  var ROLE_NAMES = {seller:"Seller", buyer:"Buyer", lender:"Lender", reviewer:"Reviewer", auditor:"Auditor"};
  function currentRole() { return UI.getActiveRoleToken().split(":")[0]; }
  function applyRole() {
    var role = currentRole(), actor = UI.getActiveRoleToken().split(":")[1] || "";
    document.querySelectorAll("[data-roles]").forEach(function (b) {
      var ok = b.dataset.roles.split(" ").indexOf(role) !== -1;
      b.classList.toggle("role-locked", !ok);
      b.title = ok ? "" : "This is a " + ROLE_NAMES[b.dataset.roles.split(" ")[0]].toLowerCase() + " action. Click to switch role.";
    });
    if (role === "reviewer") {
      var r1 = document.getElementById("override-reviewer1"), r2 = document.getElementById("override-reviewer2");
      if (r1) r1.value = actor; if (r2) r2.value = actor === "REV-1" ? "REV-2" : "REV-1";
    }
    document.getElementById("role-toast").classList.add("hidden");
  }
  function guardRole(e) {
    var b = e.target.closest && e.target.closest("[data-roles]");
    if (!b || b.disabled) return;
    var allowed = b.dataset.roles.split(" ");
    if (allowed.indexOf(currentRole()) !== -1) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var need = allowed[0];
    text("role-toast-text", "You are acting as " + ROLE_NAMES[currentRole()] + ". This step belongs to the " + ROLE_NAMES[need] + ".");
    var sw = document.getElementById("role-toast-switch");
    sw.textContent = "Switch to " + ROLE_NAMES[need] + " and continue";
    sw.onclick = function () {
      document.getElementById("role-switcher").value = ROLE_TOKENS[need];
      applyRole();
      b.click();
    };
    document.getElementById("role-toast").classList.remove("hidden");
  }
  document.addEventListener("DOMContentLoaded",function() {
    document.addEventListener("click",guardRole,true);
    document.getElementById("role-switcher").addEventListener("change",applyRole);
    document.getElementById("role-toast-close").addEventListener("click",function(){document.getElementById("role-toast").classList.add("hidden");});
    applyRole();
    document.getElementById("new-invoice-btn").addEventListener("click",function(){UI.switchTab("seller");});
    document.querySelector(".brand-cluster").addEventListener("click",function(e){e.preventDefault();if(window.LabyrinthLanding)window.LabyrinthLanding.show();else UI.switchTab("lender");});
    document.getElementById("request-search").addEventListener("input",function(e){query=e.target.value.trim().toLowerCase();renderRequests();});
    document.querySelectorAll(".filter-btn").forEach(function(btn){btn.addEventListener("click",function(){filter=btn.dataset.filter;document.querySelectorAll(".filter-btn").forEach(function(b){b.classList.toggle("active",b===btn);b.setAttribute("aria-pressed",String(b===btn));});renderRequests();});});
    var tabs=Array.from(document.querySelectorAll(".tab-btn"));
    tabs.forEach(function(btn,i){btn.tabIndex=btn.classList.contains("active")?0:-1;btn.addEventListener("keydown",function(e){var index=i;if(e.key==="ArrowDown"||e.key==="ArrowRight")index=(i+1)%tabs.length;else if(e.key==="ArrowUp"||e.key==="ArrowLeft")index=(i+tabs.length-1)%tabs.length;else if(e.key==="Home")index=0;else if(e.key==="End")index=tabs.length-1;else return;e.preventDefault();UI.switchTab(tabs[index].dataset.tab);tabs[index].focus();});});
  });
  window.LabyrinthWorkspace={updateCases:updateCases,updateCard:updateCard,switchView:switchView,setLoading:setLoading,showError:showError};
})();
