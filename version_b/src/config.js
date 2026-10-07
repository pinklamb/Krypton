window.Racer = window.Racer || {};

Racer.config = {
  width: 480,
  height: 720,
  lanes: 3,
  roadX: 60,
  laneWidth: 120,
  carWidth: 56,
  carHeight: 96,
  startSpeed: 240,
  acceleration: 6,
  scoreRate: 0.05,
  obstacleSpeedRatio: 0.8,
  gridStep: 60,
  maxFrameTime: 0.05,
  nitroCodes: ['ArrowUp', 'KeyW'],
  nitroTimeScale: 2,
  nitroScoreMultiplier: 2,
  exhaustPerFrame: 3,
  maxLives: 3,
  hitPadding: 8,
  invulnerableSeconds: 2,
  shakeSeconds: 2,
  shakeMagnitude: 12,
  shardCount: 5,
  weatherPeriod: 30,
  weatherOrder: ['clear', 'rain', 'fog'],
  weatherProfiles: {
    clear: { label: 'CLEAR', toastName: 'CLEAR', traction: 1, laneDelay: 0, spawnFactor: 1 },
    rain: { label: 'RAIN', toastName: 'RAIN', traction: 0.8, laneDelay: 0.12, spawnFactor: 1 },
    fog: { label: 'FOG', toastName: 'NEON FOG', traction: 1, laneDelay: 0, spawnFactor: 0.7 },
  },
  mistRate: 40,
  speedDemonSeconds: 5,
  nearMissGap: 5,
  survivorScore: 5000,
  toastSeconds: 3.2,
  storageKey: 'retro-racer-b-achievements',
  achievementNames: { speedDemon: 'SPEED DEMON', nearMiss: 'NEAR MISS', survivor: 'SURVIVOR' },
};

Racer.config.playerY = Racer.config.height - Racer.config.carHeight - 40;
Racer.config.roadWidth = Racer.config.laneWidth * Racer.config.lanes;

Racer.util = {
  laneCenter(lane) {
    const c = Racer.config;
    return c.roadX + c.laneWidth * lane + c.laneWidth / 2;
  },

  clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  },
};
