Racer.toast = {
  create() {
    return { queue: [] };
  },

  push(toasts, text, header = 'ACHIEVEMENT UNLOCKED') {
    toasts.queue.push({ text, header, age: 0 });
  },

  step(toasts, dt) {
    const head = toasts.queue[0];
    if (!head) return;
    head.age += dt;
    if (head.age >= Racer.config.toastSeconds) toasts.queue.shift();
  },

  slide(age) {
    const total = Racer.config.toastSeconds;
    const edge = 0.4;
    const t = age < edge ? age / edge : Math.min(1, (total - age) / edge);
    const clamped = Racer.util.clamp(t, 0, 1);
    return 1 - Math.pow(1 - clamped, 3);
  },

  drawBox(ctx, y) {
    const mid = Racer.config.width / 2;
    ctx.fillStyle = 'rgba(10, 0, 20, 0.92)';
    ctx.strokeStyle = '#ffe14d';
    ctx.lineWidth = 2;
    ctx.shadowColor = '#ffe14d';
    ctx.shadowBlur = 14;
    ctx.fillRect(mid - 150, y, 300, 48);
    ctx.strokeRect(mid - 150, y, 300, 48);
    ctx.shadowBlur = 0;
  },

  drawLabels(ctx, y, head) {
    const mid = Racer.config.width / 2;
    ctx.textAlign = 'center';
    ctx.fillStyle = '#ffe14d';
    ctx.font = '12px "Courier New", monospace';
    ctx.fillText(head.header, mid, y + 18);
    ctx.fillStyle = '#7df9ff';
    ctx.font = 'bold 20px "Courier New", monospace';
    ctx.fillText(head.text, mid, y + 39);
  },

  draw(ctx, toasts) {
    const head = toasts.queue[0];
    if (!head) return;
    const y = -60 + 90 * this.slide(head.age);
    ctx.save();
    this.drawBox(ctx, y);
    this.drawLabels(ctx, y, head);
    ctx.restore();
  },
};
