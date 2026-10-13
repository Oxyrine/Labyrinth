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

    var width = 620;
    var height = 285;
    var cx = width / 2;
    var cy = height / 2 + 4;
    var radius = nodes.length === 2 ? 155 : 104;

    var svg = svgEl("svg", {
      viewBox: "0 0 " + width + " " + height,
      width: "100%",
      height: "285",
      role: "img",
      "aria-label": "Supply chain ring graph with invoice hops and corporate bridges",
    });

    var markerPrefix = container.id || "network";
    // Arrowhead markers
    var defs = svgEl("defs", {}, [
      svgEl("marker", {
        id: markerPrefix + "-invoice",
        viewBox: "0 0 10 10",
        refX: "9",
        refY: "5",
        markerWidth: "6",
        markerHeight: "6",
        orient: "auto-start-reverse",
      }, [
        svgEl("path", { d: "M 0 1 L 10 5 L 0 9 z", fill: "#5F9179" }),
      ]),
      svgEl("marker", {
        id: markerPrefix + "-bridge",
        viewBox: "0 0 10 10",
        refX: "9",
        refY: "5",
        markerWidth: "6",
        markerHeight: "6",
        orient: "auto-start-reverse",
      }, [
        svgEl("path", { d: "M 0 1 L 10 5 L 0 9 z", fill: "#C8912F" }),
      ]),
    ]);
    svg.appendChild(defs);

    var coords = {};
    nodes.forEach(function (n, idx) {
      var angle = -Math.PI / 2 + (2 * Math.PI * idx) / nodes.length;
      if (nodes.length === 2) {
        angle = idx === 0 ? Math.PI : 0;
      }
      coords[n.id] = {
        x: Math.round(cx + radius * Math.cos(angle)),
        y: Math.round(cy + radius * Math.sin(angle)),
        node: n,
      };
    });

    // Draw invoice edges (solid arrows)
    edges.forEach(function (edge) {
      var p1 = coords[edge.from];
      var p2 = coords[edge.to];
      if (!p1 || !p2) return;
      var dx = p2.x - p1.x;
      var dy = p2.y - p1.y;
      var dist = Math.sqrt(dx * dx + dy * dy) || 1;
      var padStart = 28;
      var padEnd = 32;
      var x1 = Math.round(p1.x + (dx / dist) * padStart);
      var y1 = Math.round(p1.y + (dy / dist) * padStart);
      var x2 = Math.round(p2.x - (dx / dist) * padEnd);
      var y2 = Math.round(p2.y - (dy / dist) * padEnd);

      svg.appendChild(
        svgEl("line", {
          x1: x1,
          y1: y1,
          x2: x2,
          y2: y2,
          stroke: "#5F9179",
          "stroke-width": "2.2",
          "marker-end": "url(#" + markerPrefix + "-invoice)",
        })
      );

      var mx = Math.round((x1 + x2) / 2);
      var my = Math.round((y1 + y2) / 2);
      svg.appendChild(
        svgEl("text", {
          x: mx,
          y: my - 5,
          fill: "#9A8F7E",
          "font-size": "10",
          "font-family": "monospace",
          "text-anchor": "middle",
          text: (edge.formatted_amount || formatInr(edge.amount_paise)) + " (" + (edge.hsn || "") + ")",
        })
      );
    });

    // Draw corporate bridges (dashed orange curves with link labels)
    bridges.forEach(function (bridge) {
      var b1 = coords[bridge.from];
      var b2 = coords[bridge.to];
      if (!b1 || !b2) return;
      var midX = Math.round((b1.x + b2.x) / 2);
      var midY = Math.round((b1.y + b2.y) / 2) - 42;
      var pathD = "M " + b1.x + " " + b1.y + " Q " + midX + " " + midY + " " + b2.x + " " + b2.y;
      svg.appendChild(
        svgEl("path", {
          d: pathD,
          fill: "none",
          stroke: "#C8912F",
          "stroke-width": "2.4",
          "stroke-dasharray": "6,4",
          "marker-end": "url(#" + markerPrefix + "-bridge)",
        })
      );
      svg.appendChild(
        svgEl("text", {
          x: midX,
          y: midY + 8,
          fill: "#C8912F",
          "font-size": "10.5",
          "font-weight": "bold",
          "text-anchor": "middle",
          text: "BRIDGE: " + (bridge.label || "Shared Director + Bank Hash"),
        })
      );
    });

    // Draw firm nodes and retained % badges
    nodes.forEach(function (n) {
      var pt = coords[n.id];
      var g = svgEl("g", { transform: "translate(" + pt.x + "," + pt.y + ")" });

      var isAlert = Number(n.retained_pct) < 2.0;
      g.appendChild(
        svgEl("circle", {
          r: "24",
          fill: "#1D1813",
          stroke: isAlert ? "#D65A5A" : "#C9A227",
          "stroke-width": "2.5",
        })
      );
      g.appendChild(
        svgEl("text", {
          x: "0",
          y: "-2",
          fill: "#EFE7D8",
          "font-size": "10.5",
          "font-weight": "bold",
          "text-anchor": "middle",
          text: n.id,
        })
      );
      g.appendChild(
        svgEl("text", {
          x: "0",
          y: "11",
          fill: "#9A8F7E",
          "font-size": "9",
          "text-anchor": "middle",
          text: n.city || "",
        })
      );

      // Retained % badge pill below node
      g.appendChild(
        svgEl("rect", {
          x: "-42",
          y: "27",
          width: "84",
          height: "16",
          rx: "4",
          fill: isAlert ? "#2A1A16" : "#241E12",
          stroke: isAlert ? "#D65A5A" : "#C9A227",
          "stroke-width": "1",
        })
      );
      g.appendChild(
        svgEl("text", {
          x: "0",
          y: "38",
          fill: isAlert ? "#E58A8A" : "#E6C879",
          "font-size": "9.5",
          "font-family": "monospace",
          "text-anchor": "middle",
          text: String(n.retained_pct !== undefined ? n.retained_pct : 1.0) + "% retained",
        })
      );

      svg.appendChild(g);
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
              className: "signal-bar-fill" + (val >= 0.65 ? " high" : ""),
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
