Racer.obstacles = {
  colors: ['#7df9ff', '#ffe14d'],

  create() {
    const c = Racer.config;
    const lane = Math.floor(Math.random() * c.lanes);
    const color = this.colors[Math.floor(Math.random() * this.colors.length)];
    return { x: Racer.util.laneCenter(lane), y: -c.carHeight, color, minGap: Infinity, passed: false };
  },

  nextSpawnDelay(speed) {
    return Math.max(0.35, 1.1 - speed / 1200) + Math.random() * 0.4;
  },

  move(obstacle, speed, dt) {
    obstacle.y += speed * Racer.config.obstacleSpeedRatio * dt;
  },

  isOnScreen(obstacle) {
    return obstacle.y < Racer.config.height + Racer.config.carHeight;
  },

  advance(list, speed, dt) {
    list.forEach((o) => this.move(o, speed, dt));
    return list.filter((o) => this.isOnScreen(o));
  },
};
