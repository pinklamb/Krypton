Racer.world = {
  create() {
    return {
      player: Racer.player.create(),
      progress: Racer.progress.create(),
      obstacles: [],
      exhaust: [],
      shards: [],
      health: Racer.health.create(),
      shake: Racer.shake.create(),
      nitro: false,
      weather: Racer.weather.create(),
      mist: Racer.mist.create(),
      achievements: Racer.achievements.create(),
      toasts: Racer.toast.create(),
      spawnTimer: 0,
      over: false,
    };
  },

  reset(world) {
    const keep = { best: world.progress.best, unlocked: world.achievements.unlocked, toasts: world.toasts };
    Object.assign(world, this.create());
    world.progress.best = keep.best;
    world.achievements.unlocked = keep.unlocked;
    world.toasts = keep.toasts;
  },

  spawnIfDue(world, dt) {
    world.spawnTimer -= dt;
    if (world.spawnTimer > 0) return;
    world.obstacles.push(Racer.obstacles.create());
    const base = Racer.obstacles.nextSpawnDelay(world.progress.speed);
    world.spawnTimer = base * Racer.weather.spawnFactor(world.weather);
  },

  endRun(world) {
    world.over = true;
    Racer.progress.recordBest(world.progress);
  },

  findHit(world) {
    if (Racer.health.isInvulnerable(world.health)) return undefined;
    return world.obstacles.find((o) => Racer.collision.hitsPlayer(o, world.player));
  },

  applyHit(world, hit) {
    Racer.health.takeHit(world.health);
    Racer.shake.trigger(world.shake);
    world.obstacles = world.obstacles.filter((o) => o !== hit);
    world.shards.push(...Racer.shards.burst(world.player));
    if (Racer.health.isDead(world.health)) this.endRun(world);
  },

  checkCollisions(world) {
    const hit = this.findHit(world);
    if (hit) this.applyHit(world, hit);
  },

  stepRun(world, dt, realDt) {
    Racer.progress.advance(world.progress, dt, Racer.nitro.scoreMultiplier(world.nitro));
    Racer.player.glide(world.player, dt, Racer.weather.traction(world.weather));
    this.spawnIfDue(world, dt);
    world.obstacles = Racer.obstacles.advance(world.obstacles, world.progress.speed, dt);
    Racer.achievements.update(world, realDt);
    this.checkCollisions(world);
  },

  stepEffects(world, realDt) {
    world.exhaust = Racer.exhaust.step(world.exhaust, realDt);
    world.shards = Racer.shards.step(world.shards, realDt);
    Racer.shake.step(world.shake, realDt);
    Racer.health.tick(world.health, realDt);
    Racer.toast.step(world.toasts, realDt);
  },

  requestLane(world, delta) {
    Racer.player.requestShift(world.player, delta, Racer.weather.laneDelay(world.weather));
  },

  announceWeather(world) {
    Racer.toast.push(world.toasts, Racer.weather.profile(world.weather).toastName, 'WEATHER');
  },

  stepWeather(world, realDt) {
    if (Racer.weather.step(world.weather, realDt)) this.announceWeather(world);
    Racer.player.tickPending(world.player, realDt);
    Racer.mist.step(world.mist, world.weather, realDt);
  },

  update(world, realDt) {
    this.stepEffects(world, realDt);
    if (world.over) return;
    world.nitro = Racer.nitro.isActive();
    Racer.exhaust.emitIfNitro(world);
    this.stepWeather(world, realDt);
    this.stepRun(world, realDt * Racer.nitro.timeScale(world.nitro), realDt);
  },

  isPlayerVisible(world) {
    return !world.over && !Racer.health.isBlinkOff(world.health);
  },

  render(ctx, world) {
    ctx.save();
    Racer.shake.apply(ctx, world.shake);
    this.renderScene(ctx, world);
    ctx.restore();
  },

  renderScene(ctx, world) {
    Racer.road.draw(ctx, world.progress.gridOffset);
    Racer.cars.drawObstacles(ctx, world.obstacles);
    Racer.exhaust.draw(ctx, world.exhaust);
    if (this.isPlayerVisible(world)) Racer.cars.drawPlayer(ctx, world.player);
    Racer.shards.draw(ctx, world.shards);
    Racer.atmosphere.draw(ctx, world.weather, world.mist);
    Racer.hud.draw(ctx, world.progress);
    Racer.hud.drawLives(ctx, world.health);
    Racer.hud.drawWeather(ctx, world.weather);
    Racer.toast.draw(ctx, world.toasts);
    if (world.nitro) Racer.hud.drawNitroBadge(ctx);
    if (world.over) Racer.hud.drawGameOver(ctx, world.progress);
  },
};
