// The landing page's 3D field (index.html); needs three.js (loaded just before it).
// One field of points behind the page. Every point knows three places: a spot in the ambient background, its spot in the real garth
// syntax tree, and a speck of dust. The scroll position picks which two are in play and how far between them we are.
// The garth tree itself (its files, edges and tangled functions) is data, in garth-tree.json; the field starts once it arrives.
(function () {
  'use strict';
  var V = THREE.Vector3, ZERO = new THREE.Vector3();
  var CODES = '123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';
  // still pictures for anyone who asked their system for less motion (or #rm in the address, for testing)
  function prefersStill() {
    if (location.hash === '#rm') { return true; }
    return !!window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  }
  var REDUCE = prefersStill();

  // ---------- two palettes, one per theme. On dark, points glow and add up; on light, they are drawn darker and blended normally,
  // or they would wash out to white ----------
  var THEMES = {
    dark: { bg: 0x0b0509, add: true, ink: [.6, .59, .64], hot0: [.46, .36, .78], hot1: [.86, .76, 1], fix: [.9, .83, .72], amb: [.62, .61, .66], tree: 1, ambA: .45 },
    light: { bg: 0xe4d5c0, add: false, ink: [.40, .34, .28], hot0: [.74, .63, .84], hot1: [.38, .22, .55], fix: [.17, .14, .12], amb: [.58, .49, .40], tree: 1.25, ambA: .42 }
  };

  // ---------- the shaders: each point is placed in two of its three shapes and mixed between them ----------
  var VS = [
    'attribute vec3 pa; attribute vec3 ja; attribute vec3 jc; attribute float t; attribute float fid; attribute float sev; attribute vec3 perp; attribute float size; attribute vec4 wv; attribute vec3 du;',
    'uniform float amp; uniform float k; uniform float glow; uniform float uFrom; uniform float uTo; uniform float uMix; uniform float uTime; uniform float uAlpha; uniform float uScale; uniform float uFill; uniform float uShift; uniform float uAmb; uniform float uTreeA; uniform float uAmbA; uniform float uPick; uniform float uBurst; uniform vec3 uCen;',
    'uniform mat3 uRot; uniform vec2 uOff; uniform vec3 uInk; uniform vec3 uHot0; uniform vec3 uHot1; uniform vec3 uFix; uniform vec3 uAmbC;',
    'varying vec3 vc; varying float va;',
    'vec3 tree(){ float hot=fid>=0.0?1.0:0.0; vec3 A=pa+ja*amp; vec3 C=position+jc*amp; vec3 p=mix(A,C,t)+perp*sin(3.14159*t)*0.05*amp*hot*sev; if(abs(fid-uPick)<0.5){ p=uCen+(p-uCen)*(1.0+2.4*uBurst); } return uRot*(p*uScale); }',
    'vec3 wave(){ float x=wv.x, z=wv.y; float y=-1.0+0.2*sin(x*0.55+uTime*0.45+z*0.2)*cos(z*0.45-uTime*0.3)+0.06*sin(x*1.9+z*1.2+uTime*0.8); return vec3(x,y,z); }',
    // a drafting lattice: points run along grid lines on the floor, a slow ripple passing through
    'vec3 lattice(){ float gs=0.7; float x=wv.x, z=wv.y; if(wv.z<3.1416){ x=floor(x/gs+0.5)*gs; } else { z=floor(z/gs+0.5)*gs; }',
    '  float d=length(vec2(x,z+3.5)); return vec3(x,-1.05+0.09*sin(d*1.5-uTime*1.0)*exp(-d*0.06),z); }',
    // contour rings: nested ellipses on the floor, each turning its own way and breathing a little
    'vec3 rings(){ float i=floor((wv.x+7.5)/15.0*24.0); float r=0.75+i*0.27+0.04*sin(wv.z*2.0+uTime*0.7+i);',
    '  float a=(wv.y+11.0)/14.5*6.2832+uTime*0.05*(mod(i,2.0)*2.0-1.0)/(1.0+i*0.05); return vec3(r*cos(a)*1.25,-1.05+0.03*sin(a*3.0+uTime+i),r*sin(a)-3.2); }',
    // drifting threads: horizontal strands that flow sideways like a loom
    'vec3 threads(){ float lane=floor((wv.y+11.0)/14.5*30.0); float x=mod(wv.x+7.5+uTime*(0.18+0.08*mod(lane,3.0)),15.0)-7.5;',
    '  return vec3(x,-1.45+lane*0.1+0.28*sin(x*0.42+lane*0.55+uTime*0.35),-3.0-mod(lane,4.0)*0.4); }',
    // drift: sparse dots floating slowly upward and swaying, fading in and out
    'float driftH(){ return fract((wv.y+11.0)/14.5+uTime*0.01+0.37*fract(wv.z*7.13)); }',
    'vec3 drift(){ float h=driftH(); float sway=0.22*sin(uTime*0.25+wv.z*9.0+h*4.0);',
    '  return vec3(wv.x*0.95+sway, -2.4+h*5.2, -2.0-6.0*fract(wv.z*3.31)); }',
    // an orbit: a slow spiral disc, tilted towards the viewer
    'vec3 orbitW(float wind, float shrink){ float r=0.25+pow((wv.x+7.5)/15.0,0.75)*4.1; float arm=floor(wv.z/6.2832*3.0)*2.0944;',
    '  float a=arm+r*0.85+0.35*sin(wv.y*3.0)/(0.5+r)+uTime*0.1/(0.5+r*0.4)+wind*(1.0+0.6/(0.5+r)); r*=shrink; vec3 p=vec3(r*cos(a),0.05*sin(wv.y*7.0),r*sin(a));',
    '  float c=cos(1.15), s=sin(1.15); return vec3(p.x*mix(1.0,uFill,shrink)+uShift*mix(1.0,0.0,1.0-shrink),p.y*c-p.z*s-0.6*shrink,p.y*s+p.z*c-3.5*shrink); }',
    'vec3 orbit(){ return orbitW(0.0,1.0); }',
    'vec3 dust(){ return du+0.12*vec3(sin(uTime*0.21+wv.z), cos(uTime*0.17+wv.z*1.3), sin(uTime*0.13+wv.z*0.7)); }',
    'vec3 ambient(){ if(uAmb<0.5) return lattice(); if(uAmb<1.5) return rings(); if(uAmb<2.5) return threads(); if(uAmb<3.5) return orbit(); if(uAmb<4.5) return wave(); return drift(); }',
    'vec3 shp(float s){ return s<0.5?ambient():(s<1.5?tree():dust()); }',
    'void look(float s, vec3 p, out vec3 c, out float a, out float z){',
    '  if(s<0.5){ bool orb=uAmb>2.5&&uAmb<3.5; float f=orb?uFill:1.0; float tip=orb?1.0-smoothstep(0.62,1.0,(wv.x+7.5)/15.0):1.0; c=uAmbC; a=min(1.0,uAmbA*smoothstep(-11.5,-4.0,p.z)*smoothstep(3.6,1.0,p.z)*(orb?0.5:1.0)*f*tip); z=1.25*sqrt(f); if(uAmb>4.5){ a=min(1.0,a*1.5*pow(sin(3.1416*driftH()),2.0))*step(fract(wv.z*13.7),0.3); z=2.2; } }',
    '  else if(s<1.5){ float hot=fid>=0.0?1.0:0.0; float gl=hot*glow*amp; float fixd=hot*glow*(1.0-amp);',
    '    c=mix(mix(uInk,mix(uHot0,uHot1,sev),gl),uFix,fixd);',
    '    a=mix(mix(t<1.0?0.16:0.34,t<1.0?0.55:0.85,gl),t<1.0?0.3:0.5,fixd)*uTreeA;',
    '    z=mix(t<1.0?0.9:1.6,size,gl); a*=uScale; if(abs(fid-uPick)<0.5){ z*=1.0+0.6*uBurst; a=min(1.0,a*(1.0+0.8*uBurst)); } }',
    '  else { c=uAmbC; a=uAmbA*0.6; z=1.0; } }',
    'void main(){ float boost=1.0; float m=smoothstep(wv.w*0.45,wv.w*0.45+0.55,uMix);',
    '  vec3 p0=shp(uFrom); vec3 p1=shp(uTo); vec3 p=mix(p0,p1,m); if(uAmb>2.5&&uAmb<3.5&&uFrom+uTo>0.5&&uFrom+uTo<1.5){ float mm=smoothstep(wv.w*0.25,wv.w*0.25+0.75,uMix); float q=uFrom<0.5?mm:1.0-mm; float qg=uFrom<0.5?uMix:1.0-uMix; float w=smoothstep(0.0,0.55,qg); float b=smoothstep(0.5,1.0,q); vec3 ow=orbitW(w*2.6,1.0-0.55*w); boost=1.0+2.6*w*(1.0-b); p=mix(ow,tree(),b); p0=uFrom<0.5?ow:p0; p1=uTo<0.5?ow:p1; m=uFrom<0.5?b:1.0-b; } vec3 c0; float a0; float z0; vec3 c1; float a1; float z1; look(uFrom,p0,c0,a0,z0); look(uTo,p1,c1,a1,z1);',
    '  vc=mix(c0,c1,m); va=min(1.0,mix(a0,a1,m)*uAlpha*boost); float sz=mix(z0,z1,m);',
    '  vec4 mv=modelViewMatrix*vec4(p,1.0); gl_PointSize=sz*k/-mv.z; gl_Position=projectionMatrix*mv; gl_Position.xy+=uOff*gl_Position.w; }'
  ].join('\n');
  var FS = 'varying vec3 vc; varying float va; void main(){ float d=length(gl_PointCoord-.5)*2.; if(d>1.) discard; gl_FragColor=vec4(vc, va*smoothstep(1.,.45,d)); }';
  // the point attributes and the buffer each one is read from
  var ATTRS = [['position', 'PC', 3], ['pa', 'PA', 3], ['ja', 'JA', 3], ['jc', 'JC', 3], ['t', 'TT', 1], ['fid', 'FID', 1], ['sev', 'SV', 1], ['perp', 'PERP', 3], ['size', 'SZ', 1], ['wv', 'WV', 4], ['du', 'DU', 3]];

  // ---------- small helpers ----------
  function rng(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; var t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function clamp(x) { return Math.min(1, Math.max(0, x)); }
  function smooth(x) { x = clamp(x); return x * x * (3 - 2 * x); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function docTop(el) { return el.getBoundingClientRect().top + window.scrollY; }
  function v3c(a) { return new V(a[0], a[1], a[2]); }
  function shake(R) { return new V(R() - .5, R() - .5, R() - .5); }

  // ---------- the tree: where each file's root sits ----------
  // files are ordered by folder (biggest folder first), then by size, so neighbours on the spiral share a folder
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
      var u = .9 * (1 - 2 * (s + .5) / seq.length), v = s * golden, w = Math.sqrt(1 - u * u), p = new V(RX * w * Math.cos(v), RY * u, RZ * w * Math.sin(v));
      root[k] = p; out[k] = new V(p.x / (RX * RX), p.y / (RY * RY), p.z / (RZ * RZ)).normalize();
    });
    return { root: root, out: out };
  }

  // ---------- the tree: every node and branch as points ----------
  // the buffers every point writes into; each point also gets its background spot (wv) and its dust spot (du)
  function pointBuffers(sev) {
    var A = { PA: [], PC: [], JA: [], JC: [], TT: [], FID: [], SV: [], PERP: [], SZ: [], WV: [], DU: [] }, R2 = rng(11);
    function push(pa, pc, ja, jc, t, fid, perp, size) {
      A.PA.push(pa.x, pa.y, pa.z); A.PC.push(pc.x, pc.y, pc.z); A.JA.push(ja.x, ja.y, ja.z); A.JC.push(jc.x, jc.y, jc.z);
      A.TT.push(t); A.FID.push(fid); A.SV.push(fid >= 0 ? sev[fid] : 0); A.PERP.push(perp.x, perp.y, perp.z); A.SZ.push(size);
      A.WV.push((R2() * 2 - 1) * 7.5, -11 + R2() * 14.5, R2() * 6.283, R2());
      var dx = R2() * 2 - 1, dy = R2() * 2 - 1, dz = R2() * 2 - 1, dl = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1, rr = 3.9 * Math.cbrt(R2());
      A.DU.push(dx / dl * rr * 1.5, dy / dl * rr, dz / dl * rr - 1);
    }
    return { arrays: A, push: push };
  }
  // a file's syntax tree: each node's children, its depth, and which tangled function (if any) it belongs to
  function nodeTable(f) {
    var P = f.t, n = P.length, kids = [], dep = new Array(n).fill(0), fid = [], i;
    for (i = 0; i < n; i++) { kids.push([]); fid.push(CODES.indexOf(f.h.charAt(i))); }
    for (i = 1; i < n; i++) { dep[i] = dep[P[i]] + 1; kids[P[i]].push(i); }
    return { P: P, n: n, kids: kids, dep: dep, fid: fid };
  }
  // a node's children fan out around its direction, each generation a little shorter
  function fanOut(N, i, grow) {
    var ks = N.kids[i], m = ks.length;
    if (!m) { return; }
    var d = grow.dir[i], u = new V().crossVectors(d, grow.ref).normalize(), w = new V().crossVectors(d, u).normalize();
    var th = m === 1 ? .22 : Math.min(.9, .3 + .075 * Math.sqrt(m)), base = N.dep[i] * 1.3 + grow.k;
    ks.forEach(function (c, j) {
      var ph = base + j * (Math.PI * 2 / m), off = u.clone().multiplyScalar(Math.cos(ph)).add(w.clone().multiplyScalar(Math.sin(ph)));
      var nd = d.clone().multiplyScalar(Math.cos(th)).add(off.multiplyScalar(Math.sin(th))).normalize();
      grow.dir[c] = nd; grow.pos[c] = grow.pos[i].clone().add(nd.clone().multiplyScalar(grow.L0 * Math.pow(.8, N.dep[c])));
    });
  }
  // where every node of file k sits, grown from the file's root
  function growFile(N, k, roots) {
    var out = roots.out[k], i;
    var grow = { k: k, L0: .05 + .017 * Math.log10(N.n + 1), ref: Math.abs(out.y) > .9 ? new V(1, 0, 0) : new V(0, 1, 0), pos: [roots.root[k].clone()], dir: [out.clone()] };
    for (i = 0; i < N.n; i++) { fanOut(N, i, grow); }
    return grow.pos;
  }
  // tangled nodes shake in proportion to their function's severity; the rest stay still
  function jitter(N, sev, R) {
    return N.fid.map(function (h, i) { return h >= 0 ? shake(R).multiplyScalar((.05 + .012 * N.dep[i]) * (.6 + sev[h])) : ZERO; });
  }
  function nodeSize(i, h, sev) {
    if (i === 0) { return 4; }
    return h >= 0 ? 2.6 + 3.4 * sev[h] : 1.7;
  }
  // a few points along the branch up to the parent, more on tangled ones
  function emitBranch(N, i, tree, perp, build) {
    var h = N.fid[i], up = N.P[i], steps = h >= 0 ? 4 : 2, size = h >= 0 ? 1.4 + build.sev[h] : .9, s;
    for (s = 1; s <= steps; s++) { build.buf.push(tree.pos[up], tree.pos[i], tree.jit[up], tree.jit[i], s / (steps + 1), h, perp, size); }
  }
  // every node is a point; tangled ones also add to their function's centre (where its label goes)
  function emitFile(N, tree, build) {
    N.fid.forEach(function (h, i) {
      var perp = shake(build.R).normalize();
      build.buf.push(tree.pos[i], tree.pos[i], tree.jit[i], tree.jit[i], 1, h, perp, nodeSize(i, h, build.sev));
      if (h >= 0) { build.cent[h].s.add(tree.pos[i]); build.cent[h].n++; }
      if (i) { emitBranch(N, i, tree, perp, build); }
    });
  }
  // the import edges between files: curves bowed towards the middle
  function emitEdges(edges, root, buf) {
    edges.forEach(function (e) {
      var a = root[e[0]], b = root[e[1]], mid = a.clone().add(b).multiplyScalar(.5).multiplyScalar(.35), s;
      for (s = 1; s < 18; s++) {
        var t = s / 18, p = a.clone().multiplyScalar((1 - t) * (1 - t)).add(mid.clone().multiplyScalar(2 * (1 - t) * t)).add(b.clone().multiplyScalar(t * t));
        pushEdgePoint(buf, p);
      }
    });
  }
  function pushEdgePoint(buf, p) { buf.push(p, p, ZERO, ZERO, 1, -1, ZERO, .8); }
  function geometry(A) {
    var g = new THREE.BufferGeometry();
    ATTRS.forEach(function (a) { g.setAttribute(a[0], new THREE.Float32BufferAttribute(A[a[1]], a[2])); });
    return g;
  }
  // the whole tree: the points' geometry, plus the centre of each tangled function
  function buildTree(D) {
    var maxT = D.fns.reduce(function (a, f) { return Math.max(a, f.times); }, 1);
    var sev = D.fns.map(function (f) { return Math.max(.25, Math.log(f.times + 1) / Math.log(maxT + 1)); });
    var roots = fileRoots(D.files), R = rng(5);
    var build = { R: R, sev: sev, buf: pointBuffers(sev), cent: D.fns.map(function () { return { s: new V(), n: 0 }; }) };
    D.files.forEach(function (f, k) {
      var N = nodeTable(f), pos = growFile(N, k, roots);
      emitFile(N, { pos: pos, jit: jitter(N, sev, R) }, build);
    });
    emitEdges(D.edges, roots.root, build.buf);
    build.cent.forEach(function (c) { if (c.n) { c.s.multiplyScalar(1 / c.n); } });
    return { geometry: geometry(build.buf.arrays), cent: build.cent };
  }

  // ---------- the view: renderer, camera and the shader's uniforms ----------
  // which background: ?bg=lattice|rings|threads|wave|drift in the address, else drift
  function ambientIndex() {
    var asked = /[?&]bg=(lattice|rings|threads|wave|drift)/.exec(location.search);
    return ['lattice', 'rings', 'threads', 'orbit', 'wave', 'drift'].indexOf(asked ? asked[1] : 'drift');
  }
  function uniforms() {
    return { uAmb: { value: ambientIndex() }, amp: { value: 1 }, k: { value: 300 }, glow: { value: 0 }, uFrom: { value: 0 }, uTo: { value: 0 }, uMix: { value: 0 }, uTime: { value: 0 },
      uAlpha: { value: 1 }, uScale: { value: 1 }, uFill: { value: 1 }, uShift: { value: 1.9 }, uTreeA: { value: 1 }, uAmbA: { value: .5 }, uPick: { value: -9 }, uBurst: { value: 0 }, uCen: { value: new V() },
      uRot: { value: new THREE.Matrix3() }, uOff: { value: new THREE.Vector2() }, uInk: { value: new V() }, uHot0: { value: new V() }, uHot1: { value: new V() }, uFix: { value: new V() }, uAmbC: { value: new V() } };
  }
  // the view, or null when the browser has no WebGL (the page then keeps its plain background)
  function makeView(cv, g) {
    var R3;
    try { R3 = new THREE.WebGLRenderer({ canvas: cv, antialias: false, alpha: false }); } catch (err) { return null; }
    R3.setPixelRatio(Math.min(devicePixelRatio, 2));
    var U = uniforms(), mat = new THREE.ShaderMaterial({ uniforms: U, vertexShader: VS, fragmentShader: FS, transparent: true, depthWrite: false, depthTest: false });
    var pts = new THREE.Points(g, mat), scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(30, 1, .1, 60);
    pts.frustumCulled = false; scene.add(pts);
    return { cv: cv, R3: R3, U: U, mat: mat, scene: scene, cam: cam };
  }
  // the page's toggle sends kr-theme; this repaints the points in the chosen theme's colours
  function paintTheme(view, name) {
    var T = THEMES[name] || THEMES.dark, U = view.U;
    view.R3.setClearColor(T.bg, 1); view.mat.blending = T.add ? THREE.AdditiveBlending : THREE.NormalBlending; view.mat.needsUpdate = true;
    U.uInk.value.copy(v3c(T.ink)); U.uHot0.value.copy(v3c(T.hot0)); U.uHot1.value.copy(v3c(T.hot1)); U.uFix.value.copy(v3c(T.fix)); U.uAmbC.value.copy(v3c(T.amb));
    U.uTreeA.value = T.tree; U.uAmbA.value = T.ambA;
  }
  // fit the canvas to the window. The orbit background stretches only in width, just enough for its outer ring (4.35 from its centre,
  // 10.7 from the camera, centre 1.9 to the right) to reach past the left edge; the shader enlarges stretched points to match (uFill)
  function sizeView(view) {
    var w = view.cv.clientWidth, h = view.cv.clientHeight, cam = view.cam;
    view.R3.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix();
    view.U.k.value = h / (2 * Math.tan(cam.fov * Math.PI / 360)) * .0055;
    var halfWide = Math.tan(cam.fov * Math.PI / 360) * 10.7 * cam.aspect;
    view.U.uFill.value = Math.max(1, (halfWide * 1.05 + 1.9) / 4.35);
  }

  // ---------- the story: which shape is showing at each scroll position, and the captions that come and go with it ----------
  var FADE = .06;
  function makeStory(track) {
    var caps = [].slice.call(document.querySelectorAll('[data-show]')).map(function (el) {
      var ab = el.getAttribute('data-show').split(' ').map(Number); return { el: el, a: ab[0], b: ab[1], key: -1 };
    });
    return { track: track, caps: caps, trTop: 0, trLen: 1, keys: [], S: { from: 0, to: 0, mix: 0, glow: 0, amp: 1, a: 1, x: 0, oy: 0, s: 1 } };
  }
  function progress(st, y) { return clamp((y - st.trTop) / st.trLen); }
  function at(st, p) { return st.trTop + p * st.trLen; }
  function key(y, shape, o) { return { y: y, shape: shape, glow: o.glow || 0, amp: o.amp === undefined ? 1 : o.amp, a: o.a === undefined ? 1 : o.a, x: o.x || 0, oy: o.oy || 0, s: o.s || 1 }; }
  // the key frames: background, then the tree (plain, glowing, fixed) beside the walkthrough, then dust, then the background again
  function storyKeys(st, vh) {
    var wide = innerWidth >= 900, first = docTop(document.getElementById('rows')), start = docTop(document.getElementById('start'));
    var T = { x: wide ? .3 : 0, oy: wide ? 0 : .4, s: wide ? 1 : .6 };
    var G = Object.assign({ glow: 1 }, T), A = Object.assign({ glow: 1, amp: 0 }, T);
    return [
      key(0, 0, {}), key(vh * .15, 0, {}),
      key(at(st, .14), 1, T), key(at(st, .45), 1, T), key(at(st, .53), 1, G), key(at(st, .74), 1, G), key(at(st, .9), 1, A), key(at(st, 1), 1, A),
      key(first + vh * .1, 2, { a: .7 }), key(Math.max(first + vh * .2, start - vh * .8), 2, { a: .7 }),
      key(start - vh * .1, 0, { a: .9 })
    ];
  }
  function layoutStory(st) {
    var vh = innerHeight;
    st.trTop = docTop(st.track); st.trLen = Math.max(1, st.track.offsetHeight - vh);
    st.keys = storyKeys(st, vh);
  }
  // the shapes and settings between the two key frames around y
  function state(st, y) {
    var keys = st.keys, S = st.S, n = 0;
    while (n < keys.length - 2 && y >= keys[n + 1].y) { n++; }
    var A = keys[n], B = keys[n + 1], t = B.y > A.y ? clamp((y - A.y) / (B.y - A.y)) : 1, e = smooth(t);
    S.from = A.shape; S.to = B.shape; S.mix = A.shape === B.shape ? 0 : t;
    if (S.mix >= 1) { S.from = B.shape; S.mix = 0; }
    ['glow', 'amp', 'a', 'x', 'oy', 's'].forEach(function (q) { S[q] = lerp(A[q], B[q], e); });
    return S;
  }
  function showCaption(c, p, w) {
    var s = c.el.style, dir = p < c.a + FADE ? 1 : -1;
    s.opacity = w.toFixed(3); s.visibility = w < .005 ? 'hidden' : 'visible'; s.setProperty('--dy', (REDUCE ? 0 : dir * (1 - w) * 18).toFixed(1) + 'px');
    s.filter = REDUCE || w > .995 ? 'none' : 'blur(' + ((1 - w) * 10).toFixed(1) + 'px)';
    c.el.setAttribute('aria-hidden', w < .5 ? 'true' : 'false');
  }
  function captions(st, p) {
    st.caps.forEach(function (c) {
      var w = smooth((p - c.a) / FADE) * (1 - smooth((p - (c.b - FADE)) / FADE)), kk = Math.round(w * 200);
      if (kk !== c.key) { c.key = kk; showCaption(c, p, w); }
    });
  }

  // ---------- labels on the three worst functions, and the purity counter ----------
  function makeLabels(stage, fns, cent) {
    var pills = fns.slice(0, 3).map(function (f, q) { var el = document.createElement('div'); el.className = 'pill' + (q === 0 ? ' top' : ''); stage.appendChild(el); return { el: el, q: q, txt: '' }; });
    return { pills: pills, fns: fns, cent: cent, pur: document.getElementById('pur'), hud: document.getElementById('hud'), gspec: document.getElementById('gspec'), v3: new V() };
  }
  function labelText(f, q, p, s) {
    if (p < .335) { return f.name; }
    if (s.glow < .5) { return f.fp; }
    if (s.amp > .5) { return (q + 1) + ' · ' + f.name + ' · ' + f.times + 'x limit'; }
    return f.name + ' · split (illustration)';
  }
  // a label that would sit on an earlier one moves up a row until it is clear
  function clearOf(placed, x, y, w) {
    var moved = true;
    while (moved) { moved = false; placed.forEach(function (r) { if (Math.abs(r.x - x) < (r.w + w) / 2 + 6 && Math.abs(r.y - y) < 28) { y = r.y - 30; moved = true; } }); }
    return y;
  }
  function placePill(L, view, o, ctx) {
    var tx = labelText(L.fns[o.q], o.q, ctx.p, ctx.s);
    if (tx !== o.txt) { o.txt = tx; o.el.textContent = tx; }
    L.v3.copy(L.cent[o.q].s).multiplyScalar(ctx.s.s).applyMatrix3(view.U.uRot.value); L.v3.project(view.cam);
    var x = (L.v3.x + ctx.s.x + 1) / 2 * view.cv.clientWidth, w = o.el.offsetWidth;
    var y = clearOf(ctx.placed, x, (1 - (L.v3.y + ctx.s.oy)) / 2 * view.cv.clientHeight - 14, w);
    ctx.placed.push({ x: x, y: y, w: w }); o.el.style.left = x + 'px'; o.el.style.top = y + 'px';
  }
  // the labels show only while the tree stands still in the walkthrough; once the fix is shown, only the worst one stays
  function pillVisible(o, p, s) {
    if (s.from !== 1 || s.mix !== 0 || p <= .26 || p >= .995) { return false; }
    return s.amp >= .5 || o.q === 0;
  }
  function placeLabels(L, view, p, s) {
    var ctx = { p: p, s: s, placed: [] };
    L.pills.forEach(function (o) {
      var vis = pillVisible(o, p, s);
      o.el.style.opacity = vis ? 1 : 0;
      if (vis) { placePill(L, view, o, ctx); }
    });
    showHud(L, p);
  }
  // the purity counter climbs from 62 to 81 as the fix lands
  function showHud(L, p) {
    var inside = p < .995, pv = Math.round(62 + 19 * smooth((p - .74) / .16));
    L.hud.style.opacity = inside && p > .08 ? 1 : 0; L.gspec.classList.toggle('on', inside && p > .1);
    if (+L.pur.textContent !== pv) { L.pur.textContent = pv; }
  }

  // ---------- an easter egg: click a lavender cluster and that function's whole subtree blooms out; click again to fold it ----------
  function treeShown(s) { return !!s && s.from === 1 && s.mix === 0 && s.glow > .5; }
  function busy(t) { return t.closest && t.closest('a, button, input, select, textarea, summary, label, code, pre, .fig, .pill'); }
  // the tangled function whose centre is within 46px of (x, y) on screen, or -1
  function clusterAt(egg, view, x, y) {
    var s = egg.s, best = -1, bd = 46 * 46;
    if (!treeShown(s)) { return -1; }
    egg.cent.forEach(function (c, i) {
      if (!c.n) { return; }
      egg.ndc.copy(c.s).multiplyScalar(s.s).applyMatrix3(view.U.uRot.value).project(view.cam);
      var px = (egg.ndc.x + s.x + 1) / 2 * view.cv.clientWidth - x, py = (1 - (egg.ndc.y + s.oy)) / 2 * view.cv.clientHeight - y, d = px * px + py * py;
      if (d < bd) { bd = d; best = i; }
    });
    return best;
  }
  // a click on a cluster opens it; a click on the open one, or anywhere else, folds it
  function eggClick(egg, view, e) {
    if (busy(e.target) || String(getSelection()).length) { return; }
    var hit = clusterAt(egg, view, e.clientX, e.clientY);
    if (hit < 0 || hit === egg.on) { egg.want = 0; return; }
    egg.on = hit; egg.want = 1; view.U.uPick.value = hit; view.U.uCen.value.copy(egg.cent[hit].s);
  }
  function eggHover(egg, view, e) {
    var over = !busy(e.target) && clusterAt(egg, view, e.clientX, e.clientY) >= 0;
    document.body.style.cursor = over ? 'pointer' : '';
  }
  function makeEgg(view, cent) {
    var egg = { on: -1, want: 0, s: null, cent: cent, ndc: new V() };
    document.addEventListener('click', function (e) { eggClick(egg, view, e); });
    document.addEventListener('mousemove', function (e) { eggHover(egg, view, e); });
    return egg;
  }
  // the bloom eases towards open or shut, and the picked cluster is let go once it has folded back
  function bloom(egg, U, s, dt) {
    egg.s = s;
    if (!treeShown(s)) { egg.want = 0; }
    var b = U.uBurst.value, to = egg.want;
    b = REDUCE ? to : b + (to - b) * (1 - Math.exp(-dt / .35));
    if (to === 0 && b < .002) { b = 0; egg.on = -1; U.uPick.value = -9; }
    U.uBurst.value = b;
  }

  // ---------- the loop ----------
  function setUniforms(view, s, p, run) {
    var U = view.U;
    run.eul.set(.25, run.yaw + p * .9, 0); run.rot.makeRotationFromEuler(run.eul); U.uRot.value.setFromMatrix4(run.rot);
    U.uFrom.value = s.from; U.uTo.value = s.to; U.uMix.value = s.mix; U.glow.value = s.glow; U.amp.value = s.amp; U.uAlpha.value = s.a; U.uScale.value = s.s; U.uOff.value.set(s.x, s.oy);
  }
  // the drawn scroll position eases after the real one, so the field glides instead of jumping
  function ease(run, dt) {
    var Y = scrollY;
    run.drawn = REDUCE ? Y : run.drawn + (Y - run.drawn) * (1 - Math.exp(-dt / .3));
    if (Math.abs(Y - run.drawn) < .5) { run.drawn = Y; }
    return Y;
  }
  function startLoop(field) {
    var view = field.view, run = { drawn: scrollY, last: performance.now(), yaw: .4, rot: new THREE.Matrix4(), eul: new THREE.Euler() };
    function frame(now) {
      var dt = Math.min(.1, (now - run.last) / 1000), Y = ease(run, dt), s = state(field.story, run.drawn), p = progress(field.story, run.drawn);
      run.last = now;
      if (!REDUCE) { run.yaw += dt * .06; view.U.uTime.value += dt; }
      bloom(field.egg, view.U, s, dt);
      setUniforms(view, s, p, run);
      view.cam.position.set(0, .3, 7.2); view.cam.lookAt(0, 0, 0);
      captions(field.story, progress(field.story, Y)); placeLabels(field.labels, view, p, s);
      view.R3.render(view.scene, view.cam); requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  // ---------- the page around it: the walkthrough's two jump buttons, and the sections that reveal as they scroll in ----------
  function wirePage(story) {
    document.getElementById('tb').addEventListener('click', function () { scrollTo({ top: at(story, .62), behavior: REDUCE ? 'auto' : 'smooth' }); });
    document.getElementById('ta').addEventListener('click', function () { scrollTo({ top: at(story, .97), behavior: REDUCE ? 'auto' : 'smooth' }); });
    var io = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }); }, { threshold: .2 });
    [].forEach.call(document.querySelectorAll('.reveal'), function (el) { io.observe(el); });
  }

  function startField(D) {
    var cv = document.getElementById('scene'), tree = buildTree(D), view = makeView(cv, tree.geometry);
    if (!view) { document.documentElement.classList.add('no-gl'); return; }
    paintTheme(view, document.documentElement.getAttribute('data-theme'));
    document.addEventListener('kr-theme', function (event) { paintTheme(view, event.detail); });
    var field = { view: view, story: makeStory(document.getElementById('story')), labels: makeLabels(document.getElementById('stage'), D.fns, tree.cent), egg: makeEgg(view, tree.cent) };
    function layout() { layoutStory(field.story); sizeView(view); }
    addEventListener('resize', layout);
    wirePage(field.story);
    function go() { layout(); cv.classList.add('on'); startLoop(field); }
    if (document.fonts && document.fonts.ready) { document.fonts.ready.then(go); } else { go(); }
  }

  function treeData(r) {
    if (!r.ok) { throw new Error('garth-tree.json: HTTP ' + r.status); }
    return r.json();
  }
  function loadTree() {
    fetch('assets/garth-tree.json?v=1').then(treeData).then(startField).catch(function (err) { console.error('landing 3D field:', err); });
  }
  loadTree();
})();
