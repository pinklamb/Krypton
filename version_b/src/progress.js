Racer.progress = {
  create() {
    return { speed: Racer.config.startSpeed, score: 0, gridOffset: 0, best: 0 };
  },

  advance(progress, dt, scoreMultiplier) {
    const c = Racer.config;
    progress.speed += c.acceleration * dt;
    progress.score += progress.speed * dt * c.scoreRate * scoreMultiplier;
    progress.gridOffset = (progress.gridOffset + progress.speed * dt) % c.gridStep;
  },

  recordBest(progress) {
    progress.best = Math.max(progress.best, Math.floor(progress.score));
  },
};
