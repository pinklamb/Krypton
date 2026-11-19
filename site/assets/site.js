/* Small helpers for the docs: use the palette picked on the landing page, step the wordmark away on scroll,
   close the Reference menu on an outside click or Escape, and mark the section being read in the page list. */
(function () {
  var scheme = null;
  try { scheme = localStorage.getItem('kr-scheme'); } catch (error) { scheme = null; }
  if (scheme === 'sand' || scheme === 'cocoa' || scheme === 'linen' || scheme === 'ink') document.documentElement.setAttribute('data-scheme', scheme);

  var top = document.querySelector('header.top');
  function fade() { if (top) top.classList.toggle('scrolled', window.scrollY > 60); }
  window.addEventListener('scroll', fade, { passive: true });
  fade();

  var list = document.querySelector('.pages details');
  if (list && window.matchMedia && matchMedia('(max-width: 900px)').matches) list.removeAttribute('open');

  var menu = document.querySelector('details.more');
  if (menu) {
    document.addEventListener('click', function (event) { if (!menu.contains(event.target)) menu.removeAttribute('open'); });
    document.addEventListener('keydown', function (event) { if (event.key === 'Escape') menu.removeAttribute('open'); });
  }

  // the `krypton` splash, frame for frame as the CLI draws it (client/src/loader.rs): one lavender diamond pulses beside the name
  // (◇ ◈ ◆ ◈, bright when full), settles on a bright ◆, then the codebase map starts. It plays on a loop while it is on screen.
  var PULSE = ['◇', '◈', '◆', '◈'], TICKS = 8, TICK = 110;
  var name = '  <span class="h">Kr</span><span class="l">ypton</span>';
  function diamond(tick) {
    var shape = PULSE[tick % PULSE.length];
    return '  <span class="' + (shape === '◆' ? 'h' : 'l') + '">' + shape + '</span>';
  }
  var still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  Array.prototype.forEach.call(document.querySelectorAll('pre[data-splash]'), function (pre) {
    var code = pre.querySelector('code'), prompt = '<span class="p">$</span> krypton\n', timers = [];
    var mapping = '\n\n<span class="d">Mapping your codebase ...</span>';
    function show(tick, done) { code.innerHTML = prompt + (done ? '  <span class="h">◆</span>' : diamond(tick)) + name + (done ? mapping : ''); }
    function play() {
      timers.forEach(clearTimeout); timers = [];
      if (still) { show(0, true); return; }
      for (var tick = 0; tick < TICKS; tick++) { timers.push(setTimeout(show.bind(null, tick, false), 400 + tick * TICK)); }
      timers.push(setTimeout(show.bind(null, 0, true), 400 + TICKS * TICK));
      timers.push(setTimeout(play, 400 + TICKS * TICK + 2600));
    }
    code.innerHTML = prompt;
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting) { play(); } else { timers.forEach(clearTimeout); timers = []; }
      }, { threshold: 0.3 }).observe(pre);
    } else { play(); }
  });

  var links = Array.prototype.slice.call(document.querySelectorAll('.pages .sub a[href^="#"]'));
  if (!links.length) return;
  var sections = links.map(function (link) { return document.getElementById(link.getAttribute('href').slice(1)); });
  function mark() {
    var current = 0;
    sections.forEach(function (section, index) { if (section && section.getBoundingClientRect().top <= 120) current = index; });
    links.forEach(function (link, index) { link.classList.toggle('on', index === current); });
  }
  window.addEventListener('scroll', mark, { passive: true });
  window.addEventListener('resize', mark);
  mark();
})();
