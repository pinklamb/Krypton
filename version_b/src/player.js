Racer.player = {
  create() {
    return { lane: 1, x: Racer.util.laneCenter(1), pending: [] };
  },

  shiftLane(player, delta) {
    player.lane = Racer.util.clamp(player.lane + delta, 0, Racer.config.lanes - 1);
  },

  requestShift(player, delta, delay) {
    if (delay > 0) player.pending.push({ delta, wait: delay });
    else this.shiftLane(player, delta);
  },

  tickPending(player, dt) {
    player.pending.forEach((q) => { q.wait -= dt; });
    const due = player.pending.filter((q) => q.wait <= 0);
    player.pending = player.pending.filter((q) => q.wait > 0);
    due.forEach((q) => this.shiftLane(player, q.delta));
  },

  glide(player, dt, traction) {
    const target = Racer.util.laneCenter(player.lane);
    player.x += (target - player.x) * Math.min(1, dt * 14 * traction);
  },
};
