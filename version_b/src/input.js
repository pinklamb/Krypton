Racer.input = {
  onLaneChange: null,
  onRestart: null,
  held: {},
  deltas: { ArrowLeft: -1, KeyA: -1, ArrowRight: 1, KeyD: 1 },

  laneDelta(code) {
    return this.deltas[code] || 0;
  },

  isRestartKey(code) {
    return code === 'Space' || code === 'Enter';
  },

  handleKeyDown(event, isOver) {
    if (isOver) return this.handleRestartKey(event.code);
    const delta = this.laneDelta(event.code);
    if (delta !== 0) this.onLaneChange(delta);
  },

  handleRestartKey(code) {
    if (this.isRestartKey(code)) this.onRestart();
  },

  isAnyHeld(codes) {
    return codes.some((code) => this.held[code]);
  },

  releaseAll() {
    this.held = {};
  },

  bind(isOver) {
    window.addEventListener('keydown', (event) => {
      this.held[event.code] = true;
      Racer.audio.ensure();
      this.handleKeyDown(event, isOver());
    });
    window.addEventListener('keyup', (event) => {
      this.held[event.code] = false;
    });
    window.addEventListener('blur', () => this.releaseAll());
  },
};
