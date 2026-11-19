// The landing page (index.html): everything but the 3D tree.
(function () {
  // the wordmark steps out of the way once the page scrolls
  var barEl = document.querySelector(".bar");
  function fadeMark() { barEl.classList.toggle("scrolled", window.scrollY > 60); }
  window.addEventListener("scroll", fadeMark, { passive: true }); fadeMark();
  // the crystal by the wordmark: the CLI's loader, drawn once along the same path
  var PRISM = ["   /\\   ", "  /  \\  ", " /    \\ ", " \\    / ", "  \\  /  ", "   \\/   "], PATH = [[0,3],[1,2],[2,1],[3,1],[4,2],[5,3],[5,4],[4,5],[3,6],[2,6],[1,5],[0,4]];
  var xt = document.getElementById("xtal"), grid = PRISM.map(function (l) { return l.split("").map(function () { return " "; }); });
  function paintX() { xt.textContent = grid.map(function (r) { return r.join(""); }).join("\n"); }
  var reduceX = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  PATH.forEach(function (c, i) { function put() { grid[c[0]][c[1]] = PRISM[c[0]].charAt(c[1]); paintX(); } if (reduceX) { put(); } else { setTimeout(put, 400 + i * 90); } });
  paintX();

  // the example review card, driven by the three settings (example values; the rules are krypton review's)
  var EX = { ratings: [46, 45, 43, 44, 44, 43.5], purity: [71, 72, 74, 73, 73, 74] }, G = { target: 70, tries: 3, mode: "manual" }, pick = null;
  function draw() {
    var n = G.tries, ratings = EX.ratings.slice(0, n + 1), purity = EX.purity.slice(0, n + 1);
    var best = 0; purity.forEach(function (p, i) { if (p > purity[best]) { best = i; } });
    var low = Math.min.apply(null, ratings), pct = Math.round((1 - low / ratings[0]) * 100);
    document.getElementById("rvr").innerHTML = EX.ratings.map(function (r, i) { var cls = i > n ? "gone" : (i === best && i > 0 ? "best" : ""); return '<li class="' + cls + '"><span>' + (i ? "run " + i : "start") + (i === best && i > 0 && i <= n ? " &middot; best" : "") + "</span>" + r + "&times;</li>"; }).join("");
    var tag = document.getElementById("rvt"), acts = document.querySelector(".acts2"), auto = G.mode === "auto";
    if (auto) {
      tag.className = "tag ok"; tag.innerHTML = "Auto-resolved &middot; kept run " + best + " (" + ratings[best] + "&times;)";
      acts.hidden = true; document.getElementById("rvp").textContent = purity[best];
    } else {
      tag.className = "tag"; tag.innerHTML = "Stuck after " + n + (n === 1 ? " run" : " runs") + " &middot; best only " + pct + "% better";
      acts.hidden = false; document.getElementById("rvp").textContent = pick === "approve" ? purity[best] : purity[0];
    }
    document.getElementById("rv0").textContent = purity[0]; document.getElementById("pT").textContent = "Target " + G.target; document.getElementById("pM").textContent = "Max tries " + G.tries;
  }
  [].forEach.call(document.querySelectorAll(".seg"), function (seg) { seg.addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (!b) { return; }
    [].forEach.call(seg.querySelectorAll("button"), function (x) { x.setAttribute("aria-pressed", String(x === b)); });
    G[seg.dataset.k] = seg.dataset.k === "mode" ? b.dataset.v : +b.dataset.v; pick = null; resetActs(); draw();
  }); });
  function resetActs() { [].forEach.call(document.querySelectorAll(".acts2 button"), function (x) { x.setAttribute("aria-pressed", "false"); x.classList.remove("dim"); }); }
  [].forEach.call(document.querySelectorAll(".acts2 button"), function (b) { b.addEventListener("click", function () {
    pick = b.dataset.d;
    [].forEach.call(document.querySelectorAll(".acts2 button"), function (x) { var on = x === b; x.setAttribute("aria-pressed", String(on)); x.classList.toggle("dim", !on); });
    draw();
  }); });
  draw();
})();
