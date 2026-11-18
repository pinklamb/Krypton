/* Krypton tree: draws a repository as the landing page draws garth, one point per node of its syntax tree, files on a sphere, imports as
   arcs, and the tangled functions lit in lavender. It needs three.js (r128) on the page.

   KryptonTree.draw(canvas, data, { labels: element })  ->  { stop() }
     data: { files: [{ p, t, h }], edges: [[a, b]], fns: [{ name, times, file }] }, the outline /api/scan answers

   Same layout as the landing page (assets of land4.js), without the scroll story: it turns slowly, and dragging turns it by hand. */
(function () {
  'use strict';
  var CODES = '123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';

  // ---------- drawing ----------
  function rng(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; var t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  var DARK = { cocoa: 1, ink: 1 };
  var COLORS = {
    sand: { bg: 0xefe6da, ink: [.40, .34, .28], hot0: [.74, .63, .84], hot1: [.38, .22, .55] },
    cocoa: { bg: 0x1b1613, ink: [.62, .56, .50], hot0: [.42, .36, .66], hot1: [.84, .76, 1] },
    linen: { bg: 0xeceee6, ink: [.32, .35, .30], hot0: [.64, .76, .60], hot1: [.22, .42, .20] },
    ink: { bg: 0x050507, ink: [.62, .62, .70], hot0: [.42, .36, .92], hot1: [.84, .80, 1] }
  };

  function layout(D) {
    var V = THREE.Vector3, F = D.files, FN = D.fns, R = rng(5), i, j;
    var maxT = FN.reduce(function (a, f) { return Math.max(a, f.times); }, 1);
    var SEV = FN.map(function (f) { return Math.max(.25, Math.log(f.times + 1) / Math.log(maxT + 1)); });
    var RX = 1.45, RY = .98, RZ = 1.12, golden = Math.PI * (3 - Math.sqrt(5)), seq = [], dirs = {};
    F.forEach(function (f, k) { var d = f.p.split('/').slice(0, -1).join('/'); (dirs[d] = dirs[d] || []).push(k); });
    Object.keys(dirs).sort(function (a, b) { return dirs[b].length - dirs[a].length; }).forEach(function (d) { dirs[d].sort(function (a, b) { return F[b].t.length - F[a].t.length; }).forEach(function (k) { seq.push(k); }); });
    var root = [], out = [], P3 = [], HOT = [], SZ = [], AL = [], zero = new V(), cent = FN.map(function () { return { s: new V(), n: 0 }; });
    seq.forEach(function (k, s) { var u = .9 * (1 - 2 * (s + .5) / seq.length), v = s * golden, w = Math.sqrt(1 - u * u), p = new V(RX * w * Math.cos(v), RY * u, RZ * w * Math.sin(v)); root[k] = p; out[k] = new V(p.x / (RX * RX), p.y / (RY * RY), p.z / (RZ * RZ)).normalize(); });
    function push(p, sev, size, alpha) { P3.push(p.x, p.y, p.z); HOT.push(sev); SZ.push(size); AL.push(alpha); }
    F.forEach(function (f, k) {
      var P = f.t, n = P.length, kids = [], dep = new Array(n).fill(0), pos = new Array(n), dir = new Array(n), fid = new Array(n);
      for (i = 0; i < n; i++) { kids.push([]); fid[i] = CODES.indexOf(f.h.charAt(i)); }
      for (i = 1; i < n; i++) { if (P[i] >= 0 && P[i] < i) { dep[i] = dep[P[i]] + 1; kids[P[i]].push(i); } }
      var L0 = .05 + .017 * Math.log10(n + 1), ref = Math.abs(out[k].y) > .9 ? new V(1, 0, 0) : new V(0, 1, 0);
      pos[0] = root[k].clone(); dir[0] = out[k].clone();
      for (i = 0; i < n; i++) {
        var ks = kids[i], m = ks.length; if (!m || !pos[i]) { continue; }
        var d = dir[i], u = new V().crossVectors(d, ref).normalize(), w = new V().crossVectors(d, u).normalize(), th = m === 1 ? .22 : Math.min(.9, .3 + .075 * Math.sqrt(m)), base = dep[i] * 1.3 + k;
        for (j = 0; j < m; j++) {
          var c = ks[j], ph = base + j * (Math.PI * 2 / m), off = u.clone().multiplyScalar(Math.cos(ph)).add(w.clone().multiplyScalar(Math.sin(ph))), nd = d.clone().multiplyScalar(Math.cos(th)).add(off.multiplyScalar(Math.sin(th))).normalize();
          dir[c] = nd; pos[c] = pos[i].clone().add(nd.clone().multiplyScalar(L0 * Math.pow(.8, dep[c])));
        }
      }
      for (i = 0; i < n; i++) {
        if (!pos[i]) { continue; }
        var h = fid[i], sev = h >= 0 ? SEV[h] : -1, jit = h >= 0 ? new V(R() - .5, R() - .5, R() - .5).multiplyScalar((.05 + .012 * dep[i]) * (.6 + SEV[h])) : zero, p = pos[i].clone().add(jit);
        push(p, sev, i === 0 ? 4 : h >= 0 ? 2.6 + 3.4 * SEV[h] : 1.7, h >= 0 ? .85 : .34);
        if (h >= 0) { cent[h].s.add(p); cent[h].n++; }
        if (i && pos[P[i]]) { var steps = h >= 0 ? 4 : 2, pp = pos[P[i]], s; for (s = 1; s <= steps; s++) { push(pp.clone().lerp(p, s / (steps + 1)), sev, h >= 0 ? 1.4 + SEV[h] : .9, h >= 0 ? .55 : .16); } }
      }
    });
    D.edges.forEach(function (e) { var a = root[e[0]], b = root[e[1]], s; if (!a || !b) { return; } for (s = 1; s < 18; s++) { var t = s / 18, mid = a.clone().add(b).multiplyScalar(.5 * .35), p = a.clone().multiplyScalar((1 - t) * (1 - t)).add(mid.clone().multiplyScalar(2 * (1 - t) * t)).add(b.clone().multiplyScalar(t * t)); push(p, -1, .8, .16); } });
    cent.forEach(function (c) { if (c.n) { c.s.multiplyScalar(1 / c.n); } });
    return { P3: P3, HOT: HOT, SZ: SZ, AL: AL, cent: cent };
  }

  function scheme() {
    var name = document.documentElement.getAttribute('data-scheme') || 'sand';
    return COLORS[name] ? name : 'sand';
  }

  function draw(canvas, D, opts) {
    opts = opts || {};
    var L = layout(D), g = new THREE.BufferGeometry(), name = scheme(), C = COLORS[name];
    g.setAttribute('position', new THREE.Float32BufferAttribute(L.P3, 3));
    g.setAttribute('sev', new THREE.Float32BufferAttribute(L.HOT, 1));
    g.setAttribute('size', new THREE.Float32BufferAttribute(L.SZ, 1));
    g.setAttribute('alpha', new THREE.Float32BufferAttribute(L.AL, 1));
    var U = { k: { value: 300 }, uRot: { value: new THREE.Matrix3() }, uInk: { value: new THREE.Vector3().fromArray(C.ink) }, uHot0: { value: new THREE.Vector3().fromArray(C.hot0) }, uHot1: { value: new THREE.Vector3().fromArray(C.hot1) } };
    var mat = new THREE.ShaderMaterial({
      uniforms: U, transparent: true, depthWrite: false, depthTest: false, blending: DARK[name] ? THREE.AdditiveBlending : THREE.NormalBlending,
      vertexShader: 'attribute float sev; attribute float size; attribute float alpha; uniform float k; uniform mat3 uRot; uniform vec3 uInk; uniform vec3 uHot0; uniform vec3 uHot1; varying vec3 vc; varying float va;' +
        'void main(){ vc = sev < 0.0 ? uInk : mix(uHot0, uHot1, sev); va = alpha * 1.15; vec4 mv = modelViewMatrix * vec4(uRot * position, 1.0); gl_PointSize = size * k / -mv.z; gl_Position = projectionMatrix * mv; }',
      fragmentShader: 'varying vec3 vc; varying float va; void main(){ float d = length(gl_PointCoord - .5) * 2.; if (d > 1.) discard; gl_FragColor = vec4(vc, min(1.0, va) * smoothstep(1., .45, d)); }'
    });
    var pts = new THREE.Points(g, mat), R3 = new THREE.WebGLRenderer({ canvas: canvas, antialias: false, alpha: false });
    pts.frustumCulled = false; R3.setPixelRatio(Math.min(devicePixelRatio, 2)); R3.setClearColor(C.bg, 1);
    var scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(30, 1, .1, 60); scene.add(pts); cam.position.set(0, 0, 5.4);
    var pills = (opts.labels ? D.fns.slice(0, 3) : []).map(function (f, q) {
      var el = document.createElement('div'); el.className = 'pill' + (q === 0 ? ' top' : ''); el.textContent = (q + 1) + ' · ' + f.name + ' // ' + f.times + '× limit'; opts.labels.appendChild(el); return el;
    });
    var yaw = .4, pitch = .12, drag = null, still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches, running = true, last = performance.now();
    var rot = new THREE.Matrix4(), eul = new THREE.Euler(), v = new THREE.Vector3();
    canvas.addEventListener('pointerdown', function (e) { drag = { x: e.clientX, y: e.clientY, yaw: yaw, pitch: pitch }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', function (e) { if (!drag) { return; } yaw = drag.yaw + (e.clientX - drag.x) * .008; pitch = Math.max(-1, Math.min(1, drag.pitch + (e.clientY - drag.y) * .006)); });
    canvas.addEventListener('pointerup', function () { drag = null; });
    function size() {
      var w = canvas.clientWidth, h = canvas.clientHeight; R3.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix();
      U.k.value = h / (2 * Math.tan(cam.fov * Math.PI / 360)) * .0055;
    }
    function frame(now) {
      if (!running) { return; }
      var dt = Math.min(.1, (now - last) / 1000); last = now;
      if (!drag && !still) { yaw += dt * .08; }
      eul.set(pitch, yaw, 0); rot.makeRotationFromEuler(eul); U.uRot.value.setFromMatrix4(rot);
      R3.render(scene, cam);
      var W = canvas.clientWidth, H = canvas.clientHeight, placed = [];
      pills.forEach(function (el, q) {
        v.copy(L.cent[q].s).applyMatrix3(U.uRot.value); v.project(cam);
        var x = (v.x + 1) / 2 * W, y = (1 - v.y) / 2 * H - 14, w = el.offsetWidth, moved = true;
        while (moved) { moved = false; placed.forEach(function (r) { if (Math.abs(r.x - x) < (r.w + w) / 2 + 6 && Math.abs(r.y - y) < 28) { y = r.y - 30; moved = true; } }); }
        placed.push({ x: x, y: y, w: w }); el.style.left = x + 'px'; el.style.top = y + 'px'; el.style.opacity = 1;
      });
      requestAnimationFrame(frame);
    }
    size(); window.addEventListener('resize', size); requestAnimationFrame(frame);
    return { stop: function () { running = false; R3.dispose(); pills.forEach(function (el) { el.remove(); }); } };
  }

  window.KryptonTree = { draw: draw };
})();
