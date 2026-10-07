Racer.cars = {
  draw(ctx, x, y, color) {
    const c = Racer.config;
    const left = x - c.carWidth / 2;
    ctx.shadowColor = color;
    ctx.shadowBlur = 14;
    ctx.fillStyle = color;
    ctx.fillRect(left, y, c.carWidth, c.carHeight);
    ctx.shadowBlur = 0;
    ctx.fillStyle = '#0a0014';
    ctx.fillRect(left + 8, y + 16, c.carWidth - 16, 22);
    ctx.fillRect(left + 8, y + 62, c.carWidth - 16, 14);
  },

  drawObstacles(ctx, list) {
    list.forEach((o) => this.draw(ctx, o.x, o.y, o.color));
  },

  drawPlayer(ctx, player) {
    this.draw(ctx, player.x, Racer.config.playerY, '#ff2bd6');
  },
};
