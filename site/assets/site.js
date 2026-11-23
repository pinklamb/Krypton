/* Small helpers for the docs: step the wordmark away on scroll, close the Reference menu on an outside click or Escape, and mark the
   section being read in the page list. Every page is cocoa, set on its <html> tag. */
(function () {
  var STILL = !!window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  // the wordmark steps out of the way once the page scrolls
  function startHeader() {
    var top = document.querySelector('header.top');
    if (!top) { return; }
    function fade() { top.classList.toggle('scrolled', window.scrollY > 60); }
    window.addEventListener('scroll', fade, { passive: true });
    fade();
  }

  // on a phone the page list starts folded
  function foldPageList() {
    var list = document.querySelector('.pages details');
    if (list && window.matchMedia && matchMedia('(max-width: 900px)').matches) { list.removeAttribute('open'); }
  }

  // the Reference menu closes on a click outside it or on Escape
  function wireMenu() {
    var menu = document.querySelector('details.more');
    if (!menu) { return; }
    document.addEventListener('click', function (event) { if (!menu.contains(event.target)) { menu.removeAttribute('open'); } });
    document.addEventListener('keydown', function (event) { if (event.key === 'Escape') { menu.removeAttribute('open'); } });
  }

  // ---------- the `krypton` splash, frame for frame as the CLI draws it (client/src/loader.rs): one lavender diamond pulses beside the name
  // (◇ ◈ ◆ ◈, bright when full), settles on a bright ◆, then the codebase map starts. It plays on a loop while it is on screen. ----------
  var PULSE = ['◇', '◈', '◆', '◈'], TICKS = 8, TICK = 110;
  var NAME = '  <span class="h">Kr</span><span class="l">ypton</span>', PROMPT = '<span class="p">$</span> krypton\n';
  var MAPPING = '\n\n<span class="d">Mapping your codebase ...</span>';
  function diamond(tick) {
    var shape = PULSE[tick % PULSE.length];
    return '  <span class="' + (shape === '◆' ? 'h' : 'l') + '">' + shape + '</span>';
  }
  function splashFrame(tick, done) { return PROMPT + (done ? '  <span class="h">◆</span>' : diamond(tick)) + NAME + (done ? MAPPING : ''); }
  function startSplash(pre) {
    var code = pre.querySelector('code'), timers = [];
    function show(tick, done) { code.innerHTML = splashFrame(tick, done); }
    function stop() { timers.forEach(clearTimeout); timers = []; }
    function play() {
      stop();
      if (STILL) { show(0, true); return; }
      for (var tick = 0; tick < TICKS; tick++) { timers.push(setTimeout(show.bind(null, tick, false), 400 + tick * TICK)); }
      timers.push(setTimeout(show.bind(null, 0, true), 400 + TICKS * TICK));
      timers.push(setTimeout(play, 400 + TICKS * TICK + 2600));
    }
    code.innerHTML = PROMPT;
    if (!('IntersectionObserver' in window)) { play(); return; }
    new IntersectionObserver(function (entries) { if (entries[0].isIntersecting) { play(); } else { stop(); } }, { threshold: 0.3 }).observe(pre);
  }

  // ---------- a Copy button on every code block. For commands it copies only the lines after a "$" prompt, without the prompt or the
  // example output, so what you copy pastes straight into a terminal; for anything else (a script, YAML, JSON) it copies the whole block. ----------
  var COPY_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.6"/>' +
    '<path d="M10.5 5.5V4A1.5 1.5 0 0 0 9 2.5H4A1.5 1.5 0 0 0 2.5 4v5A1.5 1.5 0 0 0 4 10.5h1.5"/></svg>';
  function copyText(pre) {
    var whole = (pre.querySelector('code') || pre).textContent;
    var commands = whole.split('\n').filter(function (line) { return /^\s*\$ /.test(line); });
    var text = commands.length ? commands.map(function (line) { return line.replace(/^\s*\$ /, ''); }).join('\n') : whole;
    return text.replace(/\s+$/, '');
  }
  function selectIn(el) {
    var range = document.createRange(); range.selectNodeContents(el);
    var picked = window.getSelection(); picked.removeAllRanges(); picked.addRange(range);
  }
  function copyButton() {
    var button = document.createElement('button');
    button.type = 'button'; button.className = 'copy'; button.setAttribute('aria-label', 'Copy to clipboard');
    button.innerHTML = COPY_ICON + '<span>Copy</span>';
    return button;
  }
  // copy to the clipboard, or select the text and say how when the browser will not
  function copy(pre, text, say) {
    var unable = function () { selectIn(pre.querySelector('code') || pre); say('Press Ctrl+C'); };
    if (!navigator.clipboard || !navigator.clipboard.writeText) { unable(); return; }
    navigator.clipboard.writeText(text).then(function () { say('Copied'); }, unable);
  }
  function addCopy(pre) {
    if (pre.classList.contains('xtal') || pre.hasAttribute('data-splash')) { return; }
    var text = copyText(pre), button = copyButton(), label = button.querySelector('span'), reset = null;
    function say(word) { label.textContent = word; clearTimeout(reset); reset = setTimeout(function () { label.textContent = 'Copy'; }, 1600); }
    button.addEventListener('click', function () { copy(pre, text, say); });
    pre.appendChild(button);
  }

  // ---------- the page list marks the section being read ----------
  function markSections() {
    var links = Array.prototype.slice.call(document.querySelectorAll('.pages .sub a[href^="#"]'));
    if (!links.length) { return; }
    var sections = links.map(function (link) { return document.getElementById(link.getAttribute('href').slice(1)); });
    function mark() {
      var current = 0;
      sections.forEach(function (section, index) { if (section && section.getBoundingClientRect().top <= 120) { current = index; } });
      links.forEach(function (link, index) { link.classList.toggle('on', index === current); });
    }
    window.addEventListener('scroll', mark, { passive: true });
    window.addEventListener('resize', mark);
    mark();
  }

  startHeader(); foldPageList(); wireMenu();
  Array.prototype.forEach.call(document.querySelectorAll('pre[data-splash]'), startSplash);
  Array.prototype.forEach.call(document.querySelectorAll('pre'), addCopy);
  markSections();
})();
