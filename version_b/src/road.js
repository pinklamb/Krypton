Racer.road = {
  drawBase(ctx) {
    const c = Racer.config;
    ctx.fillStyle = '#12002a';
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.fillStyle = '#1b0640';
    ctx.fillRect(c.roadX, 0, c.roadWidth, c.height);
  },

  drawEdge(ctx) {
    const c = Racer.config;
    ctx.strokeStyle = '#ff2bd6';
    ctx.lineWidth = 2;
    ctx.shadowColor = '#ff2bd6';
    ctx.shadowBlur = 10;
    ctx.strokeRect(c.roadX, -2, c.roadWidth, c.height + 4);
    ctx.shadowBlur = 0;
  },

  drawGridLine(ctx, y) {
    const c = Racer.config;
    ctx.beginPath();
    ctx.moveTo(c.roadX, y);
    ctx.lineTo(c.roadX + c.roadWidth, y);
    ctx.stroke();
  },

  drawGrid(ctx, offset) {
    const c = Racer.config;
    ctx.strokeStyle = 'rgba(125, 249, 255, 0.35)';
    ctx.lineWidth = 1;
    const count = Math.ceil(c.height / c.gridStep) + 1;
    Array.from({ length: count }, (_, i) => -c.gridStep + offset + i * c.gridStep)
      .forEach((y) => this.drawGridLine(ctx, y));
  },

  drawLaneDivider(ctx, lane, offset) {
    const c = Racer.config;
    const x = c.roadX + c.laneWidth * lane;
    ctx.beginPath();
    ctx.setLineDash([24, 24]);
    ctx.lineDashOffset = -offset;
    ctx.moveTo(x, 0);
    ctx.lineTo(x, c.height);
    ctx.stroke();
    ctx.setLineDash([]);
  },

  drawLaneDividers(ctx, offset) {
    const count = Racer.config.lanes - 1;
    Array.from({ length: count }, (_, i) => i + 1)
      .forEach((lane) => this.drawLaneDivider(ctx, lane, offset));
  },

  draw(ctx, offset) {
    this.drawBase(ctx);
    this.drawEdge(ctx);
    this.drawGrid(ctx, offset);
    this.drawLaneDividers(ctx, offset);
  },
};
