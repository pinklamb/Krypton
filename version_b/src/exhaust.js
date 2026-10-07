Racer.exhaust = {
  create(player) {
    const c = Racer.config;
    return {
      x: player.x + (Math.random() - 0.5) * (c.carWidth - 24),
      y: c.playerY + c.carHeight - 4,
      vx: (Math.random() - 0.5) * 40,
      vy: 140 + Math.random() * 160,
      life: 0.35 + Math.random() * 0.25,
      maxLife: 0.6,
      size: 4 + Math.random() * 5,
    };
  },

  emitIfNitro(world) {
    if (!world.nitro) return;
    const count = Racer.config.exhaustPerFrame;
    for (let i = 0; i < count; i++) world.exhaust.push(this.create(world.player));
  },

  move(particle, dt) {
    particle.x += particle.vx * dt;
    particle.y += particle.vy * dt;
    particle.life -= dt;
  },

  step(list, dt) {
    list.forEach((p) => this.move(p, dt));
    return list.filter((p) => p.life > 0);
  },

  drawOne(ctx, p) {
    const t = p.life / p.maxLife;
    ctx.globalAlpha = Math.min(1, t);
    ctx.fillStyle = t > 0.5 ? '#ff7ae8' : '#ff1493';
    ctx.fillRect(p.x - p.size / 2, p.y, p.size, p.size * 1.6);
  },

  draw(ctx, list) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.shadowColor = '#ff1493';
    ctx.shadowBlur = 12;
    list.forEach((p) => this.drawOne(ctx, p));
    ctx.restore();
  },
};
