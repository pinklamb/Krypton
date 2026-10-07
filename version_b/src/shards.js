Racer.shards = {
  create(x, y, index) {
    const count = Racer.config.shardCount;
    const angle = (Math.PI * 2 * index) / count + Math.random() * 0.6;
    const speed = 220 + Math.random() * 160;
    return {
      x,
      y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      life: 0.9,
      maxLife: 0.9,
      rot: Math.random() * Math.PI,
      spin: (Math.random() - 0.5) * 12,
      size: 8 + Math.random() * 8,
    };
  },

  burst(player) {
    const c = Racer.config;
    const y = c.playerY + c.carHeight / 2;
    return Array.from({ length: c.shardCount }, (_, i) => this.create(player.x, y, i));
  },

  move(shard, dt) {
    shard.x += shard.vx * dt;
    shard.y += shard.vy * dt;
    shard.rot += shard.spin * dt;
    shard.life -= dt;
  },

  step(list, dt) {
    list.forEach((s) => this.move(s, dt));
    return list.filter((s) => s.life > 0);
  },

  drawOne(ctx, s) {
    ctx.save();
    ctx.translate(s.x, s.y);
    ctx.rotate(s.rot);
    ctx.globalAlpha = s.life / s.maxLife;
    ctx.fillStyle = '#ff2bd6';
    ctx.shadowColor = '#ff2bd6';
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.moveTo(0, -s.size / 2);
    ctx.lineTo(s.size / 2, s.size / 2);
    ctx.lineTo(-s.size / 2, s.size / 3);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  },

  draw(ctx, list) {
    list.forEach((s) => this.drawOne(ctx, s));
  },
};
