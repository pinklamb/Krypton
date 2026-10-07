Racer.atmosphere = {
  painters: { fog: 'drawFog', rain: 'drawRain' },

  drawFog(ctx, weather, mist) {
    const c = Racer.config;
    Racer.mist.draw(ctx, mist);
    const alpha = 0.2 + 0.1 * Math.sin(weather.clock * 1.5);
    const grad = ctx.createLinearGradient(0, 0, 0, c.height);
    grad.addColorStop(0, 'rgba(255, 43, 214, ' + alpha + ')');
    grad.addColorStop(1, 'rgba(125, 249, 255, ' + alpha * 0.6 + ')');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, c.width, c.height);
  },

  drawStreak(ctx, index, clock) {
    const c = Racer.config;
    const x = (index * 47) % c.width;
    const y = (index * 131 + clock * 700) % c.height;
    ctx.moveTo(x, y);
    ctx.lineTo(x - 4, y + 14);
  },

  drawRain(ctx, weather) {
    const c = Racer.config;
    ctx.fillStyle = 'rgba(30, 60, 140, 0.14)';
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.strokeStyle = 'rgba(160, 200, 255, 0.45)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < 45; i++) this.drawStreak(ctx, i, weather.clock);
    ctx.stroke();
  },

  draw(ctx, weather, mist) {
    const painter = this.painters[weather.kind];
    if (painter) this[painter](ctx, weather, mist);
  },
};
