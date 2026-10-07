Racer.shake = {
  create() {
    return { remaining: 0 };
  },

  trigger(shake) {
    shake.remaining = Racer.config.shakeSeconds;
  },

  step(shake, dt) {
    shake.remaining = Math.max(0, shake.remaining - dt);
  },

  jitter(strength) {
    return (Math.random() - 0.5) * 2 * strength;
  },

  apply(ctx, shake) {
    if (shake.remaining <= 0) return;
    const c = Racer.config;
    const strength = c.shakeMagnitude * (shake.remaining / c.shakeSeconds);
    ctx.translate(this.jitter(strength), this.jitter(strength));
  },
};
