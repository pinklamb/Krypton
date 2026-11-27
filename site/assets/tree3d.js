/* Krypton tree: draws a repository as the landing page draws garth, one point per node of its syntax tree, files on a sphere, imports as
   arcs, and the tangled functions lit in lavender. It needs three.js (r128) on the page.

   KryptonTree.draw(canvas, data, { labels: element, selection: element })  ->  { focus(index), show(index), release(), stop() }
     data: { files: [{ p, t, h }], edges: [[a, b]], fns: [{ name, times, file }] }, the outline /api/scan answers

   Same layout as the landing page (assets of land4.js), without the scroll story: it turns slowly, dragging turns it by hand, and the
   tangled function under the pointer is named and lit (the tree holds still while you look). */
(function () {
  'use strict';
  var CODES = '123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';
  // the cocoa palette: points on the dark background add up, so they glow
  var C = { bg: 0x0b0509, ink: [.6, .59, .64], hot0: [.46, .36, .78], hot1: [.86, .76, 1] };
  // the function under the pointer grows and turns the deepest lavender; the other tangled functions step back while one is picked
  var VERT = 'attribute float sev; attribute float size; attribute float alpha; attribute float fn; uniform float k; uniform float uSel; uniform mat3 uRot; uniform vec3 uInk; uniform vec3 uHot0; uniform vec3 uHot1; varying vec3 vc; varying float va;' +
    'void main(){ bool picked = uSel >= 0.0 && abs(fn - uSel) < 0.5; bool other = uSel >= 0.0 && fn >= 0.0 && !picked;' +
    ' vc = sev < 0.0 ? uInk : (picked ? uHot1 : mix(uHot0, uHot1, sev)); va = alpha * (picked ? 1.6 : other ? 0.45 : 1.15);' +
    ' vec4 mv = modelViewMatrix * vec4(uRot * position, 1.0); gl_PointSize = size * (picked ? 1.7 : 1.0) * k / -mv.z; gl_Position = projectionMatrix * mv; }';
  var FRAG = 'varying vec3 vc; varying float va; void main(){ float d = length(gl_PointCoord - .5) * 2.; if (d > 1.) discard; gl_FragColor = vec4(vc, min(1.0, va) * smoothstep(1., .45, d)); }';
  // the point attributes and the list each one is read from
  var ATTRS = [['position', 'P3', 3], ['sev', 'HOT', 1], ['size', 'SZ', 1], ['alpha', 'AL', 1], ['fn', 'HIT', 1]];

  // ---------- small helpers ----------
  function rng(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; var t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function vec(x, y, z) { return new THREE.Vector3(x, y, z); }
  function clampUnit(x) { return Math.max(-1, Math.min(1, x)); }

  // ---------- the layout: where each file's root sits ----------
  // files are ordered by folder (biggest folder first), then by size, so neighbours on the sphere share a folder
  function fileOrder(F) {
    var dirs = {}, seq = [];
    F.forEach(function (f, k) { var d = f.p.split('/').slice(0, -1).join('/'); (dirs[d] = dirs[d] || []).push(k); });
    Object.keys(dirs).sort(function (a, b) { return dirs[b].length - dirs[a].length; }).forEach(function (d) {
      dirs[d].sort(function (a, b) { return F[b].t.length - F[a].t.length; }).forEach(function (k) { seq.push(k); });
    });
    return seq;
  }
  // the roots spread over an ellipsoid along a golden-angle spiral; each file grows outward along the surface normal
  function fileRoots(F) {
    var RX = 1.45, RY = .98, RZ = 1.12, golden = Math.PI * (3 - Math.sqrt(5)), seq = fileOrder(F), root = [], out = [];
    seq.forEach(function (k, s) {
      var u = .9 * (1 - 2 * (s + .5) / seq.length), v = s * golden, w = Math.sqrt(1 - u * u), p = vec(RX * w * Math.cos(v), RY * u, RZ * w * Math.sin(v));
      root[k] = p; out[k] = vec(p.x / (RX * RX), p.y / (RY * RY), p.z / (RZ * RZ)).normalize();
    });
    return { root: root, out: out };
  }
  function severities(fns) {
    var maxT = fns.reduce(function (a, f) { return Math.max(a, f.times); }, 1);
    return fns.map(function (f) { return Math.max(.25, Math.log(f.times + 1) / Math.log(maxT + 1)); });
  }

  // ---------- the layout: every node and branch as points ----------
  // a file's syntax tree: each node's children, its depth, and which tangled function (if any) it belongs to; a parent that does not
  // come before its child is ignored, so a damaged outline still draws
  function nodeTable(f) {
    var P = f.t, n = P.length, kids = [], dep = new Array(n).fill(0), fid = [], i;
    for (i = 0; i < n; i++) { kids.push([]); fid.push(CODES.indexOf(f.h.charAt(i))); }
    for (i = 1; i < n; i++) {
      if (P[i] >= 0 && P[i] < i) { dep[i] = dep[P[i]] + 1; kids[P[i]].push(i); }
    }
    return { P: P, n: n, kids: kids, dep: dep, fid: fid };
  }
  // a node's children fan out around its direction, each generation a little shorter
  function fanOut(N, i, grow) {
    var ks = N.kids[i], m = ks.length;
    if (!m || !grow.pos[i]) { return; }
    var d = grow.dir[i], u = vec().crossVectors(d, grow.ref).normalize(), w = vec().crossVectors(d, u).normalize();
    var th = m === 1 ? .22 : Math.min(.9, .3 + .075 * Math.sqrt(m)), base = N.dep[i] * 1.3 + grow.k;
    ks.forEach(function (c, j) {
      var ph = base + j * (Math.PI * 2 / m), off = u.clone().multiplyScalar(Math.cos(ph)).add(w.clone().multiplyScalar(Math.sin(ph)));
      var nd = d.clone().multiplyScalar(Math.cos(th)).add(off.multiplyScalar(Math.sin(th))).normalize();
      grow.dir[c] = nd; grow.pos[c] = grow.pos[i].clone().add(nd.clone().multiplyScalar(grow.L0 * Math.pow(.8, N.dep[c])));
    });
  }
  // where every node of file k sits, grown from the file's root (a node cut off from the root has no place)
  function growFile(N, k, roots) {
    var out = roots.out[k], i;
    var grow = { k: k, L0: .05 + .017 * Math.log10(N.n + 1), ref: Math.abs(out.y) > .9 ? vec(1, 0, 0) : vec(0, 1, 0), pos: new Array(N.n), dir: new Array(N.n) };
    grow.pos[0] = roots.root[k].clone(); grow.dir[0] = out.clone();
    for (i = 0; i < N.n; i++) { fanOut(N, i, grow); }
    return grow.pos;
  }
  // the lists every point writes into
  function pointLists() {
    var A = { P3: [], HOT: [], SZ: [], AL: [], HIT: [] };
    function push(p, sev, size, alpha, hit) { A.P3.push(p.x, p.y, p.z); A.HOT.push(sev); A.SZ.push(size); A.AL.push(alpha); A.HIT.push(hit); }
    return { arrays: A, push: push };
  }
  // tangled nodes shake in proportion to their function's severity; the rest stay still
  function jitter(N, i, h, build) {
    if (h < 0) { return build.zero; }
    var R = build.R;
    return vec(R() - .5, R() - .5, R() - .5).multiplyScalar((.05 + .012 * N.dep[i]) * (.6 + build.sev[h]));
  }
  function nodeSize(i, h, sev) {
    if (i === 0) { return 4; }
    return h >= 0 ? 2.6 + 3.4 * sev[h] : 1.7;
  }
  // a few points along the branch up to the parent, more on tangled ones
  function emitBranch(from, to, h, build) {
    var hot = h >= 0, sev = hot ? build.sev[h] : -1, steps = hot ? 4 : 2, size = hot ? 1.4 + build.sev[h] : .9, alpha = hot ? .55 : .16, s;
    for (s = 1; s <= steps; s++) { build.lists.push(from.clone().lerp(to, s / (steps + 1)), sev, size, alpha, h); }
  }
  // one node as a point; a tangled one also adds to its function's centre (where its label goes)
  function emitNode(N, i, pos, build) {
    var h = N.fid[i], p = pos[i].clone().add(jitter(N, i, h, build)), up = pos[N.P[i]];
    build.lists.push(p, h >= 0 ? build.sev[h] : -1, nodeSize(i, h, build.sev), h >= 0 ? .85 : .34, h);
    if (h >= 0) { build.cent[h].s.add(p); build.cent[h].n++; }
    if (i && up) { emitBranch(up, p, h, build); }
  }
  function emitFile(N, pos, build) {
    for (var i = 0; i < N.n; i++) {
      if (pos[i]) { emitNode(N, i, pos, build); }
    }
  }
  // the import edges between files: curves bowed towards the middle
  function emitEdge(a, b, push) {
    var mid = a.clone().add(b).multiplyScalar(.5 * .35), s;
    for (s = 1; s < 18; s++) {
      var t = s / 18, p = a.clone().multiplyScalar((1 - t) * (1 - t)).add(mid.clone().multiplyScalar(2 * (1 - t) * t)).add(b.clone().multiplyScalar(t * t));
      push(p, -1, .8, .16);
    }
  }
  function layout(D) {
    var sev = severities(D.fns), roots = fileRoots(D.files);
    var build = { R: rng(5), sev: sev, zero: vec(), lists: pointLists(), cent: D.fns.map(function () { return { s: vec(), n: 0 }; }) };
    D.files.forEach(function (f, k) { var N = nodeTable(f); emitFile(N, growFile(N, k, roots), build); });
    D.edges.forEach(function (e) {
      var a = roots.root[e[0]], b = roots.root[e[1]];
      if (a && b) { emitEdge(a, b, build.lists.push); }
    });
    build.cent.forEach(function (c) { if (c.n) { c.s.multiplyScalar(1 / c.n); } });
    var A = build.lists.arrays;
    return { P3: A.P3, HOT: A.HOT, SZ: A.SZ, AL: A.AL, HIT: A.HIT, cent: build.cent };
  }

  // ---------- the view ----------
  function geometry(L) {
    var g = new THREE.BufferGeometry();
    ATTRS.forEach(function (a) { g.setAttribute(a[0], new THREE.Float32BufferAttribute(L[a[1]], a[2])); });
    return g;
  }
  function uniforms() {
    return { k: { value: 300 }, uSel: { value: -1 }, uRot: { value: new THREE.Matrix3() }, uInk: { value: vec().fromArray(C.ink) }, uHot0: { value: vec().fromArray(C.hot0) }, uHot1: { value: vec().fromArray(C.hot1) } };
  }
  // labels on the three worst functions, each pinned to its own cluster: "1  name  46.2× limit"
  function makePills(D, opts) {
    return (opts.labels ? D.fns.slice(0, 3) : []).map(function (f, q) {
      var el = document.createElement('div'), n = document.createElement('i'), t = document.createElement('span'), x = document.createElement('em');
      el.className = 'pill' + (q === 0 ? ' top' : ''); n.textContent = q + 1; t.textContent = f.name; x.textContent = f.times + '× limit';
      el.append(n, t, x); opts.labels.appendChild(el); return el;
    });
  }
  function makeTip(opts) {
    if (!opts.labels) { return null; }
    var tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; opts.labels.appendChild(tip);
    return tip;
  }
  // the points that belong to a tangled function, the only ones the pointer can pick
  function hotPoints(L) {
    var hot = [];
    L.HIT.forEach(function (h, n) { if (h >= 0) { hot.push(n); } });
    return hot;
  }
  function makeView(canvas, D, opts) {
    var L = layout(D), U = uniforms();
    var mat = new THREE.ShaderMaterial({ uniforms: U, transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending, vertexShader: VERT, fragmentShader: FRAG });
    var pts = new THREE.Points(geometry(L), mat), R3 = new THREE.WebGLRenderer({ canvas: canvas, antialias: false, alpha: false });
    pts.frustumCulled = false; R3.setPixelRatio(Math.min(devicePixelRatio, 2)); R3.setClearColor(C.bg, 1);
    var scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(30, 1, .1, 60);
    scene.add(pts); cam.position.set(0, 0, 5.4);
    return { canvas: canvas, D: D, L: L, opts: opts, U: U, R3: R3, scene: scene, cam: cam, pills: makePills(D, opts), tip: makeTip(opts), hot: hotPoints(L) };
  }
  // how the tree is turned and what is picked, changed by the pointer, the list and the loop
  function startState() {
    return { yaw: .4, pitch: .12, aim: null, pinned: -1, drag: null, hover: null, picked: -1, held: -1,
      still: !!window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches, running: true, last: performance.now(),
      rot: new THREE.Matrix4(), eul: new THREE.Euler(), v: vec(), pickV: vec() };
  }
  // fit the canvas; a narrow stage (a phone held upright) would crop the tree's sides, so the camera steps back until the whole width
  // fits, and the dots shrink by only part of that step so they stay easy to see
  function size(view) {
    var w = view.canvas.clientWidth, h = view.canvas.clientHeight, cam = view.cam;
    view.R3.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix();
    var back = Math.min(2.2, Math.max(1, 1.5 / cam.aspect));
    cam.position.z = 5.4 * back;
    view.U.k.value = h / (2 * Math.tan(cam.fov * Math.PI / 360)) * .0055 * Math.sqrt(back);
  }

  // ---------- picking and naming functions ----------
  // the tangled function with a point within 14px of (x, y) on the canvas, or -1
  function nearest(view, st, x, y) {
    var rect = view.canvas.getBoundingClientRect(), best = 14 * 14, found = -1, P3 = view.L.P3;
    view.hot.forEach(function (n) {
      var at = st.pickV.set(P3[n * 3], P3[n * 3 + 1], P3[n * 3 + 2]).applyMatrix4(st.rot).project(view.cam);
      if (at.z < -1 || at.z > 1) { return; }
      var px = (at.x + 1) * rect.width / 2 - x, py = (1 - at.y) * rect.height / 2 - y, dist = px * px + py * py;
      if (dist < best) { best = dist; found = view.L.HIT[n]; }
    });
    return found;
  }
  function describe(fn, box) {
    var title = document.createElement('b'), where = document.createElement('span'), times = document.createElement('em');
    title.textContent = fn.name; where.textContent = fn.file; times.textContent = fn.times + '× the limit';
    box.replaceChildren(title, where, times);
  }
  function hideTip(view) { if (view.tip) { view.tip.hidden = true; } }
  function showTip(view, id, x, y) {
    var tip = view.tip;
    if (!tip) { return; }
    if (id < 0 || !view.D.fns[id]) { tip.hidden = true; return; }
    describe(view.D.fns[id], tip); tip.hidden = false;
    tip.style.left = Math.min(x + 14, view.canvas.clientWidth - tip.offsetWidth - 8) + 'px'; tip.style.top = Math.max(8, y - tip.offsetHeight - 10) + 'px';
  }
  // a click names the function under it in the corner card, or clears the card
  function pin(view, st, found) {
    var box = view.opts.selection;
    if (found >= 0 && view.D.fns[found]) { describe(view.D.fns[found], box); box.hidden = false; st.pinned = found; return; }
    st.pinned = -1; box.hidden = true;
  }

  // ---------- the pointer: drag to turn, hover to name, click to pin ----------
  function pointerMove(view, st, e) {
    if (st.drag) {
      st.yaw = st.drag.yaw + (e.clientX - st.drag.x) * .008; st.pitch = clampUnit(st.drag.pitch + (e.clientY - st.drag.y) * .006);
      hideTip(view); return;
    }
    var rect = view.canvas.getBoundingClientRect();
    st.hover = { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }
  function pointerUp(view, st, e) {
    if (!st.drag) { return; }
    var moved = Math.hypot(e.clientX - st.drag.x, e.clientY - st.drag.y);
    st.drag = null;
    if (moved > 5 || !view.opts.selection) { return; }
    var rect = view.canvas.getBoundingClientRect();
    pin(view, st, nearest(view, st, e.clientX - rect.left, e.clientY - rect.top));
  }
  function wirePointer(view, st) {
    var canvas = view.canvas;
    canvas.addEventListener('pointerdown', function (e) { st.aim = null; st.drag = { x: e.clientX, y: e.clientY, yaw: st.yaw, pitch: st.pitch }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', function (e) { pointerMove(view, st, e); });
    canvas.addEventListener('pointerleave', function () { st.hover = null; st.picked = -1; hideTip(view); });
    canvas.addEventListener('pointerup', function (e) { pointerUp(view, st, e); });
  }

  // ---------- the loop ----------
  // turning to show a function: ease the yaw (the short way round) and the pitch towards it
  function easeToAim(st, dt) {
    var aim = st.aim, dy = Math.atan2(Math.sin(aim.yaw - st.yaw), Math.cos(aim.yaw - st.yaw)), k = st.still ? 1 : 1 - Math.exp(-dt / .25);
    st.yaw += dy * k; st.pitch += (aim.pitch - st.pitch) * k;
    if (Math.abs(dy) < .002 && Math.abs(aim.pitch - st.pitch) < .002) { st.aim = null; }
  }
  // the tree turns slowly unless it is being dragged, looked at, or told to hold still
  function idle(st) { return !st.drag && !st.still && st.picked < 0 && st.held < 0 && st.pinned < 0; }
  function turn(st, dt) {
    if (st.aim) { easeToAim(st, dt); return; }
    if (idle(st)) { st.yaw += dt * .08; }
  }
  // what is lit: the cluster under the pointer, else the function you clicked, else the list row under the pointer
  function lit(st) {
    if (st.picked >= 0) { return st.picked; }
    return st.pinned >= 0 ? st.pinned : st.held;
  }
  // a label that would cover an earlier one slides sideways, so each stays beside its own cluster
  function sideways(placed, x, y, w, h) {
    placed.forEach(function (r) { if (Math.abs(r.x - x) < (r.w + w) / 2 + 8 && Math.abs(r.y - y) < h + 4) { x = r.x + (x >= r.x ? 1 : -1) * ((r.w + w) / 2 + 8); } });
    return x;
  }
  function placePill(view, st, el, q, placed) {
    var c = view.L.cent[q], v = st.v, W = view.canvas.clientWidth;
    if (!c || !c.n) { el.style.opacity = 0; return; }
    v.copy(c.s).applyMatrix3(view.U.uRot.value);
    var front = v.z > -.15;
    v.project(view.cam);
    var y = (1 - v.y) / 2 * view.canvas.clientHeight - 12, w = el.offsetWidth;
    var x = Math.max(w / 2 + 6, Math.min(W - w / 2 - 6, sideways(placed, (v.x + 1) / 2 * W, y, w, el.offsetHeight)));
    placed.push({ x: x, y: y, w: w }); el.style.left = x + 'px'; el.style.top = y + 'px';
    var on = view.U.uSel.value;
    el.style.opacity = on >= 0 && on !== q ? .25 : front ? 1 : .35;
  }
  // one frame; false once the tree has been stopped
  function tick(view, st, now) {
    if (!st.running) { return false; }
    var dt = Math.min(.1, (now - st.last) / 1000), placed = [];
    st.last = now;
    turn(st, dt);
    st.eul.set(st.pitch, st.yaw, 0); st.rot.makeRotationFromEuler(st.eul); view.U.uRot.value.setFromMatrix4(st.rot);
    if (st.hover && !st.drag) { st.picked = nearest(view, st, st.hover.x, st.hover.y); showTip(view, st.picked, st.hover.x, st.hover.y); }
    view.U.uSel.value = lit(st);
    view.R3.render(view.scene, view.cam);
    view.pills.forEach(function (el, q) { placePill(view, st, el, q, placed); });
    return true;
  }

  // ---------- what the page can ask of the tree ----------
  // turn the tree until one function faces you, keep it lit, and name it in the corner card (the list's rows call this on click)
  function showFunction(view, st, id) {
    var c = view.L.cent[id], fn = view.D.fns[id], box = view.opts.selection;
    if (!c || !c.n || !fn) { return false; }
    st.aim = { yaw: Math.atan2(-c.s.x, c.s.z), pitch: clampUnit(Math.atan2(c.s.y, Math.hypot(c.s.x, c.s.z))) }; st.pinned = id;
    if (box) { describe(fn, box); box.hidden = false; }
    return true;
  }
  function controls(view, st) {
    var box = view.opts.selection;
    return {
      // light up one function by its place in the list (the "most tangled" rows call this on hover), or none with -1
      focus: function (id) { st.held = id; },
      show: function (id) { return showFunction(view, st, id); },
      // let go of a shown function: the tree starts turning again
      release: function () { st.pinned = -1; st.aim = null; if (box) { box.hidden = true; } },
      stop: function () { st.running = false; view.R3.dispose(); view.pills.forEach(function (el) { el.remove(); }); if (view.tip) { view.tip.remove(); } }
    };
  }

  function draw(canvas, D, opts) {
    var view = makeView(canvas, D, opts || {}), st = startState();
    function frame(now) { if (tick(view, st, now)) { requestAnimationFrame(frame); } }
    wirePointer(view, st);
    size(view); window.addEventListener('resize', function () { size(view); }); requestAnimationFrame(frame);
    return controls(view, st);
  }

  window.KryptonTree = { draw: draw };
})();
