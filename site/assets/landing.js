// The landing page (index.html): everything but the 3D tree.
(function () {
  var barEl = document.querySelector(".bar");

  // the wordmark steps out of the way once the page scrolls
  function startHeader() {
    function fadeMark() { barEl.classList.toggle("scrolled", window.scrollY > 60); }
    window.addEventListener("scroll", fadeMark, { passive: true }); fadeMark();
    // the crystal by the wordmark: the CLI's loader, drawn once along the same path
    var PRISM = ["   /\\   ", "  /  \\  ", " /    \\ ", " \\    / ", "  \\  /  ", "   \\/   "], PATH = [[0,3],[1,2],[2,1],[3,1],[4,2],[5,3],[5,4],[4,5],[3,6],[2,6],[1,5],[0,4]];
    var xt = document.getElementById("xtal"), grid = PRISM.map(function (l) { return l.split("").map(function () { return " "; }); });
    function paintX() { xt.textContent = grid.map(function (r) { return r.join(""); }).join("\n"); }
    var reduceX = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    PATH.forEach(function (c, i) { function put() { grid[c[0]][c[1]] = PRISM[c[0]].charAt(c[1]); paintX(); } if (reduceX) { put(); } else { setTimeout(put, 400 + i * 90); } });
    paintX();
  }

  // phones: the menu button folds the nav open and shut; a link, a tap outside the header or Escape closes it
  function startMenu() {
    var menuBtn = document.getElementById("menuBtn");
    function setMenu(open) { barEl.classList.toggle("open", open); menuBtn.setAttribute("aria-expanded", String(open)); }
    menuBtn.addEventListener("click", function () { setMenu(!barEl.classList.contains("open")); });
    document.querySelector(".bnav").addEventListener("click", function (e) { if (e.target.closest("a")) { setMenu(false); } });
    document.addEventListener("click", function (e) { if (!barEl.contains(e.target)) { setMenu(false); } });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") { setMenu(false); } });
  }

  // the example review card, driven by the three settings (example values; the rules are krypton review's)
  var EX = { ratings: [46, 45, 43, 44, 44, 43.5], purity: [71, 72, 74, 73, 73, 74] };
  function bestRun(purity) {
    var best = 0;
    purity.forEach(function (p, i) { if (p > purity[best]) { best = i; } });
    return best;
  }
  // one row of the run list: runs past max tries are greyed out, the best kept run is marked
  function runItem(r, i, n, best) {
    var shown = i <= n, isBest = i === best && i > 0, cls = shown ? (isBest ? "best" : "") : "gone";
    return '<li class="' + cls + '"><span>' + (i ? "run " + i : "start") + (isBest && shown ? " &middot; best" : "") + "</span>" + r + "&times;</li>";
  }
  // the badge, and what the card shows as the purity: auto keeps the best run; manual waits for approve or reject
  function verdict(G, best, pick) {
    var n = G.tries, ratings = EX.ratings.slice(0, n + 1), purity = EX.purity.slice(0, n + 1), tag = document.getElementById("rvt");
    if (G.mode === "auto") {
      tag.className = "tag ok"; tag.innerHTML = "Auto-resolved &middot; kept run " + best + " (" + ratings[best] + "&times;)";
      return { hideActs: true, purity: purity[best] };
    }
    var pct = Math.round((1 - Math.min.apply(null, ratings) / ratings[0]) * 100);
    tag.className = "tag"; tag.innerHTML = "Stuck after " + n + (n === 1 ? " run" : " runs") + " &middot; best only " + pct + "% better";
    return { hideActs: false, purity: pick === "approve" ? purity[best] : purity[0] };
  }
  function drawReview(G, pick) {
    var best = bestRun(EX.purity.slice(0, G.tries + 1)), shown = verdict(G, best, pick);
    document.getElementById("rvr").innerHTML = EX.ratings.map(function (r, i) { return runItem(r, i, G.tries, best); }).join("");
    document.querySelector(".acts2").hidden = shown.hideActs; document.getElementById("rvp").textContent = shown.purity;
    document.getElementById("rv0").textContent = EX.purity[0]; document.getElementById("pT").textContent = "Target " + G.target; document.getElementById("pM").textContent = "Max tries " + G.tries;
  }
  // press one button of a group; with dim, the others fade
  function pressOnly(buttons, b, dim) {
    [].forEach.call(buttons, function (x) { var on = x === b; x.setAttribute("aria-pressed", String(on)); x.classList.toggle("dim", dim && !on); });
  }
  function startReview() {
    var G = { target: 70, tries: 3, mode: "manual" }, picked = { d: null }, acts = document.querySelectorAll(".acts2 button");
    function draw() { drawReview(G, picked.d); }
    [].forEach.call(document.querySelectorAll(".seg"), function (seg) { seg.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) { return; }
      pressOnly(seg.querySelectorAll("button"), b, false);
      G[seg.dataset.k] = seg.dataset.k === "mode" ? b.dataset.v : +b.dataset.v; picked.d = null;
      pressOnly(acts, null, false); draw();
    }); });
    [].forEach.call(acts, function (b) { b.addEventListener("click", function () { picked.d = b.dataset.d; pressOnly(acts, b, true); draw(); }); });
    draw();
  }

  startHeader(); startMenu(); startReview();
})();
