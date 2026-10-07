Racer.nitro = {
  isActive() {
    return Racer.input.isAnyHeld(Racer.config.nitroCodes);
  },

  timeScale(active) {
    return active ? Racer.config.nitroTimeScale : 1;
  },

  scoreMultiplier(active) {
    return active ? Racer.config.nitroScoreMultiplier : 1;
  },
};
