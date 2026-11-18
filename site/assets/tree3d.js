/* Krypton tree: draws a repository as the landing page draws garth, one point per node of its syntax tree, files on a sphere, imports as
   arcs, and the tangled functions lit in lavender. It needs three.js (r128) on the page.

   KryptonTree.draw(canvas, data, { labels: element, selection: element })  ->  { focus(index), show(index), release(), stop() }
     data: { files: [{ p, t, h }], edges: [[a, b]], fns: [{ name, times, file }] }, the outline /api/scan answers

   Same layout as the landing page (assets of land4.js), without the scroll story: it turns slowly, dragging turns it by hand, and the
   tangled function under the pointer is named and lit (the tree holds still while you look). */
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
    var root = [], out = [], P3 = [], HOT = [], SZ = [], AL = [], HIT = [], zero = new V(), cent = FN.map(function () { return { s: new V(), n: 0 }; });
    seq.forEach(function (k, s) { var u = .9 * (1 - 2 * (s + .5) / seq.length), v = s * golden, w = Math.sqrt(1 - u * u), p = new V(RX * w * Math.cos(v), RY * u, RZ * w * Math.sin(v)); root[k] = p; out[k] = new V(p.x / (RX * RX), p.y / (RY * RY), p.z / (RZ * RZ)).normalize(); });
    function push(p, sev, size, alpha, hit) { P3.push(p.x, p.y, p.z); HOT.push(sev); SZ.push(size); AL.push(alpha); HIT.push(hit); }
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
        push(p, sev, i === 0 ? 4 : h >= 0 ? 2.6 + 3.4 * SEV[h] : 1.7, h >= 0 ? .85 : .34, h);
        if (h >= 0) { cent[h].s.add(p); cent[h].n++; }
        if (i && pos[P[i]]) { var steps = h >= 0 ? 4 : 2, pp = pos[P[i]], s; for (s = 1; s <= steps; s++) { push(pp.clone().lerp(p, s / (steps + 1)), sev, h >= 0 ? 1.4 + SEV[h] : .9, h >= 0 ? .55 : .16, h); } }
      }
    });
    D.edges.forEach(function (e) { var a = root[e[0]], b = root[e[1]], s; if (!a || !b) { return; } for (s = 1; s < 18; s++) { var t = s / 18, mid = a.clone().add(b).multiplyScalar(.5 * .35), p = a.clone().multiplyScalar((1 - t) * (1 - t)).add(mid.clone().multiplyScalar(2 * (1 - t) * t)).add(b.clone().multiplyScalar(t * t)); push(p, -1, .8, .16); } });
    cent.forEach(function (c) { if (c.n) { c.s.multiplyScalar(1 / c.n); } });
    return { P3: P3, HOT: HOT, SZ: SZ, AL: AL, HIT: HIT, cent: cent };
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
    g.setAttribute('fn', new THREE.Float32BufferAttribute(L.HIT, 1));
    var U = { k: { value: 300 }, uSel: { value: -1 }, uRot: { value: new THREE.Matrix3() }, uInk: { value: new THREE.Vector3().fromArray(C.ink) }, uHot0: { value: new THREE.Vector3().fromArray(C.hot0) }, uHot1: { value: new THREE.Vector3().fromArray(C.hot1) } };
    var mat = new THREE.ShaderMaterial({
      uniforms: U, transparent: true, depthWrite: false, depthTest: false, blending: DARK[name] ? THREE.AdditiveBlending : THREE.NormalBlending,
      // the function under the pointer grows and turns the deepest lavender; the other tangled functions step back while one is picked
      vertexShader: 'attribute float sev; attribute float size; attribute float alpha; attribute float fn; uniform float k; uniform float uSel; uniform mat3 uRot; uniform vec3 uInk; uniform vec3 uHot0; uniform vec3 uHot1; varying vec3 vc; varying float va;' +
        'void main(){ bool picked = uSel >= 0.0 && abs(fn - uSel) < 0.5; bool other = uSel >= 0.0 && fn >= 0.0 && !picked;' +
        ' vc = sev < 0.0 ? uInk : (picked ? uHot1 : mix(uHot0, uHot1, sev)); va = alpha * (picked ? 1.6 : other ? 0.45 : 1.15);' +
        ' vec4 mv = modelViewMatrix * vec4(uRot * position, 1.0); gl_PointSize = size * (picked ? 1.7 : 1.0) * k / -mv.z; gl_Position = projectionMatrix * mv; }',
      fragmentShader: 'varying vec3 vc; varying float va; void main(){ float d = length(gl_PointCoord - .5) * 2.; if (d > 1.) discard; gl_FragColor = vec4(vc, min(1.0, va) * smoothstep(1., .45, d)); }'
    });
    var pts = new THREE.Points(g, mat), R3 = new THREE.WebGLRenderer({ canvas: canvas, antialias: false, alpha: false });
    pts.frustumCulled = false; R3.setPixelRatio(Math.min(devicePixelRatio, 2)); R3.setClearColor(C.bg, 1);
    var scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(30, 1, .1, 60); scene.add(pts); cam.position.set(0, 0, 5.4);
    // labels on the three worst functions, each pinned to its own cluster: "1  name  46.2× limit"
    var pills = (opts.labels ? D.fns.slice(0, 3) : []).map(function (f, q) {
      var el = document.createElement('div'), n = document.createElement('i'), t = document.createElement('span'), x = document.createElement('em');
      el.className = 'pill' + (q === 0 ? ' top' : ''); n.textContent = q + 1; t.textContent = f.name; x.textContent = f.times + '× limit';
      el.append(n, t, x); opts.labels.appendChild(el); return el;
    });
    var tip = null;
    if (opts.labels) { tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; opts.labels.appendChild(tip); }
    var hot = []; for (var n0 = 0; n0 < L.HIT.length; n0++) { if (L.HIT[n0] >= 0) { hot.push(n0); } }
    var yaw = .4, pitch = .12, aim = null, pinned = -1, drag = null, hover = null, picked = -1, held = -1, still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches, running = true, last = performance.now();
    var rot = new THREE.Matrix4(), eul = new THREE.Euler(), v = new THREE.Vector3(), pickV = new THREE.Vector3();

    function nearest(x, y) {
      var rect = canvas.getBoundingClientRect(), best = 14 * 14, found = -1;
      for (var q = 0; q < hot.length; q++) {
        var n = hot[q];
        pickV.set(L.P3[n * 3], L.P3[n * 3 + 1], L.P3[n * 3 + 2]).applyMatrix4(rot).project(cam);
        if (pickV.z < -1 || pickV.z > 1) { continue; }
        var px = (pickV.x + 1) * rect.width / 2, py = (1 - pickV.y) * rect.height / 2, dist = (px - x) * (px - x) + (py - y) * (py - y);
        if (dist < best) { best = dist; found = L.HIT[n]; }
      }
      return found;
    }
    function describe(fn, box) {
      var title = document.createElement('b'), where = document.createElement('span'), times = document.createElement('em');
      title.textContent = fn.name; where.textContent = fn.file; times.textContent = fn.times + '× the limit';
      box.replaceChildren(title, where, times);
    }
    function showTip(id, x, y) {
      if (!tip) { return; }
      if (id < 0 || !D.fns[id]) { tip.hidden = true; return; }
      describe(D.fns[id], tip); tip.hidden = false;
      var W = canvas.clientWidth; tip.style.left = Math.min(x + 14, W - tip.offsetWidth - 8) + 'px'; tip.style.top = Math.max(8, y - tip.offsetHeight - 10) + 'px';
    }
    canvas.addEventListener('pointerdown', function (e) { aim = null; drag = { x: e.clientX, y: e.clientY, yaw: yaw, pitch: pitch }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', function (e) {
      if (drag) { yaw = drag.yaw + (e.clientX - drag.x) * .008; pitch = Math.max(-1, Math.min(1, drag.pitch + (e.clientY - drag.y) * .006)); if (tip) { tip.hidden = true; } return; }
      var rect = canvas.getBoundingClientRect(); hover = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    });
    canvas.addEventListener('pointerleave', function () { hover = null; picked = -1; if (tip) { tip.hidden = true; } });
    canvas.addEventListener('pointerup', function (e) {
      if (!drag) { return; }
      var moved = Math.hypot(e.clientX - drag.x, e.clientY - drag.y); drag = null;
      if (moved > 5 || !opts.selection) { return; }
      var rect = canvas.getBoundingClientRect(), found = nearest(e.clientX - rect.left, e.clientY - rect.top);
      if (found >= 0 && D.fns[found]) { describe(D.fns[found], opts.selection); opts.selection.hidden = false; pinned = found; }
      else { pinned = -1; opts.selection.hidden = true; }
    });
    function size() {
      var w = canvas.clientWidth, h = canvas.clientHeight; R3.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix();
      // A narrow stage (a phone held upright) would crop the tree's sides, so the camera steps back until the whole width
      // fits; the dots shrink by only part of that step so they stay easy to see.
      var back = Math.min(2.2, Math.max(1, 1.5 / cam.aspect));
      cam.position.z = 5.4 * back;
      U.k.value = h / (2 * Math.tan(cam.fov * Math.PI / 360)) * .0055 * Math.sqrt(back);
    }
    function frame(now) {
      if (!running) { return; }
      var dt = Math.min(.1, (now - last) / 1000); last = now;
      if (aim) {
        // turning to show a function: ease the yaw (the short way round) and the pitch towards it
        var dy = Math.atan2(Math.sin(aim.yaw - yaw), Math.cos(aim.yaw - yaw)), k = still ? 1 : 1 - Math.exp(-dt / .25);
        yaw += dy * k; pitch += (aim.pitch - pitch) * k;
        if (Math.abs(dy) < .002 && Math.abs(aim.pitch - pitch) < .002) { aim = null; }
      } else if (!drag && !still && picked < 0 && held < 0 && pinned < 0) { yaw += dt * .08; }
      eul.set(pitch, yaw, 0); rot.makeRotationFromEuler(eul); U.uRot.value.setFromMatrix4(rot);
      if (hover && !drag) { picked = nearest(hover.x, hover.y); showTip(picked, hover.x, hover.y); }
      // what is lit: the cluster under the pointer, else the function you clicked, else the list row under the pointer
      U.uSel.value = picked >= 0 ? picked : pinned >= 0 ? pinned : held;
      R3.render(scene, cam);
      var W = canvas.clientWidth, H = canvas.clientHeight, placed = [];
      pills.forEach(function (el, q) {
        if (!L.cent[q] || !L.cent[q].n) { el.style.opacity = 0; return; }
        v.copy(L.cent[q].s).applyMatrix3(U.uRot.value);
        var front = v.z > -.15;
        v.project(cam);
        var x = (v.x + 1) / 2 * W, y = (1 - v.y) / 2 * H - 12, w = el.offsetWidth, h = el.offsetHeight;
        // a label that would cover an earlier one slides sideways, so each stays beside its own cluster
        placed.forEach(function (r) { if (Math.abs(r.x - x) < (r.w + w) / 2 + 8 && Math.abs(r.y - y) < h + 4) { x = r.x + (x >= r.x ? 1 : -1) * ((r.w + w) / 2 + 8); } });
        x = Math.max(w / 2 + 6, Math.min(W - w / 2 - 6, x));
        placed.push({ x: x, y: y, w: w }); el.style.left = x + 'px'; el.style.top = y + 'px';
        var lit = U.uSel.value;
        el.style.opacity = lit >= 0 && lit !== q ? .25 : front ? 1 : .35;
      });
      requestAnimationFrame(frame);
    }
    size(); window.addEventListener('resize', size); requestAnimationFrame(frame);
    return {
      // light up one function by its place in the list (the "most tangled" rows call this on hover), or none with -1
      focus: function (id) { held = id; },
      // turn the tree until one function faces you, keep it lit, and name it in the corner card (the list's rows call this on click)
      show: function (id) {
        var c = L.cent[id], fn = D.fns[id];
        if (!c || !c.n || !fn) { return false; }
        var yawTo = Math.atan2(-c.s.x, c.s.z), r = Math.hypot(c.s.x, c.s.z);
        aim = { yaw: yawTo, pitch: Math.max(-1, Math.min(1, Math.atan2(c.s.y, r))) }; pinned = id;
        if (opts.selection) { describe(fn, opts.selection); opts.selection.hidden = false; }
        return true;
      },
      // let go of a shown function: the tree starts turning again
      release: function () { pinned = -1; aim = null; if (opts.selection) { opts.selection.hidden = true; } },
      stop: function () { running = false; R3.dispose(); pills.forEach(function (el) { el.remove(); }); if (tip) { tip.remove(); } }
    };
  }

  window.KryptonTree = { draw: draw };
})();
