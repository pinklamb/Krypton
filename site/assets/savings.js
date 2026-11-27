/* Savings page: your monthly bill for reading your code, today against with Krypton.
   Today: caching on, files in default order. With Krypton: stable files first (krypton order), then cleaner code.
   Rates: 47% of each prompt reused in default order against 71% in Krypton's order (garth, live Claude API, 12 replayed
   commits), and 16.9% fewer tokens per change after a cleanup (average of six runs). A repository scanned on the tree
   page (sessionStorage "kr-repo") replaces the example team's codebase with its own. */
(function () {
  'use strict';
  // Prices per million tokens: normal input, cached read, cache write (list prices).
  var MODELS = {
    sonnet: { name: 'Sonnet 5.5', input: 2.0, read: 0.2, write: 2.5 },
    opus: { name: 'Opus 5.5', input: 4.0, read: 0.2, write: 5.0 },
    gpt4o: { name: 'GPT-4o', input: 2.5, read: 1.25, write: 2.5 }
  };
  var REUSE = { alpha: 0.466, krypton: 0.712 };
  var REFACTOR = 0.169, GARTH_TANGLED_SHARE = 11 / 111, DAYS = 22, CHARS_PER_TOKEN = 3.5;
  var NS = 'http://www.w3.org/2000/svg';
  var $ = function (id) { return document.getElementById(id); };

  // a repository scanned on the tree page, if there is one
  function readRepo() {
    try { return JSON.parse(sessionStorage.getItem('kr-repo') || 'null'); } catch (e) { return null; }
  }
  // the example team's numbers, or the scanned repository's: its size, tokens a line, and how much cleaner code would save
  function startState(repo) {
    var S = { devs: 50, turns: 100, lines: 25000, tpl: 11.5, model: 'sonnet', refactor: true, effect: REFACTOR };
    if (!repo || !(repo.lines > 0)) { return S; }
    var tangledShare = repo.functions ? repo.tangled / repo.functions : 0;
    S.devs = 10; S.lines = repo.lines;
    S.tpl = Math.min(20, Math.max(1, Math.round(10 * (repo.characters / CHARS_PER_TOKEN) / repo.lines) / 10));
    S.effect = REFACTOR * Math.min(1, tangledShare / GARTH_TANGLED_SHARE);
    S.refactor = repo.tangled > 0;
    return S;
  }
  var repo = readRepo(), S = startState(repo);

  function money(v) { return v >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'M' : v >= 1e4 ? '$' + (v / 1e3).toFixed(1) + 'K' : v >= 100 ? '$' + Math.round(v).toLocaleString('en-US') : '$' + v.toFixed(2); }
  function fmt(n) { return Math.round(n).toLocaleString('en-US'); }
  function tokK(n) { return n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : Math.round(n / 1e3) + 'K'; }
  function clean(text) { return String(text).replace(/[<>&"]/g, ''); }
  function calc(model) {
    var m = MODELS[model || S.model], C = S.lines * S.tpl, N = S.devs * S.turns * DAYS;
    var per = function (h) { return C * (h * m.read + (1 - h) * m.write) / 1e6 * N; };
    var today = per(REUSE.alpha), order = per(REUSE.krypton), full = order * (S.refactor ? 1 - S.effect : 1);
    return { C: C, today: today, order: order, full: full, saved: today - full };
  }

  function el(tag, attrs, parent, text) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined) { e.textContent = text; }
    parent.appendChild(e);
    return e;
  }
  function clear(svg) { while (svg.firstChild) { svg.removeChild(svg.firstChild); } return svg; }
  var tip = $('tip');
  function hover(node, html) {
    node.addEventListener('pointermove', function (e) {
      tip.innerHTML = html(); tip.hidden = false;
      tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 10) + 'px';
      tip.style.top = Math.max(8, e.clientY - tip.offsetHeight - 12) + 'px';
    });
    node.addEventListener('pointerleave', function () { tip.hidden = true; });
  }

  // The tree behind the headline: Krypton's own picture of a codebase, with a few tangled functions lit.
  function drawArt() {
    var svg = $('art'), seed = 7, nodes = [];
    var rnd = function () { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    function grow(x, y, ang, len, depth) {
      var x2 = x + Math.cos(ang) * len, y2 = y + Math.sin(ang) * len, n = depth > 4 ? 2 : 3;
      el('line', { x1: x, y1: y, x2: x2, y2: y2, stroke: 'rgba(255,255,255,.28)', 'stroke-width': Math.max(0.6, depth * 0.45) }, svg);
      nodes.push([x2, y2, depth]);
      if (depth === 0) { return; }
      for (var i = 0; i < n; i++) { grow(x2, y2, ang + (i - (n - 1) / 2) * (0.5 + rnd() * 0.25), len * (0.68 + rnd() * 0.12), depth - 1); }
    }
    grow(470, 660, -Math.PI / 2, 150, 7);
    nodes.forEach(function (q, i) {
      var hot = i % 37 === 5;
      el('circle', { cx: q[0], cy: q[1], r: hot ? 6 : 2 + q[2] * 0.35, fill: hot ? '#e3a86b' : 'rgba(236,226,255,.85)', opacity: hot ? 1 : 0.75 }, svg);
    });
  }

  function drawModels() {
    var svg = clear($('models')), keys = Object.keys(MODELS), bills = keys.map(function (k) { return calc(k).full; });
    var top = Math.max.apply(null, bills) || 1;
    keys.forEach(function (k, i) {
      var on = k === S.model, w = 160 * bills[i] / top, ink = on ? '#fff' : 'rgba(247,241,234,.7)';
      el('text', { x: 0, y: 14 + i * 24, fill: ink, 'font-size': 12 }, svg, MODELS[k].name);
      var b = el('rect', { x: 74, y: 5 + i * 24, width: Math.max(3, w), height: 12, rx: 6, fill: on ? 'var(--sv-money)' : 'rgba(255,255,255,.35)' }, svg);
      el('text', { x: 80 + w, y: 15 + i * 24, fill: ink, 'font-size': 12 }, svg, money(bills[i]));
      hover(b, function () { var c = calc(k); return '<b>' + MODELS[k].name + '</b><br>Today ' + money(c.today) + '<br>With Krypton ' + money(c.full) + ' a month'; });
    });
  }

  function drawBarcode() {
    var svg = clear($('barcode'));
    for (var k = 0; k < 100; k++) {
      var fill = k < Math.round(REUSE.alpha * 100) ? '#fff' : k < Math.round(REUSE.krypton * 100) ? 'var(--sv-money)' : 'rgba(255,255,255,.22)';
      el('rect', { x: k * 3, y: 0, width: 1.8, height: 34, fill: fill }, svg);
    }
  }

  // Nested arcs from one point: today's bill, after stable files first, with Krypton. Each spans its bill.
  function drawArcs(b) {
    var svg = clear($('arcs')), x0 = 18, base = 150, R = 132;
    [[b.today, 'rgba(255,255,255,.95)', 'Today'], [b.order, 'rgba(255,255,255,.6)', 'Stable files first'], [b.full, 'var(--sv-money)', 'With Krypton']].forEach(function (q) {
      var r = R * q[0] / b.today, x1 = x0 + 2 * r;
      var arc = el('path', { d: 'M' + x0 + ',' + base + 'A' + r + ',' + r + ' 0 0 1 ' + x1 + ',' + base, fill: 'none', stroke: q[1], 'stroke-width': 1.6 }, svg);
      el('circle', { cx: x1, cy: base, r: 4, fill: q[1] }, svg);
      hover(arc, function () { return '<b>' + q[2] + '</b><br>' + money(q[0]) + ' a month'; });
    });
    el('circle', { cx: x0, cy: base, r: 4, fill: '#fff' }, svg);
    for (var d = 0; d < 9; d++) { el('circle', { cx: x0 + 34 + d * 28, cy: base, r: 1.6, fill: 'rgba(255,255,255,.5)' }, svg); }
  }

  // the headline numbers: what is saved, as money, a share and per developer
  function renderTotals(b) {
    $('saved').textContent = money(b.saved);
    $('facts').innerHTML = '<span><b>' + fmt(S.devs) + '</b> developer' + (S.devs === 1 ? '' : 's') + '</span><span><b>' + S.turns + '</b> agent turns a day each</span><span><b>' + fmt(S.lines) + '</b> lines</span><span>' + MODELS[S.model].name + ', list prices</span>';
    $('pct').textContent = Math.round(100 * b.saved / b.today) + '%';
    $('pctNote').textContent = money(b.saved) + ' of ' + money(b.today);
    $('perDev').textContent = money(b.saved / S.devs);
    $('reuseNow').textContent = Math.round(REUSE.krypton * 100) + '%';
    $('reuseWas').textContent = 'today ' + Math.round(REUSE.alpha * 100) + '%';
    $('arcToday').textContent = money(b.today) + ' today';
    $('arcPay').textContent = (100 * b.full / b.today).toFixed(1) + '%';
    $('arcKr').textContent = money(b.full) + ' with Krypton';
  }

  // the controls' own read-outs and the cleaner-code switch's wording
  function renderControls(b) {
    $('devsOut').textContent = fmt(S.devs); $('turnsOut').textContent = S.turns; $('linesOut').textContent = fmt(S.lines) + ' lines';
    $('tokHint').textContent = 'About ' + tokK(b.C) + ' tokens each time an agent reads it (' + S.tpl + ' a line).';
    $('modelPick').innerHTML = Object.keys(MODELS).map(function (k) { return '<button type="button" data-k="' + k + '" aria-pressed="' + (k === S.model) + '">' + MODELS[k].name + '</button>'; }).join('');
    $('effLabel').textContent = 'Include cleaner code: ' + (S.effect * 100).toFixed(1) + '% fewer tokens per change';
    $('effNote').textContent = repo ? (repo.tangled ? 'Scaled from the 16.9% measured after a cleanup to your share of tangled functions.' : 'No tangled functions were found, so no cleaner-code saving is assumed.') : 'The average over six measured runs after a cleanup; the typical run saved about 6%.';
  }

  // the assumptions behind the numbers, listed under the chart
  function renderAssumptions() {
    var m = MODELS[S.model];
    $('assume').innerHTML = [['Prompt reused: today / krypton order (garth)', Math.round(REUSE.alpha * 100) + '% / ' + Math.round(REUSE.krypton * 100) + '%'],
      ['Fewer tokens per change after a cleanup', S.refactor ? (S.effect * 100).toFixed(1) + '%' : 'off'],
      ['Price per million: input / cached / write', '$' + m.input.toFixed(2) + ' / $' + m.read.toFixed(2) + ' / $' + m.write.toFixed(2)],
      ['Working days a month · tokens a line', DAYS + ' · ' + S.tpl]].map(function (r) { return '<div><span>' + r[0] + '</span><b>' + r[1] + '</b></div>'; }).join('');
  }

  function render() {
    var b = calc();
    renderTotals(b); renderControls(b); renderAssumptions();
    drawModels(); drawArcs(b);
  }

  // the badge naming the scanned repository, when the numbers are its own
  function showRepo() {
    if (!repo) { return; }
    $('svEyebrow').textContent = 'Savings for ' + clean(repo.repo);
    $('repoBadge').hidden = false;
    $('repoBadge').innerHTML = 'Based on <b>' + clean(repo.repo) + '</b><span>' + fmt(repo.files) + ' files</span><span>' + fmt(repo.lines) + ' lines</span><span>about ' + tokK(repo.characters / CHARS_PER_TOKEN) + ' tokens each time it is read</span><span>' + fmt(repo.tangled) + ' tangled functions</span>';
  }
  // the sliders, the model buttons and the cleaner-code switch all redraw the page
  function wireSlider(k) {
    var input = $(k);
    if (k === 'lines' && S.lines > +input.max) { input.max = S.lines; }
    input.value = S[k];
    input.addEventListener('input', function () { S[k] = +input.value; render(); });
  }
  function pickModel(e) {
    var b = e.target.closest('[data-k]');
    if (!b) { return; }
    S.model = b.getAttribute('data-k'); render();
  }
  function wireControls() {
    ['devs', 'turns', 'lines'].forEach(wireSlider);
    $('modelPick').addEventListener('click', pickModel);
    $('refactor').checked = S.refactor;
    $('refactor').addEventListener('change', function () { S.refactor = $('refactor').checked; render(); });
  }

  showRepo(); wireControls();
  drawArt(); drawBarcode(); render();
})();
