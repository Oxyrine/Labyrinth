/* Network view: every firm in the generated market, grouped into zones, with the loops the network check flagged.
   Quiet by default: grey firms and invoices, red for anything flagged, gold dashes for related firms. */
(function () {
  "use strict";

  var UI = window.LabyrinthUI;
  var NS = "http://www.w3.org/2000/svg";
  var W = 1800, H = 1060;
  var SIGNAL_NAMES = {
    s_flow: "Value kept by each firm", s_product: "Same goods all the way round", s_timing: "Timing of invoices", s_isolation: "Closed off from other trade",
    s_movement: "Truck movements", s_related: "Related firms", s_transform: "No change of form", s_rapid: "Faster than payment terms",
  };
  var data = null, byId = {}, ringById = {}, pos = {}, zones = [], zoneOf = {};
  var nodeEls = {}, edgeEls = [], relEls = [];
  var view = { x: 0, y: 0, w: W, h: H };
  var state = { trade: true, related: false, flaggedOnly: false, ring: "", industry: "all", selected: null, hover: null, q: "" };
  var loaded = false;

  function $(id) { return document.getElementById(id); }
  function svg(tag, attrs, kids) {
    var n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    (kids || []).forEach(function (c) { n.appendChild(c); });
    return n;
  }
  function cap(s) { return String(s || "").replace(/^./, function (c) { return c.toUpperCase(); }); }

  /* ───── layout: components become zones; each zone runs a small force simulation, seeded so the picture never changes ───── */
  function rng(seed) { var h = seed >>> 0; return function () { h = (Math.imul(h ^ (h >>> 15), 2246822507) + 0x9e3779b9) >>> 0; return (h % 100000) / 100000; }; }
  function hash(str) { var h = 2166136261; for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }

  function components() {
    var adj = {}, seen = {}, out = [];
    data.nodes.forEach(function (n) { adj[n.id] = []; });
    data.trade_edges.forEach(function (e) { adj[e.from].push(e.to); adj[e.to].push(e.from); });
    data.nodes.forEach(function (n) {
      if (seen[n.id]) return;
      var stack = [n.id], comp = []; seen[n.id] = true;
      while (stack.length) { var x = stack.pop(); comp.push(x); adj[x].forEach(function (y) { if (!seen[y]) { seen[y] = true; stack.push(y); } }); }
      out.push(comp.sort());
    });
    return out.sort(function (a, b) { return b.length - a.length || (a[0] < b[0] ? -1 : 1); });
  }

  /* Fruchterman-Reingold with cooling: firms repel, invoices pull, and the movement allowed each round shrinks to nothing. */
  function simulate(ids, box, links) {
    var r = rng(hash(ids.join()));
    var pad = 34, top = 46;
    var bx = box.x + pad, by = box.y + top, bw = box.w - pad * 2, bh = box.h - top - pad;
    var cx = bx + bw / 2, cy = by + bh / 2;
    ids.forEach(function (id, i) {
      var a = (i / ids.length) * 6.2832, d = Math.min(bw, bh) * (0.15 + r() * 0.25);
      pos[id] = { x: cx + Math.cos(a) * d, y: cy + Math.sin(a) * d };
    });
    var inZone = {}; ids.forEach(function (id) { inZone[id] = true; });
    var zl = links.filter(function (l) { return inZone[l[0]] && inZone[l[1]]; });
    var k = Math.sqrt((bw * bh) / Math.max(ids.length, 1)) * 0.9, rounds = 320, t0 = bw / 12;
    var disp = {};
    for (var it = 0; it < rounds; it++) {
      var t = t0 * (1 - it / rounds) + 0.5;
      ids.forEach(function (id) { disp[id] = { x: 0, y: 0 }; });
      for (var i = 0; i < ids.length; i++) {
        for (var j = i + 1; j < ids.length; j++) {
          var A = pos[ids[i]], B = pos[ids[j]], dx = A.x - B.x, dy = A.y - B.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01, f = d > k * 3 ? 0 : (k * k) / d / d;
          disp[ids[i]].x += dx * f; disp[ids[i]].y += dy * f; disp[ids[j]].x -= dx * f; disp[ids[j]].y -= dy * f;
        }
      }
      zl.forEach(function (l) {
        var A = pos[l[0]], B = pos[l[1]], dx = A.x - B.x, dy = A.y - B.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01, f = (d / k) * l[2];
        disp[l[0]].x -= dx * f; disp[l[0]].y -= dy * f; disp[l[1]].x += dx * f; disp[l[1]].y += dy * f;
      });
      ids.forEach(function (id) {
        var p = pos[id], d = disp[id];
        d.x += (cx - p.x) * 0.09; d.y += (cy - p.y) * 0.09;
        var len = Math.sqrt(d.x * d.x + d.y * d.y) + 0.01, step = Math.min(len, t);
        p.x = Math.max(bx, Math.min(bx + bw, p.x + (d.x / len) * step));
        p.y = Math.max(by, Math.min(by + bh, p.y + (d.y / len) * step));
      });
    }
  }

  function grid(ids, box, cell) {
    var cols = Math.max(1, Math.floor((box.w - 40) / cell));
    ids.forEach(function (id, i) { pos[id] = { x: box.x + 30 + (i % cols) * cell + cell / 2, y: box.y + 66 + Math.floor(i / cols) * cell * 0.9 }; });
  }

  function layout() {
    var comps = components(), big = comps.filter(function (c) { return c.length >= 8; }).slice(0, 3);
    var rest = comps.filter(function (c) { return big.indexOf(c) < 0; });
    var slots = [{ x: 40, y: 40, w: 1040, h: 770 }, { x: 1120, y: 40, w: 640, h: 380 }, { x: 1120, y: 440, w: 640, h: 370 }];
    var links = data.trade_edges.map(function (e) { return [e.from, e.to, 1]; }).concat(data.related_edges.map(function (e) { return [e.from, e.to, 0.7]; }));
    zones = [];
    big.forEach(function (c, i) {
      var title = i === 0 ? "Main trading network" : "Trading group " + String.fromCharCode(65 + i);
      zones.push({ box: slots[i], title: title + " · " + c.length + " firms" });
      c.forEach(function (id) { zoneOf[id] = i; });
      simulate(c, slots[i], links);
    });
    var tray = { x: 40, y: 840, w: 1720, h: 190 }, loose = [];
    rest.forEach(function (c) { c.forEach(function (id) { loose.push(id); }); });
    if (loose.length) { zones.push({ box: tray, title: "Small groups and firms with no invoices in this dataset · " + loose.length + " firms" }); loose.forEach(function (id) { zoneOf[id] = "tray"; }); grid(loose, tray, 44); }
  }

  /* ───── drawing (once); filters and focus only change classes, so hover stays smooth ───── */
  function radius(n) { return n.rings.length ? 8 + Math.min(6, (n.rings.length - 1) * 3) + Math.min(3, Math.sqrt(n.invoices) / 5) : 4.5; }

  function build() {
    var root = $("network-svg");
    UI.clear(root);
    var gZones = svg("g"), gRel = svg("g"), gEdges = svg("g"), gNodes = svg("g"), gHubs = svg("g", { class: "net-hubs" });
    zones.forEach(function (z) {
      var t = svg("text", { x: z.box.x + 8, y: z.box.y + 18, class: "net-zone-title" }); t.textContent = z.title.toUpperCase(); gZones.appendChild(t);
    });
    var hop = {};
    data.rings.forEach(function (r) { r.hops.forEach(function (h) { (hop[h.from + ">" + h.to] = hop[h.from + ">" + h.to] || []).push(r.ring_id); }); });
    edgeEls = data.trade_edges.map(function (e) {
      var a = pos[e.from], b = pos[e.to];
      var line = svg("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "net-edge" + (hop[e.from + ">" + e.to] ? " flagged" : ""), "stroke-width": hop[e.from + ">" + e.to] ? 2.4 : 0.9 });
      gEdges.appendChild(line);
      return { el: line, e: e, rings: hop[e.from + ">" + e.to] || [] };
    });
    relEls = data.related_edges.map(function (e) {
      var a = pos[e.from], b = pos[e.to];
      var line = svg("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "net-related" });
      gRel.appendChild(line);
      return { el: line, e: e };
    });
    nodeEls = {};
    data.nodes.forEach(function (n) {
      var p = pos[n.id], r = radius(n), flagged = n.rings.length > 0;
      var g = svg("g", { class: "net-node" + (flagged ? " flagged" : ""), tabindex: "-1" }, [
        svg("circle", { cx: p.x, cy: p.y, r: r + 7, class: "net-hit" }),
        svg("circle", { cx: p.x, cy: p.y, r: r, class: "net-dot" }),
      ]);
      g.addEventListener("mouseenter", function (ev) { state.hover = n.id; paint(); tip(n, ev); });
      g.addEventListener("mousemove", function (ev) { tip(n, ev); });
      g.addEventListener("mouseleave", function () { state.hover = null; paint(); $("net-tip").classList.add("hidden"); });
      g.addEventListener("click", function (ev) { ev.stopPropagation(); select(n.id); });
      gNodes.appendChild(g);
      nodeEls[n.id] = { el: g, n: n };
    });
    var hubs = data.nodes.filter(function (n) { return n.rings.length > 0; })
      .sort(function (a, b) { return b.rings.length - a.rings.length || b.volume_paise - a.volume_paise; }).slice(0, 12);
    hubs.forEach(function (n) {
      var p = pos[n.id], t = svg("text", { x: p.x, y: p.y - radius(n) - 6, class: "net-hub", "text-anchor": "middle" });
      t.textContent = n.id; gHubs.appendChild(t);
    });
    [gZones, gRel, gEdges, gNodes, gHubs].forEach(function (g) { root.appendChild(g); });
    paint(); applyView();
  }

  function neighbours(id) {
    var out = {}; out[id] = true;
    data.trade_edges.forEach(function (e) { if (e.from === id) out[e.to] = true; if (e.to === id) out[e.from] = true; });
    data.related_edges.forEach(function (e) { if (e.from === id) out[e.to] = true; if (e.to === id) out[e.from] = true; });
    return out;
  }

  /* Decide what is lit: a chosen loop wins, then a selected firm, then whatever the pointer is over. */
  function paint() {
    var root = $("network-svg"), focusRing = state.ring ? ringById[state.ring] : null;
    root.classList.toggle("hide-trade", !state.trade);
    root.classList.toggle("has-focus", !!(state.ring || state.selected));
        var lit = null, edgeOk = null;
    if (focusRing) { lit = {}; focusRing.members.forEach(function (m) { lit[m] = true; }); edgeOk = function (x) { return x.rings.indexOf(focusRing.ring_id) >= 0; }; }
    else if (state.selected || state.hover) { var id = state.selected || state.hover; lit = neighbours(id); edgeOk = function (x) { return x.e.from === id || x.e.to === id; }; }
    var q = state.q;
    Object.keys(nodeEls).forEach(function (id) {
      var o = nodeEls[id], n = o.n, dim = false;
      if (lit && !lit[id]) dim = true;
      if (state.flaggedOnly && !n.rings.length) dim = true;
      if (state.industry !== "all" && n.industry !== state.industry) dim = true;
      if (q && (n.name + " " + n.id + " " + n.gstin).toLowerCase().indexOf(q) < 0) dim = true;
      o.el.classList.toggle("dim", dim);
      o.el.classList.toggle("selected", id === state.selected || (!!focusRing && lit[id]));
      o.el.classList.toggle("hover", id === state.hover);
    });
    edgeEls.forEach(function (x) {
      var dim = (edgeOk && !edgeOk(x)) || (state.flaggedOnly && !x.rings.length);
      x.el.classList.toggle("dim", !!dim);
      x.el.classList.toggle("lit", !!(edgeOk && !dim));
    });
    // Related-firm lines are long and many; show them when asked for, or for whatever is in focus.
    relEls.forEach(function (x) {
      var inFocus = !!lit && (state.selected || state.hover ? (x.e.from === (state.selected || state.hover) || x.e.to === (state.selected || state.hover)) : (lit[x.e.from] && lit[x.e.to]));
      var a = byId[x.e.from], b = byId[x.e.to];
      var shortLine = zoneOf[x.e.from] === zoneOf[x.e.to] && zoneOf[x.e.from] !== "tray";
      var bothFlagged = a.rings.length > 0 && b.rings.length > 0;
      x.el.classList.toggle("on", inFocus || (state.related && !lit && shortLine && (!state.flaggedOnly || bothFlagged)));
      x.el.classList.toggle("dim", !!lit && !inFocus);
    });
    labels();
  }

  /* Names appear only where they help: the members of a chosen loop, and the selected firm. */
  function labels() {
    var root = $("network-svg"), old = root.querySelector(".net-labels");
    if (old) old.remove();
    var ids = state.ring ? ringById[state.ring].members.slice() : [];
    if (state.selected && ids.indexOf(state.selected) < 0) ids.push(state.selected);
    if (!ids.length) return;
    var g = svg("g", { class: "net-labels" });
    ids.forEach(function (id) {
      var p = pos[id], t = svg("text", { x: p.x, y: p.y - radius(byId[id]) - 9, class: "net-label", "text-anchor": "middle" });
      t.textContent = byId[id].name.replace(/ (Pvt|Private) Ltd$| Ltd$/i, ""); g.appendChild(t);
    });
    root.appendChild(g);
  }

  function tip(n, ev) {
    var t = $("net-tip"), wrap = $("net-canvas").getBoundingClientRect();
    UI.clear(t);
    t.appendChild(UI.el("strong", { text: n.name }));
    t.appendChild(UI.el("span", { text: n.id + " · " + n.city + " · " + cap(n.industry) }));
    t.appendChild(UI.el("span", { text: n.invoices + " invoices · " + n.volume + (n.rings.length ? " · in " + n.rings.length + " flagged loop" + (n.rings.length > 1 ? "s" : "") : "") }));
    t.classList.remove("hidden");
    var x = ev.clientX - wrap.left + 16, y = ev.clientY - wrap.top + 14;
    t.style.left = Math.min(x, wrap.width - t.offsetWidth - 8) + "px"; t.style.top = Math.min(y, wrap.height - t.offsetHeight - 8) + "px";
  }

  /* ───── view ───── */
  function applyView() {
    view.w = Math.min(view.w, W); view.h = view.w * (H / W);
    view.x = Math.max(0, Math.min(W - view.w, view.x)); view.y = Math.max(0, Math.min(H - view.h, view.y));
    $("network-svg").setAttribute("viewBox", view.x + " " + view.y + " " + view.w + " " + view.h); $("net-zoom-val").textContent = Math.round((W / view.w) * 100) + "%"; }
  function fit(ids) {
    if (!ids || !ids.length) { view = { x: 0, y: 0, w: W, h: H }; return applyView(); }
    var xs = ids.map(function (i) { return pos[i].x; }), ys = ids.map(function (i) { return pos[i].y; });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    var s = Math.max(Math.max(360, (x1 - x0) * 1.7) / W, Math.max(240, (y1 - y0) * 1.7) / H);
    view = { x: (x0 + x1) / 2 - (W * s) / 2, y: (y0 + y1) / 2 - (H * s) / 2, w: W * s, h: H * s };
    applyView();
  }
  function zoom(f, cx, cy) {
    var nw = Math.max(300, Math.min(W, view.w * f)), nh = nw * (H / W);
    cx = cx == null ? view.x + view.w / 2 : cx; cy = cy == null ? view.y + view.h / 2 : cy;
    view = { x: cx - ((cx - view.x) / view.w) * nw, y: cy - ((cy - view.y) / view.h) * nh, w: nw, h: nh };
    applyView();
  }

  /* ───── side panel ───── */
  function stat(label, value, cls) {
    return UI.el("div", { className: "trail-stat" }, [UI.el("span", { className: "ts-label", text: label }), UI.el("span", { className: "ts-val " + (cls || ""), text: String(value) })]);
  }
  function section(title, kids) { return UI.el("div", { className: "rail-section" }, [UI.el("h4", { text: title })].concat(kids)); }
  function link(id, right) {
    return UI.el("button", { type: "button", className: "rail-row-btn", onClick: function () { select(id, true); } }, [
      UI.el("span", { className: "rr-name", text: byId[id].name }), UI.el("span", { className: "rr-val", text: right || "" }),
    ]);
  }
  function showRail(kids) {
    var rail = $("network-rail"); UI.clear(rail);
    kids.forEach(function (k) { rail.appendChild(k); });
    rail.classList.remove("hidden"); $("net-layout").classList.add("has-rail");
  }
  function hideRail() { $("network-rail").classList.add("hidden"); $("net-layout").classList.remove("has-rail"); }
  function closeButton(label, fn) { return UI.el("button", { type: "button", className: "btn btn-sm btn-ghost rail-close", text: label, onClick: fn }); }

  function firmRail(n) {
    var sold = {}, bought = {};
    data.trade_edges.forEach(function (e) { if (e.from === n.id) sold[e.to] = (sold[e.to] || 0) + e.total_paise; if (e.to === n.id) bought[e.from] = (bought[e.from] || 0) + e.total_paise; });
    var all = {}; Object.keys(sold).forEach(function (k) { all[k] = (all[k] || 0) + sold[k]; }); Object.keys(bought).forEach(function (k) { all[k] = (all[k] || 0) + bought[k]; });
    var top = Object.keys(all).sort(function (a, b) { return all[b] - all[a]; }).slice(0, 6);
    var rel = data.related_edges.filter(function (e) { return e.from === n.id || e.to === n.id; });
    var kids = [
      UI.el("div", { className: "detail-header" }, [
        UI.el("div", { className: "detail-title", text: n.name }),
        UI.el("div", { className: "detail-subtitle", text: n.id + " · " + n.city.toUpperCase() + " · " + n.industry.toUpperCase() }),
      ]),
      UI.el("div", { className: "trail-stat-group" }, [stat("INVOICES", n.invoices), stat("VALUE", n.volume), stat("FLAGGED LOOPS", n.rings.length, n.rings.length ? "risk" : ""), stat("RELATED FIRMS", rel.length)]),
    ];
    if (n.rings.length) kids.push(section("Flagged loops", [UI.el("div", { className: "rail-chips" }, n.rings.map(function (id) {
      return UI.el("button", { type: "button", className: "btn btn-sm btn-danger", text: id + " · " + ringById[id].members.length + " firms", onClick: function () { setRing(id); } });
    }))]));
    if (top.length) kids.push(section("Trades most with", top.map(function (id) {
      var dir = sold[id] && bought[id] ? "↔" : sold[id] ? "sold →" : "← bought";
      return link(id, dir + "  " + UI.formatInr(all[id]));
    })));
    if (rel.length) kids.push(section("Related firms", rel.map(function (e) {
      var other = e.from === n.id ? e.to : e.from;
      return UI.el("div", { className: "rail-related" }, [link(other, ""), UI.el("div", { className: "rail-chips" }, e.links.map(function (l) { return UI.el("span", { className: "chip-gold", text: l }); }))]);
    })));
    kids.push(section("Registration", [
      UI.el("div", { className: "rail-kv" }, [UI.el("span", { text: "GSTIN" }), UI.el("span", { className: "mono-id", text: n.gstin })]),
      UI.el("div", { className: "rail-kv" }, [UI.el("span", { text: "Directors on record" }), UI.el("span", { className: "mono-id", text: String(n.directors) })]),
    ]));
    kids.push(closeButton("Close", function () { select(null); }));
    showRail(kids);
  }

  function ringRail(r) {
    var kids = [
      UI.el("div", { className: "detail-header" }, [
        UI.el("div", { className: "detail-title", text: "Loop " + r.ring_id }),
        UI.el("div", { className: "detail-subtitle", text: r.members.length + " FIRMS · " + r.tier.toUpperCase() + " TIER" }),
      ]),
      UI.el("div", { className: "trail-stat-group" }, [stat("VALUE CIRCLING", r.exposure, "risk"), stat("SCORE", r.score), stat("FIRMS", r.members.length), stat("INVOICE HOPS", r.hops.length)]),
      section("Firms and what each keeps", r.members.map(function (id) { return link(id, r.retained_pct[id] != null ? r.retained_pct[id] + "% kept" : ""); })),
    ];
    var sig = Object.keys(r.signals || {});
    if (sig.length) kids.push(section("What the check looked at", sig.map(function (k) {
      return UI.el("div", { className: "sig-row" }, [
        UI.el("span", { className: "sig-name", text: SIGNAL_NAMES[k] || k }),
        UI.el("span", { className: "sig-bar" }, [UI.el("i", { style: "width:" + Math.round(r.signals[k] * 100) + "%" })]),
        UI.el("span", { className: "sig-val", text: r.signals[k].toFixed(2) }),
      ]);
    })));
    kids.push(section("Why it was flagged", r.evidence.map(function (s) { return UI.el("p", { className: "rail-evidence", text: s }); })));
    kids.push(closeButton("Reset view", function () { setRing(""); }));
    showRail(kids);
  }

  /* ───── state changes ───── */
  function select(id, zoomIn) {
    state.selected = id; state.ring = ""; $("net-ring-select").value = "";
    paint();
    if (!id) { hideRail(); return; }
    firmRail(byId[id]);
    if (zoomIn) { var near = Object.keys(neighbours(id)); fit(near.length > 1 ? near : [id]); }
  }
  function setRing(id) {
    state.ring = id; state.selected = null; $("net-ring-select").value = id;
    paint();
    if (id) { ringRail(ringById[id]); fit(ringById[id].members); } else { hideRail(); fit(null); }
  }
  function flip(btn, key) { state[key] = !state[key]; btn.classList.toggle("active", state[key]); btn.setAttribute("aria-pressed", String(state[key])); paint(); }

  function init() {
    $("net-toggle-trade").addEventListener("click", function () { flip(this, "trade"); });
    $("net-toggle-related").addEventListener("click", function () { flip(this, "related"); });
    $("net-toggle-flagged").addEventListener("click", function () { flip(this, "flaggedOnly"); });
    $("net-industry-filter").addEventListener("change", function () { state.industry = this.value; paint(); });
    $("net-ring-select").addEventListener("change", function () { setRing(this.value); });
    $("net-reset-btn").addEventListener("click", function () {
      state.selected = null; state.ring = ""; state.industry = "all"; state.q = "";
      $("net-industry-filter").value = "all"; $("net-ring-select").value = ""; $("net-search").value = "";
      hideRail(); paint(); fit(null);
    });
    $("net-zoom-in").addEventListener("click", function () { zoom(0.78); });
    $("net-zoom-out").addEventListener("click", function () { zoom(1.28); });
    $("net-zoom-reset").addEventListener("click", function () { fit(null); });
    $("net-search").addEventListener("input", function () {
      state.q = this.value.trim().toLowerCase(); if (!data) return; paint();
      var hits = state.q.length >= 2 ? data.nodes.filter(function (n) { return (n.name + " " + n.id + " " + n.gstin).toLowerCase().indexOf(state.q) >= 0; }) : [];
      if (hits.length === 1) select(hits[0].id, true);
    });
    var root = $("network-svg"), drag = null;
    root.addEventListener("wheel", function (e) {
      e.preventDefault(); var b = root.getBoundingClientRect();
      zoom(e.deltaY < 0 ? 0.88 : 1.14, view.x + ((e.clientX - b.left) / b.width) * view.w, view.y + ((e.clientY - b.top) / b.height) * view.h);
    }, { passive: false });
    root.addEventListener("pointerdown", function (e) { if (e.target.closest(".net-node")) return; drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false }; root.setPointerCapture(e.pointerId); root.classList.add("panning"); });
    root.addEventListener("pointermove", function (e) {
      if (!drag) return; var b = root.getBoundingClientRect(), dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      view.x = drag.vx - (dx / b.width) * view.w; view.y = drag.vy - (dy / b.height) * view.h; applyView();
    });
    root.addEventListener("pointerup", function () { if (drag && !drag.moved && state.selected) select(null); drag = null; root.classList.remove("panning"); });
  }

  function load() {
    var status = $("net-status");
    return UI.apiFetch("/network").then(function (d) {
      if (!d || !d.available) { status.textContent = (d && d.message) || "The market map is not available."; status.classList.remove("hidden"); return; }
      data = d; byId = {}; ringById = {};
      d.nodes.forEach(function (n) { byId[n.id] = n; });
      d.rings.forEach(function (r) { ringById[r.ring_id] = r; });
      layout(); build();
      var sel = $("net-ring-select");
      d.rings.forEach(function (r) { sel.appendChild(UI.el("option", { value: r.ring_id, text: "Loop " + r.ring_id + " · " + r.members.length + " firms · " + r.exposure })); });
      var flaggedFirms = d.nodes.filter(function (n) { return n.rings.length; }).length;
      $("net-stat-firms").textContent = d.nodes.length;
      $("net-stat-invoices").textContent = d.trade_edges.reduce(function (s, e) { return s + e.count; }, 0);
      $("net-stat-loops").textContent = d.rings.length;
      $("net-stat-flagged").textContent = flaggedFirms;
      $("net-source").textContent = "Generated market";
      status.classList.add("hidden");
      loaded = true;
    }).catch(function (e) { status.textContent = "Could not load the market map: " + e.message; status.classList.remove("hidden"); });
  }

  function show() { if (!loaded) load(); }
  document.addEventListener("DOMContentLoaded", init);
  window.LabyrinthNetwork = { show: show };
})();
