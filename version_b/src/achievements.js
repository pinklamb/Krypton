Racer.achievements = {
  create() {
    return { unlocked: Racer.storage.load(), nitroTime: 0 };
  },

  unlock(world, id) {
    const tracker = world.achievements;
    if (tracker.unlocked.includes(id)) return;
    tracker.unlocked.push(id);
    Racer.storage.save(tracker.unlocked);
    Racer.toast.push(world.toasts, Racer.config.achievementNames[id]);
    Racer.audio.playUnlock();
  },

  checkSpeedDemon(world, dt) {
    const tracker = world.achievements;
    tracker.nitroTime = world.nitro ? tracker.nitroTime + dt : 0;
    if (tracker.nitroTime >= Racer.config.speedDemonSeconds) this.unlock(world, 'speedDemon');
  },

  checkSurvivor(world) {
    if (world.progress.score >= Racer.config.survivorScore) this.unlock(world, 'survivor');
  },

  isInBand(obstacle) {
    const c = Racer.config;
    return obstacle.y < c.playerY + c.carHeight && obstacle.y + c.carHeight > c.playerY;
  },

  hasPassed(obstacle) {
    return obstacle.y >= Racer.config.playerY + Racer.config.carHeight;
  },

  gapTo(obstacle, player) {
    return Math.abs(obstacle.x - player.x) - Racer.config.carWidth;
  },

  settlePass(world, obstacle) {
    obstacle.passed = true;
    const close = obstacle.minGap <= Racer.config.nearMissGap;
    if (close && !Racer.health.isInvulnerable(world.health)) this.unlock(world, 'nearMiss');
  },

  trackObstacle(world, obstacle) {
    if (obstacle.passed) return;
    if (this.isInBand(obstacle)) obstacle.minGap = Math.min(obstacle.minGap, this.gapTo(obstacle, world.player));
    else if (this.hasPassed(obstacle)) this.settlePass(world, obstacle);
  },

  update(world, dt) {
    this.checkSpeedDemon(world, dt);
    this.checkSurvivor(world);
    world.obstacles.forEach((o) => this.trackObstacle(world, o));
  },
};
