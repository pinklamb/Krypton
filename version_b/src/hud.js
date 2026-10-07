Racer.hud = {
  styles: {
    left: { align: 'left', color: '#7df9ff', font: '20px "Courier New", monospace' },
    right: { align: 'right', color: '#7df9ff', font: '20px "Courier New", monospace' },
    title: { align: 'center', color: '#ff2bd6', font: 'bold 56px "Courier New", monospace' },
    info: { align: 'center', color: '#7df9ff', font: '24px "Courier New", monospace' },
    nitro: { align: 'center', color: '#ff1493', font: '20px "Courier New", monospace' },
    lives: { align: 'left', color: '#ff2bd6', font: '20px "Courier New", monospace' },
    weather: { align: 'right', color: '#7df9ff', font: '20px "Courier New", monospace' },
    hint: { align: 'center', color: '#ffe14d', font: '18px "Courier New", monospace' },
  },

  text(ctx, text, x, y, styleName) {
    const style = this.styles[styleName];
    ctx.textAlign = style.align;
    ctx.fillStyle = style.color;
    ctx.font = style.font;
    ctx.fillText(text, x, y);
  },

  drawNitroBadge(ctx) {
    this.text(ctx, 'NITRO x' + Racer.config.nitroScoreMultiplier, Racer.config.width / 2, 28, 'nitro');
  },

  drawLives(ctx, health) {
    const max = Racer.config.maxLives;
    const hearts = Array.from({ length: max }, (_, i) => (i < health.lives ? '\u2665' : '\u2661'));
    this.text(ctx, hearts.join(' '), 12, 54, 'lives');
  },

  drawWeather(ctx, weather) {
    const label = Racer.weather.profile(weather).label;
    this.text(ctx, label, Racer.config.width - 12, 54, 'weather');
  },

  draw(ctx, progress) {
    this.text(ctx, 'SCORE ' + Math.floor(progress.score), 12, 28, 'left');
    this.text(ctx, 'SPEED ' + Math.floor(progress.speed), Racer.config.width - 12, 28, 'right');
  },

  drawGameOver(ctx, progress) {
    const c = Racer.config;
    const mid = c.width / 2;
    const cy = c.height / 2;
    ctx.fillStyle = 'rgba(10, 0, 20, 0.8)';
    ctx.fillRect(0, 0, c.width, c.height);
    this.text(ctx, 'GAME OVER', mid, cy - 40, 'title');
    this.text(ctx, 'Score ' + Math.floor(progress.score), mid, cy + 10, 'info');
    this.text(ctx, 'Best ' + progress.best, mid, cy + 44, 'info');
    this.text(ctx, 'Press SPACE to race again', mid, cy + 100, 'hint');
  },
};
