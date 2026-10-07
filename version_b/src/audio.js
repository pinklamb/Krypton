Racer.audio = {
  context: null,
  notes: [660, 880, 1320],

  ensure() {
    if (this.context) return;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return;
    try {
      this.context = new Ctor();
    } catch (err) {
      this.context = null;
    }
  },

  tone(freq, index) {
    const ac = this.context;
    const osc = ac.createOscillator();
    const gain = ac.createGain();
    const t0 = ac.currentTime + index * 0.09;
    osc.type = 'square';
    osc.frequency.setValueAtTime(freq, t0);
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(0.15, t0 + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.14);
    osc.connect(gain);
    gain.connect(ac.destination);
    osc.start(t0);
    osc.stop(t0 + 0.16);
  },

  playUnlock() {
    if (!this.context) return;
    this.notes.forEach((freq, i) => this.tone(freq, i));
  },
};
