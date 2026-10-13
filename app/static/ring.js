/* Labyrinth SVG Ring Visualizer & Shared Safe DOM Helpers (ring.js) */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";

  function clear(node) {
    if (!node) return;
    node.replaceChildren();
  }

  function el(tag, attrs, children) {
    var element = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var val = attrs[key];
        if (val === undefined || val === null) return;
        if (key === "className") {
          element.className = String(val);
        } else if (key === "text") {
          element.textContent = String(val);
        } else if (key.indexOf("on") === 0 && typeof val === "function") {
          element.addEventListener(key.slice(2).toLowerCase(), val);
        } else {
          element.setAttribute(key, String(val));
        }
      });
    }
    if (Array.isArray(children)) {
      children.forEach(function (child) {
        if (child === null || child === undefined) return;
        if (typeof child === "string" || typeof child === "number") {
          element.appendChild(document.createTextNode(String(child)));
        } else {
          element.appendChild(child);
        }
      });
    }
    return element;
  }

  function svgEl(tag, attrs, children) {
    var element = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var val = attrs[key];
        if (val === undefined || val === null) return;
        if (key === "text") {
          element.textContent = String(val);
        } else {
          element.setAttribute(key, String(val));
        }
      });
    }
    if (Array.isArray(children)) {
      children.forEach(function (child) {
        if (child) element.appendChild(child);
      });
    }
    return element;
  }

  function formatInr(paise) {
    var num = Number(paise || 0);
    var sign = num < 0 ? "-" : "";
    var absPaise = Math.abs(Math.round(num));
    var rupees = Math.floor(absPaise / 100);
    var rem = absPaise % 100;
    var s = String(rupees);
    var formatted = s;
    if (s.length > 3) {
      var last3 = s.slice(-3);
      var rest = s.slice(0, -3);
      var groups = [];
      while (rest.length > 2) {
        groups.unshift(rest.slice(-2));
        rest = rest.slice(0, -2);
      }
      if (rest.length > 0) groups.unshift(rest);
      formatted = groups.join(",") + "," + last3;
    }
    if (rem > 0) {
      var remStr = rem < 10 ? "0" + rem : String(rem);
      return sign + "\u20B9" + formatted + "." + remStr;
    }
    return sign + "\u20B9" + formatted;
  }

  function getActiveRoleToken() {
    var switcher = document.getElementById("role-switcher");
    return switcher ? switcher.value : "lender:L1";
  }

  function setConnectionStatus(isLive) {
    var dot = document.getElementById("connection-dot");
    var txt = document.getElementById("connection-text");
    if (!dot || !txt) return;
    if (isLive) {
      dot.classList.remove("offline");
      txt.textContent = "Live connection";
    } else {
      dot.classList.add("offline");
      txt.textContent = "Offline snapshot";
    }
  }

  function apiFetch(path, options) {
    var opts = options || {};
    var headers = Object.assign(
      {
        "X-Labyrinth-Role": opts.roleOverride || getActiveRoleToken(),
        "Authorization": "Bearer " + (opts.roleOverride || getActiveRoleToken()),
      },
      opts.headers || {}
    );
    if (opts.jsonBody !== undefined) {
      headers["Content-Type"] = "application/json";
    }

    if (window.location.protocol === "file:") {
      setConnectionStatus(false);
      return Promise.resolve(offlineFallback(path, opts));
    }

    return fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.jsonBody !== undefined ? JSON.stringify(opts.jsonBody) : opts.body,
    })
      .then(function (resp) {
        setConnectionStatus(true);
        return resp.json().then(function (data) {
          if (!resp.ok) {
            var err = new Error(data.detail || data.reason || ("HTTP " + resp.status));
            err.status = resp.status;
            err.payload = data;
            throw err;
          }
          return data;
        });
      })
      .catch(function (err) {
        if (err && err.status) {
          throw err;
        }
        setConnectionStatus(false);
        return offlineFallback(path, opts);
      });
  }

  function offlineError(msg) {
    var e = new Error(msg);
    e.status = 503;
    return e;
  }

  function offlineFallback(path, opts) {
    var store = window.LABYRINTH_OFFLINE_DATA || { cases: {}, case_order: [], queue: [], eval_summary: {} };
    if (path === "/network") return store.market || { available: false, message: "The market map needs the offline snapshot rebuilt." };
    if (path === "/cases") {
      var list = (store.case_order || []).map(function (cid) {
        var c = store.cases[cid];
        return {
          case_id: cid,
          step_number: c.step_number,
          step_label: c.step_label,
          description: c.description,
          request_id: c.request_id,
          invoice_id: c.invoice_id,
          invoice_no: c.invoice.invoice_no,
          seller_id: c.request.seller_id,
          Buyer_id: c.request.buyer_id,
          amount_paise: c.invoice.grand_total_paise,
          formatted_amount: formatInr(c.invoice.grand_total_paise),
          outcome: c.decision.outcome,
          state: c.decision.state,
          grade: c.decision.grade,
          score_points: c.decision.score_points,
          network_tier: c.decision.network_tier,
          three_properties: c.three_properties,
        };
      });
      return { cases: list, total: list.length, demo_mode: true, eval_summary: store.eval_summary };
    }
    if (path.indexOf("/requests/") === 0 && path.indexOf("/accept") === -1) {
      var id = decodeURIComponent(path.replace("/requests/", ""));
      var found = store.cases[id];
      if (!found) {
        Object.keys(store.cases).forEach(function (k) {
          if (store.cases[k].request_id === id || store.cases[k].invoice_id === id) {
            found = store.cases[k];
          }
        });
      }
      return found || store.cases.clean_goods;
    }
    if (path.indexOf("/accept") !== -1 || path.indexOf("/reservations/") === 0 || path.indexOf("/overrides") === 0) {
      throw offlineError("Actions need the live server; this is a read-only snapshot.");
    }
    if (path.indexOf("/queue") === 0) {
      return { sort_order: "(tier, exposure_paise)", queue: store.queue || [], all_prioritized: store.queue || [] };
    }
    if (path.indexOf("/audit/verify/") === 0) {
      var t = decodeURIComponent(path.replace("/audit/verify/", ""));
      var baseCase = store.cases[t] || store.cases.clean_goods;
      Object.keys(store.cases).forEach(function (k) {
        var blobs = store.cases[k].audit_proof.encrypted_blobs || [];
        blobs.forEach(function (b) {
          if (b.doc_id === t) baseCase = store.cases[k];
        });
      });
      return baseCase.audit_proof;
    }
    if (path === "/breakit") {
      throw offlineError("Break-It runs the real network check and needs the live server.");
    }
    return store.cases.clean_goods;
  }

  function switchTab(tabName) {
    var btns = document.querySelectorAll(".tab-btn");
    btns.forEach(function (b) {
      var active = b.getAttribute("data-tab") === tabName;
      b.classList.toggle("active", active);
      b.setAttribute("aria-selected", active ? "true" : "false");
      b.tabIndex = active ? 0 : -1;
    });
    if (window.LabyrinthWorkspace) window.LabyrinthWorkspace.switchView(tabName);
    var panels = {
      seller: document.getElementById("panel-seller"),
      lender: document.getElementById("panel-lender"),
      queue: document.getElementById("panel-queue"),
      network: document.getElementById("panel-network"),
      verifier: document.getElementById("panel-verifier"),
    };
    Object.keys(panels).forEach(function (k) {
      if (panels[k]) {
        panels[k].classList.toggle("hidden", k !== tabName);
      }
    });
  }

  function updateGlobalPropertyBanner(threeProps, proof) {
    if (!threeProps) return;
    var bCons = document.getElementById("banner-badge-consistency");
    var bInt = document.getElementById("banner-badge-integrity");
    var bAuth = document.getElementById("banner-badge-authenticity");

    if (bCons && threeProps.consistency) {
      bCons.textContent = (threeProps.consistency.status === "pass" ? "Checks consistent" : "Needs attention");
      bCons.className = "badge badge-" + (threeProps.consistency.status || "pass");
    }
    if (bInt && threeProps.integrity) {
      bInt.textContent = threeProps.integrity.badge || "Awaiting verification";
      bInt.className = "badge badge-" + (threeProps.integrity.status || "pass");
      if (proof) bInt.textContent = String(proof.integrity || "unavailable") + " · " + (proof.anchor_mode === "simulated" ? "simulated anchor " : "anchor ") + String(proof.anchoring || "pending");
    }
    if (bAuth && threeProps.authenticity) {
      bAuth.textContent = (threeProps.authenticity.status === "pass" ? "No elevated signal" : "Review suggested");
      bAuth.className = "badge badge-" + (threeProps.authenticity.status || "pass");
    }
  }

  function renderRingSvg(container, ringGraph, signalBarsContainer) {
    clear(container);
    if (signalBarsContainer) clear(signalBarsContainer);

    var nodes = (ringGraph && ringGraph.nodes) || [];
    var edges = (ringGraph && ringGraph.invoice_edges) || [];
    var bridges = (ringGraph && ringGraph.corporate_bridges) || [];

    if (nodes.length === 0) {
      var emptyMsg = el("div", {
        className: "helper-copy",
        text: "Acyclic bipartite trade (no circular ring topology or related-party closure bridge detected).",
      });
      container.appendChild(emptyMsg);
      return;
    }

    // Quiet by design: circles and arrows only. Names, amounts and links appear on hover, not on the page.
    var width = 620;
    var height = 270;
    var cx = width / 2;
    var cy = height / 2 - 6;
    var radius = nodes.length === 2 ? 170 : 92;
    var flaggedLoop = !ringGraph || ringGraph.tier !== "low";

    var svg = svgEl("svg", {
      viewBox: "0 0 " + width + " " + height,
      width: "100%",
      height: String(height),
      role: "img",
      "aria-label": "Trade loop between " + nodes.length + " firms. Hover a firm or a line for details.",
      class: "rg",
    });

    var markerPrefix = container.id || "network";
    svg.appendChild(svgEl("defs", {}, [
      svgEl("marker", { id: markerPrefix + "-arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" }, [
        svgEl("path", { d: "M 0 1 L 10 5 L 0 9 z", class: flaggedLoop ? "rg-arrow flagged" : "rg-arrow" }),
      ]),
    ]));

    var coords = {};
    nodes.forEach(function (n, idx) {
      var angle = -Math.PI / 2 + (2 * Math.PI * idx) / nodes.length;
      if (nodes.length === 2) angle = idx === 0 ? Math.PI : 0;
      coords[n.id] = { x: Math.round(cx + radius * Math.cos(angle)), y: Math.round(cy + radius * Math.sin(angle)), node: n };
    });

    function tip(node, text) { var t = svgEl("title", {}); t.textContent = text; node.appendChild(t); return node; }

    edges.forEach(function (edge) {
      var p1 = coords[edge.from], p2 = coords[edge.to];
      if (!p1 || !p2) return;
      var dx = p2.x - p1.x, dy = p2.y - p1.y, dist = Math.sqrt(dx * dx + dy * dy) || 1;
      var line = svgEl("line", {
        x1: Math.round(p1.x + (dx / dist) * 26), y1: Math.round(p1.y + (dy / dist) * 26),
        x2: Math.round(p2.x - (dx / dist) * 30), y2: Math.round(p2.y - (dy / dist) * 30),
        class: "rg-edge" + (flaggedLoop ? " flagged" : ""), "marker-end": "url(#" + markerPrefix + "-arrow)",
      });
      svg.appendChild(tip(line, p1.node.id + " invoiced " + p2.node.id + ": " + (edge.formatted_amount || formatInr(edge.amount_paise)) + (edge.hsn ? " (HSN " + edge.hsn + ")" : "")));
    });

    bridges.forEach(function (bridge) {
      var b1 = coords[bridge.from], b2 = coords[bridge.to];
      if (!b1 || !b2) return;
      var mx = Math.round((b1.x + b2.x) / 2), my = Math.round((b1.y + b2.y) / 2) - 34;
      var path = svgEl("path", { d: "M " + b1.x + " " + b1.y + " Q " + mx + " " + my + " " + b2.x + " " + b2.y, class: "rg-bridge" });
      svg.appendChild(tip(path, b1.node.id + " and " + b2.node.id + " are related: " + ((bridge.links || []).join("; ") || bridge.label || "shared details")));
    });

    nodes.forEach(function (n) {
      var pt = coords[n.id];
      var alert = flaggedLoop && Number(n.retained_pct) < 2.0;
      var g = svgEl("g", { transform: "translate(" + pt.x + "," + pt.y + ")", class: "rg-node" + (alert ? " alert" : "") });
      g.appendChild(svgEl("circle", { r: "22", class: "rg-dot" }));
      var id = svgEl("text", { y: "4", "text-anchor": "middle", class: "rg-id" }); id.textContent = n.id; g.appendChild(id);
      if (n.retained_pct !== undefined) {
        var kept = svgEl("text", { y: "40", "text-anchor": "middle", class: "rg-kept" }); kept.textContent = n.retained_pct + "% kept"; g.appendChild(kept);
      }
      svg.appendChild(tip(g, (n.label || n.id) + (n.city ? " · " + n.city : "") + (n.retained_pct !== undefined ? "\nKeeps " + n.retained_pct + "% of the value passing through" : "")));
    });

    container.appendChild(svg);

    // Render 8-signal breakdown bars if signalBarsContainer is provided
    var signals = (ringGraph && ringGraph.signals) || {};
    if (signalBarsContainer && Object.keys(signals).length > 0) {
      Object.keys(signals).forEach(function (sigKey) {
        var val = Number(signals[sigKey] || 0);
        var pct = Math.min(100, Math.round(val * 100));
        var card = el("div", { className: "signal-Mini-card" }, [
          el("div", { className: "signal-name-row" }, [
            el("span", { text: ({s_flow:"Value flow",s_product:"Product",s_timing:"Timing",s_isolation:"Isolation",s_movement:"Movement",s_related:"Related parties",s_transform:"Transformation",s_rapid:"Velocity"})[sigKey] || sigKey }),
            el("span", { text: val.toFixed(2) }),
          ]),
          el("div", { className: "signal-bar-track" }, [
            el("div", {
              className: "signal-bar-fill" + (val >= 0.85 ? " high" : ""),
              style: "width:" + pct + "%",
            }),
          ]),
        ]);
        signalBarsContainer.appendChild(card);
      });
    }
  }

  window.LabyrinthUI = {
    outcomeLabel: function (value) { return ({conditionally_approved:"Conditional approval",review:"Needs review",reject:"Declined"})[value] || value || "Awaiting decision"; },
    el: el,
    svgEl: svgEl,
    clear: clear,
    formatInr: formatInr,
    apiFetch: apiFetch,
    switchTab: switchTab,
    updateGlobalPropertyBanner: updateGlobalPropertyBanner,
    renderRingSvg: renderRingSvg,
    getActiveRoleToken: getActiveRoleToken,
  };
})();
