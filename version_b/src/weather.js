Racer.weather = {
  create() {
    return { index: 0, kind: 'clear', elapsed: 0, clock: 0 };
  },

  profile(weather) {
    return Racer.config.weatherProfiles[weather.kind];
  },

  traction(weather) {
    return this.profile(weather).traction;
  },

  laneDelay(weather) {
    return this.profile(weather).laneDelay;
  },

  spawnFactor(weather) {
    return this.profile(weather).spawnFactor;
  },

  advanceKind(weather) {
    const order = Racer.config.weatherOrder;
    weather.index = (weather.index + 1) % order.length;
    weather.kind = order[weather.index];
  },

  step(weather, dt) {
    weather.clock += dt;
    weather.elapsed += dt;
    if (weather.elapsed < Racer.config.weatherPeriod) return false;
    weather.elapsed -= Racer.config.weatherPeriod;
    this.advanceKind(weather);
    return true;
  },
};
