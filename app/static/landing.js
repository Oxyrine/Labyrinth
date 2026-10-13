/* Landing screen: a drifting network behind the wordmark, and the way in and out of the workspace. */
(function () {
  "use strict";
  var landing = document.getElementById("landing-page");
  var shell = document.getElementById("app-shell");
  var canvas = document.getElementById("constellation");
  if (!landing || !shell) return;
  var visible = true;

  function enter() {
    visible = false;
    landing.classList.add("leaving");
    shell.classList.remove("hidden-app");
    try { sessionStorage.setItem("labyrinth-entered", "1"); } catch (e) { /* storage may be blocked */ }
    var main = document.getElementById("main-workspace");
    if (main) main.focus({ preventScroll: true });
  }

  function show() {
    visible = true;
    landing.classList.remove("leaving");
    shell.classList.add("hidden-app");
    var title = landing.querySelector(".landing-title");
    if (title) { title.style.animation = "none"; void title.offsetWidth; title.style.animation = ""; }
    start();
    var btn = document.getElementById("enter-btn");
    if (btn) btn.focus();
  }

  function start() {
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext("2d"), w = 0, h = 0, nodes = [], LINK = 150;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function size() {
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var count = w < 768 ? 36 : 80;
      nodes = [];
      for (var i = 0; i < count; i++) {
        nodes.push({ x: Math.random() * w, y: Math.random() * h, vx: (Math.random() - .5) * .3, vy: (Math.random() - .5) * .3, r: Math.random() * 2.2 + 1.5 });
      }
    }

    function frame() {
      if (!visible) return;
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = "#E6C879"; ctx.lineWidth = 1;
      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var d = Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y);
          if (d < LINK) {
            ctx.globalAlpha = .12 + (1 - d / LINK) * .5;
            ctx.beginPath(); ctx.moveTo(nodes[i].x, nodes[i].y); ctx.lineTo(nodes[j].x, nodes[j].y); ctx.stroke();
          }
        }
      }
      var t = Date.now() * .001;
      ctx.fillStyle = "#E6C879";
      nodes.forEach(function (n) {
        if (!reduce) { n.x += n.vx; n.y += n.vy; }
        if (n.x < 0 || n.x > w) n.vx *= -1;
        if (n.y < 0 || n.y > h) n.vy *= -1;
        var pulse = .78 + Math.sin(t + n.x) * .22;
        ctx.globalAlpha = pulse * .26; ctx.beginPath(); ctx.arc(n.x, n.y, n.r * 2.4, 0, 6.2832); ctx.fill();
        ctx.globalAlpha = pulse; ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, 6.2832); ctx.fill();
      });
      ctx.globalAlpha = 1;
      if (!reduce) requestAnimationFrame(frame);
    }

    size();
    window.addEventListener("resize", size);
    frame();
  }

  document.getElementById("enter-btn").addEventListener("click", enter);
  document.addEventListener("keydown", function (e) {
    if (visible && (e.key === "Enter" || e.key === " ") && document.activeElement === document.body) { e.preventDefault(); enter(); }
  });

  var entered = false;
  try { entered = sessionStorage.getItem("labyrinth-entered") === "1" || /[?&]enter=1/.test(location.search); } catch (e) { /* ignore */ }
  if (entered) { visible = false; landing.classList.add("leaving"); shell.classList.remove("hidden-app"); }
  else start();

  window.LabyrinthLanding = { show: show };
})();
