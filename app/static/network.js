/* Network view: every firm in the generated market, who invoiced whom, which firms are related, and the loops the check flagged. */
(function () {
  "use strict";

  var UI = window.LabyrinthUI;
  var NS = "http://www.w3.org/2000/svg";
  var W = 1000, H = 640;
  var COLORS = { manufacturing: "#C8912F", trading: "#9A86B0", distribution: "#5F9179", services: "#9A8F7E" };
  var data = null, pos = {}, byId = {}, ringById = {}, view = { x: 0, y: 0, w: W, h: H };
  var state = { trade: true, related: true, flaggedOnly: false, ring: "", industry: "all", selected: null };
  var loaded = false;

  function $(id) { return document.getElementById(id); }
  function svg(tag, attrs, kids) {
    var n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    (kids || []).forEach(function (c) { n.appendChild(c); });
    return n;
  }
  function inr(p) { return UI.formatInr(p); }

  /* ───── layout: a small force simulation, seeded so the picture is the same every time ───── */
  function seeded(id) {
    var h = 2166136261;
    for (var i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
    return function () { h ^= h << 13; h ^= h >>> 17; h ^= h << 5; return ((h >>> 0) % 10000) / 10000; };
  }
  function layout() {
    var ids = data.nodes.map(function (n) { return n.id; });
    ids.forEach(function (id) { var r = seeded(id); pos[id] = { x: 80 + r() * (W - 160), y: 60 + r() * (H - 120), vx: 0, vy: 0 }; });
    var links = data.trade_edges.map(function (e) { return [e.from, e.to, 1]; })
      .concat(data.related_edges.map(function (e) { return [e.from, e.to, 1.6]; }));
    var k = Math.sqrt((W * H) / ids.length) * 0.62;
    for (var it = 0; it < 260; it++) {
      var cool = 1 - it / 260;
      for (var i = 0; i < ids.length; i++) {
        var a = pos[ids[i]];
        for (var j = i + 1; j < ids.length; j++) {
          var b = pos[ids[j]], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy + 0.01, d = Math.sqrt(d2);
          if (d > k * 3) continue;
          var f = (k * k) / d2;
          a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
        }
        a.vx += (W / 2 - a.x) * 0.01; a.vy += (H / 2 - a.y) * 0.01;
      }
      links.forEach(function (l) {
        var a = pos[l[0]], b = pos[l[1]]; if (!a || !b) return;
        var dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01, f = (d / k) * 0.18 * l[2];
        a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
      });
      ids.forEach(function (id) {
        var p = pos[id], sp = Math.sqrt(p.vx * p.vx + p.vy * p.vy) + 0.01, cap = 14 * cool + 1;
        p.x += (p.vx / sp) * Math.min(sp, cap); p.y += (p.vy / sp) * Math.min(sp, cap);
        p.x = Math.max(30, Math.min(W - 30, p.x)); p.y = Math.max(30, Math.min(H - 30, p.y)); p.vx = p.vy = 0;
      });
    }
  }

  /* ───── drawing ───── */
  function activeMembers() {
    if (state.ring) return ringById[state.ring].members;
    return null;
  }
  function visible(n) {
    if (state.flaggedOnly && !n.rings.length) return false;
    return true;
  }
  function draw() {
    var root = $("network-svg");
    UI.clear(root);
    var focus = activeMembers(), sel = state.selected;
    var near = {};
    if (sel) {
      near[sel] = true;
      data.trade_edges.concat(data.related_edges).forEach(function (e) { if (e.from === sel) near[e.to] = true; if (e.to === sel) near[e.from] = true; });
    }
    function dim(id) {
      var n = byId[id];
      if (focus && focus.indexOf(id) < 0) return true;
      if (sel && !near[id]) return true;
      if (state.industry !== "all" && n.industry !== state.industry) return true;
      return false;
    }
    var gEdges = svg("g"), gNodes = svg("g");
    var hopKeys = {};
    data.rings.forEach(function (r) { if (!state.ring || r.ring_id === state.ring) r.hops.forEach(function (h) { hopKeys[h.from + ">" + h.to] = true; }); });

    if (state.trade) {
      data.trade_edges.forEach(function (e) {
        var a = pos[e.from], b = pos[e.to], A = byId[e.from], B = byId[e.to];
        if (!a || !b || !visible(A) || !visible(B)) return;
        var flagged = !!hopKeys[e.from + ">" + e.to], faded = dim(e.from) || dim(e.to);
        gEdges.appendChild(svg("line", {
          x1: a.x, y1: a.y, x2: b.x, y2: b.y,
          stroke: flagged ? "#D65A5A" : "#9A8F7E",
          "stroke-width": flagged ? 2.2 : Math.min(2, 0.5 + Math.sqrt(e.total_paise / 1e9)),
          opacity: flagged ? 0.95 : faded ? 0.05 : 0.28,
        }));
      });
    }
    if (state.related) {
      data.related_edges.forEach(function (e) {
        var a = pos[e.from], b = pos[e.to], A = byId[e.from], B = byId[e.to];
        if (!a || !b || !visible(A) || !visible(B)) return;
        gEdges.appendChild(svg("line", {
          x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: "#C8912F", "stroke-width": 1.2, "stroke-dasharray": "4 3",
          opacity: dim(e.from) || dim(e.to) ? 0.05 : 0.6,
        }));
      });
    }
    data.nodes.forEach(function (n) {
      if (!visible(n)) return;
      var p = pos[n.id], flagged = n.rings.length > 0, r = 4 + Math.min(5, n.rings.length * 2) + Math.min(3, Math.sqrt(n.invoices) / 3);
      var g = svg("g", { opacity: dim(n.id) ? 0.14 : 1, style: "cursor:pointer" }, [
        svg("title", {}),
        svg("circle", {
          cx: p.x, cy: p.y, r: r, fill: COLORS[n.industry] || "#9A8F7E",
          stroke: n.id === sel ? "#EFE7D8" : flagged ? "#D65A5A" : "#14100C", "stroke-width": n.id === sel ? 2.5 : flagged ? 2 : 1,
        }),
      ]);
      g.firstChild.textContent = n.name + " (" + n.id + ")\n" + n.city + " · " + n.industry + (flagged ? "\nIn " + n.rings.length + " flagged loop(s)" : "");
      g.addEventListener("click", function (ev) { ev.stopPropagation(); select(n.id); });
      gNodes.appendChild(g);
    });
    root.appendChild(gEdges); root.appendChild(gNodes);
    applyView();
  }
  function applyView() { $("network-svg").setAttribute("viewBox", view.x + " " + view.y + " " + view.w + " " + view.h); $("net-zoom-val").textContent = Math.round((W / view.w) * 100) + "%"; }
  function fit(ids) {
    if (!ids || !ids.length) { view = { x: 0, y: 0, w: W, h: H }; return applyView(); }
    var xs = ids.map(function (i) { return pos[i].x; }), ys = ids.map(function (i) { return pos[i].y; });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    var w = Math.max(220, (x1 - x0) * 1.6), h = Math.max(140, (y1 - y0) * 1.6), s = Math.max(w / W, h / H);
    view = { x: (x0 + x1) / 2 - (W * s) / 2, y: (y0 + y1) / 2 - (H * s) / 2, w: W * s, h: H * s };
    applyView();
  }
  function zoom(f, cx, cy) {
    var nw = Math.max(120, Math.min(W * 1.2, view.w * f)), nh = nw * (H / W);
    cx = cx == null ? view.x + view.w / 2 : cx; cy = cy == null ? view.y + view.h / 2 : cy;
    view = { x: cx - ((cx - view.x) / view.w) * nw, y: cy - ((cy - view.y) / view.h) * nh, w: nw, h: nh };
    applyView();
  }

  /* ───── side panel ───── */
  function row(label, value) {
    return UI.el("div", { className: "rail-row" }, [UI.el("span", { className: "rail-key", text: label }), UI.el("span", { className: "rail-val", text: value })]);
  }
  function ringButton(r) {
    return UI.el("button", { type: "button", className: "rail-ring", onClick: function () { setRing(r.ring_id); } }, [
      UI.el("span", { className: "badge badge-fail", text: r.ring_id }),
      UI.el("span", { text: r.members.length + " firms · " + r.exposure + " · " + r.tier + " tier" }),
    ]);
  }
  function renderRail() {
    var rail = $("network-rail");
    UI.clear(rail);
    if (state.selected) {
      var n = byId[state.selected], trades = {};
      data.trade_edges.forEach(function (e) {
        if (e.from === n.id) trades[e.to] = (trades[e.to] || 0) + e.total_paise;
        if (e.to === n.id) trades[e.from] = (trades[e.from] || 0) + e.total_paise;
      });
      rail.appendChild(UI.el("div", { className: "eyebrow", text: "FIRM" }));
      rail.appendChild(UI.el("h3", { className: "rail-title", text: n.name }));
      [["ID", n.id], ["City", n.city], ["Industry", n.industry], ["GSTIN", n.gstin], ["Directors on record", String(n.directors)], ["Invoices", n.invoices + " · " + n.volume]].forEach(function (r) { rail.appendChild(row(r[0], r[1])); });
      var top = Object.keys(trades).sort(function (a, b) { return trades[b] - trades[a]; }).slice(0, 5);
      rail.appendChild(UI.el("h4", { className: "section-heading rail-h", text: "Trades most with" }));
      top.forEach(function (id) { rail.appendChild(UI.el("button", { type: "button", className: "rail-link", text: byId[id].name + " · " + inr(trades[id]), onClick: function () { select(id, true); } })); });
      if (!top.length) rail.appendChild(UI.el("p", { className: "helper-copy", text: "No invoices in this dataset." }));
      var rel = data.related_edges.filter(function (e) { return e.from === n.id || e.to === n.id; });
      if (rel.length) {
        rail.appendChild(UI.el("h4", { className: "section-heading rail-h", text: "Related firms" }));
        rel.forEach(function (e) {
          var other = e.from === n.id ? e.to : e.from;
          rail.appendChild(UI.el("button", { type: "button", className: "rail-link", text: byId[other].name + " — " + e.links.join(", "), onClick: function () { select(other, true); } }));
        });
      }
      if (n.rings.length) {
        rail.appendChild(UI.el("h4", { className: "section-heading rail-h", text: "Flagged loops" }));
        n.rings.forEach(function (id) { rail.appendChild(ringButton(ringById[id])); });
      }
      rail.appendChild(UI.el("button", { type: "button", className: "btn btn-ghost btn-sm rail-back", text: "← Back to overview", onClick: function () { select(null); } }));
    } else if (state.ring) {
      var r = ringById[state.ring];
      rail.appendChild(UI.el("div", { className: "eyebrow", text: "FLAGGED LOOP" }));
      rail.appendChild(UI.el("h3", { className: "rail-title", text: r.ring_id + " · " + r.members.length + " firms" }));
      rail.appendChild(row("Tier", r.tier)); rail.appendChild(row("Score", String(r.score))); rail.appendChild(row("Value circling", r.exposure));
      rail.appendChild(UI.el("h4", { className: "section-heading rail-h", text: "Firms and what each keeps" }));
      r.members.forEach(function (id) { rail.appendChild(UI.el("button", { type: "button", className: "rail-link", text: byId[id].name + " · " + (r.retained_pct[id] != null ? r.retained_pct[id] + "% kept" : ""), onClick: function () { select(id, true); } })); });
      rail.appendChild(UI.el("h4", { className: "section-heading rail-h", text: "Why it was flagged" }));
      r.evidence.forEach(function (s) { rail.appendChild(UI.el("p", { className: "rail-evidence", text: s })); });
      rail.appendChild(UI.el("button", { type: "button", className: "btn btn-ghost btn-sm rail-back", text: "← All loops", onClick: function () { setRing(""); } }));
    } else {
      rail.appendChild(UI.el("div", { className: "eyebrow", text: "OVERVIEW" }));
      rail.appendChild(UI.el("h3", { className: "rail-title", text: data.nodes.length + " firms, " + data.rings.length + " flagged loops" }));
      rail.appendChild(UI.el("p", { className: "helper-copy", text: "Click a firm to see who it trades with and who it is related to. Pick a loop below to see why it was flagged." }));
      data.rings.forEach(function (r) { rail.appendChild(ringButton(r)); });
    }
  }

  /* ───── state changes ───── */
  function select(id, zoomIn) {
    state.selected = id; draw(); renderRail();
    if (id && zoomIn) {
      var near = [id];
      data.trade_edges.concat(data.related_edges).forEach(function (e) { if (e.from === id) near.push(e.to); if (e.to === id) near.push(e.from); });
      fit(near);
    }
  }
  function setRing(id) {
    state.ring = id; state.selected = null; $("net-ring-select").value = id;
    draw(); renderRail(); fit(id ? ringById[id].members : null);
  }
  function toggle(btn, key) {
    state[key] = !state[key]; btn.classList.toggle("active", state[key]); btn.setAttribute("aria-pressed", String(state[key])); draw();
  }

  function init() {
    $("net-toggle-trade").addEventListener("click", function () { toggle(this, "trade"); });
    $("net-toggle-related").addEventListener("click", function () { toggle(this, "related"); });
    $("net-toggle-flagged").addEventListener("click", function () { state.flaggedOnly = !state.flaggedOnly; this.classList.toggle("active", state.flaggedOnly); this.setAttribute("aria-pressed", String(state.flaggedOnly)); draw(); });
    $("net-industry-filter").addEventListener("change", function () { state.industry = this.value; draw(); });
    $("net-ring-select").addEventListener("change", function () { setRing(this.value); });
    $("net-reset-btn").addEventListener("click", function () { state.selected = null; setRing(""); state.industry = "all"; $("net-industry-filter").value = "all"; fit(null); });
    $("net-zoom-in").addEventListener("click", function () { zoom(0.75); });
    $("net-zoom-out").addEventListener("click", function () { zoom(1.33); });
    $("net-zoom-reset").addEventListener("click", function () { fit(null); });
    $("net-search").addEventListener("input", function () {
      var q = this.value.trim().toLowerCase(); if (q.length < 2) return;
      var hit = data && data.nodes.filter(function (n) { return (n.name + " " + n.id + " " + n.gstin).toLowerCase().indexOf(q) >= 0; })[0];
      if (hit) select(hit.id, true);
    });
    var svgEl = $("network-svg"), drag = null;
    svgEl.addEventListener("wheel", function (e) {
      e.preventDefault(); var box = svgEl.getBoundingClientRect();
      zoom(e.deltaY < 0 ? 0.85 : 1.18, view.x + ((e.clientX - box.left) / box.width) * view.w, view.y + ((e.clientY - box.top) / box.height) * view.h);
    }, { passive: false });
    svgEl.addEventListener("pointerdown", function (e) { drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false }; svgEl.setPointerCapture(e.pointerId); });
    svgEl.addEventListener("pointermove", function (e) {
      if (!drag) return; var box = svgEl.getBoundingClientRect();
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      view.x = drag.vx - (dx / box.width) * view.w; view.y = drag.vy - (dy / box.height) * view.h; applyView();
    });
    svgEl.addEventListener("pointerup", function () { drag = null; });
  }

  function load() {
    var status = $("net-status");
    return UI.apiFetch("/network").then(function (d) {
      if (!d || !d.available) { status.textContent = (d && d.message) || "The market map is not available."; status.classList.remove("hidden"); return; }
      data = d; byId = {}; ringById = {};
      d.nodes.forEach(function (n) { byId[n.id] = n; });
      d.rings.forEach(function (r) { ringById[r.ring_id] = r; });
      layout();
      var sel = $("net-ring-select");
      d.rings.forEach(function (r) { sel.appendChild(UI.el("option", { value: r.ring_id, text: r.ring_id + " · " + r.members.length + " firms · " + r.exposure })); });
      $("net-source").textContent = d.source;
      status.classList.add("hidden");
      loaded = true;
      draw(); renderRail();
    }).catch(function (e) { status.textContent = "Could not load the market map: " + e.message; status.classList.remove("hidden"); });
  }

  function show() { if (!loaded) load(); }

  document.addEventListener("DOMContentLoaded", init);
  window.LabyrinthNetwork = { show: show };
})();
