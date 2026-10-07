Racer.collision = {
  hitbox(centerX, top) {
    const c = Racer.config;
    const pad = c.hitPadding;
    return {
      left: centerX - c.carWidth / 2 + pad,
      right: centerX + c.carWidth / 2 - pad,
      top: top + pad,
      bottom: top + c.carHeight - pad,
    };
  },

  overlap(a, b) {
    return {
      x: Math.min(a.right, b.right) - Math.max(a.left, b.left),
      y: Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top),
    };
  },

  isHit(a, b) {
    const shared = this.overlap(a, b);
    return shared.x > 0 && shared.y > 0;
  },

  hitsPlayer(obstacle, player) {
    const other = this.hitbox(obstacle.x, obstacle.y);
    const mine = this.hitbox(player.x, Racer.config.playerY);
    return this.isHit(other, mine);
  },
};
