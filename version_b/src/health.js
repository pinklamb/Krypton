Racer.health = {
  create() {
    return { lives: Racer.config.maxLives, invulnerable: 0 };
  },

  tick(health, dt) {
    health.invulnerable = Math.max(0, health.invulnerable - dt);
  },

  isInvulnerable(health) {
    return health.invulnerable > 0;
  },

  takeHit(health) {
    health.lives -= 1;
    health.invulnerable = Racer.config.invulnerableSeconds;
  },

  isDead(health) {
    return health.lives <= 0;
  },

  isBlinkOff(health) {
    return this.isInvulnerable(health) && Math.floor(health.invulnerable * 10) % 2 === 0;
  },
};
