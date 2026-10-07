Racer.mist = {
  create() {
    return { list: [], carry: 0 };
  },

  make() {
    return {
      x: Math.random() * Racer.config.width,
      y: -40,
      vx: (Math.random() - 0.5) * 20,
      vy: 30 + Math.random() * 40,
      r: 30 + Math.random() * 40,
    };
  },

  emit(mist, dt) {
    mist.carry += Racer.config.mistRate * dt;
    const count = Math.floor(mist.carry);
    mist.carry -= count;
    for (let i = 0; i < count; i++) mist.list.push(this.make());
  },

  move(puff, dt) {
    puff.x += puff.vx * dt;
    puff.y += puff.vy * dt;
  },

  step(mist, weather, dt) {
    if (weather.kind === 'fog') this.emit(mist, dt);
    mist.list.forEach((p) => this.move(p, dt));
    mist.list = mist.list.filter((p) => p.y < Racer.config.height + 80);
  },

  drawOne(ctx, puff) {
    ctx.beginPath();
    ctx.arc(puff.x, puff.y, puff.r, 0, Math.PI * 2);
    ctx.fill();
  },

  draw(ctx, mist) {
    ctx.fillStyle = 'rgba(125, 249, 255, 0.07)';
    mist.list.forEach((p) => this.drawOne(ctx, p));
  },
};
