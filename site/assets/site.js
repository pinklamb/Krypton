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

  // the `krypton` splash, frame for frame as the CLI draws it (client/src/loader.rs): the outline in lavender along twelve cells,
  // a bright arrow head leading, then the name, then the codebase map starting. It plays on a loop while it is on screen.
  var PRISM = ['   /\\   ', '  /  \\  ', ' /    \\ ', ' \\    / ', '  \\  /  ', '   \\/   '];
  var PATH = [[0, 3], [1, 2], [2, 1], [3, 1], [4, 2], [5, 3], [5, 4], [4, 5], [3, 6], [2, 6], [1, 5], [0, 4]];
  function crystal(drawn) {
    var rows = PRISM.map(function (line) { return line.split('').map(function () { return ' '; }); });
    PATH.slice(0, drawn).forEach(function (cell) { rows[cell[0]][cell[1]] = '<span class="l">' + PRISM[cell[0]].charAt(cell[1]) + '</span>'; });
    if (drawn < PATH.length) { var at = PATH[drawn]; rows[at[0]][at[1]] = '<span class="h">' + (drawn < 6 ? 'v' : drawn === 6 ? '>' : '^') + '</span>'; }
    return rows.map(function (row) { return '  ' + row.join(''); }).join('\n');
  }
  var still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  Array.prototype.forEach.call(document.querySelectorAll('pre[data-splash]'), function (pre) {
    var code = pre.querySelector('code'), prompt = '<span class="p">$</span> krypton\n', timers = [];
    var name = '\n  <span class="h">Kr</span><span class="l">ypton</span>\n\n<span class="d">Mapping your codebase ...</span>';
    function show(drawn, done) { code.innerHTML = prompt + crystal(drawn) + (done ? name : ''); }
    function play() {
      timers.forEach(clearTimeout); timers = [];
      if (still) { show(PATH.length, true); return; }
      for (var step = 0; step <= PATH.length; step++) { timers.push(setTimeout(show.bind(null, step, false), 400 + step * 70)); }
      timers.push(setTimeout(show.bind(null, PATH.length, true), 400 + (PATH.length + 2) * 70));
      timers.push(setTimeout(play, 400 + (PATH.length + 2) * 70 + 2600));
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
