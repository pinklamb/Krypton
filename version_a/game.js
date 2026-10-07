const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');

const W = canvas.width;
const H = canvas.height;
const LANES = 3;
const ROAD_X = 60;
const LANE_W = 120;
const CAR_W = 56;
const CAR_H = 96;
const PLAYER_Y = H - CAR_H - 40;

const MAX_LIVES = 3;
const HIT_PADDING = 8;
const SHAKE_TIME = 2;
const SHAKE_MAGNITUDE = 12;
const SHARD_COUNT = 5;
const NITRO_TIME_SCALE = 2;
const NITRO_SCORE_MULT = 2;
const keys = {};

const WEATHER_PERIOD = 30;
const WEATHER_ORDER = ['clear', 'rain', 'fog'];
const RAIN_TRACTION = 0.8;
const RAIN_LANE_DELAY = 0.12;
const FOG_SPAWN_FACTOR = 0.7;
const MIST_RATE = 40;
const SPEED_DEMON_TIME = 5;
const NEAR_MISS_GAP = 5;
const SURVIVOR_SCORE = 5000;
const TOAST_TIME = 3.2;
const STORAGE_KEY = 'retro-racer-a-achievements';
const WEATHER_NAMES = { clear: 'CLEAR', rain: 'RAIN', fog: 'NEON FOG' };
const ACHIEVEMENTS = {
  speedDemon: 'SPEED DEMON',
  nearMiss: 'NEAR MISS',
  survivor: 'SURVIVOR',
};

const state = {
  lane: 1,
  playerX: laneCenter(1),
  obstacles: [],
  speed: 240,
  score: 0,
  spawnTimer: 0,
  gridOffset: 0,
  over: false,
  best: 0,
  nitro: false,
  particles: [],
  lives: MAX_LIVES,
  shakeTimer: 0,
  invulnTimer: 0,
  shards: [],
  weather: 'clear',
  weatherIndex: 0,
  weatherTime: 0,
  clock: 0,
  laneQueue: [],
  mist: [],
  mistCarry: 0,
  nitroTime: 0,
  unlocked: loadUnlocked(),
  toasts: [],
};

function laneCenter(lane) {
  return ROAD_X + LANE_W * lane + LANE_W / 2;
}

function resetGame() {
  state.lane = 1;
  state.playerX = laneCenter(1);
  state.obstacles = [];
  state.speed = 240;
  state.score = 0;
  state.spawnTimer = 0;
  state.over = false;
  state.particles = [];
  state.lives = MAX_LIVES;
  state.shakeTimer = 0;
  state.invulnTimer = 0;
  state.shards = [];
  state.weather = 'clear';
  state.weatherIndex = 0;
  state.weatherTime = 0;
  state.laneQueue = [];
  state.mist = [];
  state.nitroTime = 0;
}

function loadUnlocked() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return Array.isArray(saved) ? saved : [];
  } catch (err) {
    return [];
  }
}

function saveUnlocked() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.unlocked));
  } catch (err) {
    // storage unavailable: achievements still live for this session
  }
}

let audioCtx = null;

function ensureAudio() {
  if (audioCtx) return;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  try {
    audioCtx = new AC();
  } catch (err) {
    audioCtx = null;
  }
}

function playAchievementSound() {
  if (!audioCtx) return;
  [660, 880, 1320].forEach((freq, i) => {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    const t0 = audioCtx.currentTime + i * 0.09;
    osc.type = 'square';
    osc.frequency.setValueAtTime(freq, t0);
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(0.15, t0 + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.14);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(t0);
    osc.stop(t0 + 0.16);
  });
}

function unlock(id) {
  if (state.unlocked.includes(id)) return;
  state.unlocked.push(id);
  saveUnlocked();
  state.toasts.push({ title: 'ACHIEVEMENT UNLOCKED', text: ACHIEVEMENTS[id], age: 0 });
  playAchievementSound();
}

function shiftLane(delta) {
  state.lane = Math.max(0, Math.min(LANES - 1, state.lane + delta));
}

function requestLane(delta) {
  if (state.weather === 'rain') state.laneQueue.push({ delta, wait: RAIN_LANE_DELAY });
  else shiftLane(delta);
}

window.addEventListener('keydown', (e) => {
  keys[e.code] = true;
  ensureAudio();
  if (state.over) {
    if (e.code === 'Space' || e.code === 'Enter') resetGame();
    return;
  }
  if (e.code === 'ArrowLeft' || e.code === 'KeyA') requestLane(-1);
  if (e.code === 'ArrowRight' || e.code === 'KeyD') requestLane(1);
});

window.addEventListener('keyup', (e) => {
  keys[e.code] = false;
});
window.addEventListener('blur', () => {
  for (const k in keys) keys[k] = false;
});

function spawnObstacle() {
  const lane = Math.floor(Math.random() * LANES);
  const hue = Math.random() < 0.5 ? '#7df9ff' : '#ffe14d';
  state.obstacles.push({ x: laneCenter(lane), y: -CAR_H, color: hue, minGap: Infinity, passed: false });
}

function emitExhaust(count) {
  for (let i = 0; i < count; i++) {
    state.particles.push({
      x: state.playerX + (Math.random() - 0.5) * (CAR_W - 24),
      y: PLAYER_Y + CAR_H - 4,
      vx: (Math.random() - 0.5) * 40,
      vy: 140 + Math.random() * 160,
      life: 0.35 + Math.random() * 0.25,
      maxLife: 0.6,
      size: 4 + Math.random() * 5,
    });
  }
}

function overlapPixels(ax, ay, bx, by) {
  const pad = HIT_PADDING;
  const w = CAR_W - pad * 2;
  const h = CAR_H - pad * 2;
  const ox = Math.min(ax + w / 2, bx + w / 2) - Math.max(ax - w / 2, bx - w / 2);
  const oy = Math.min(ay + pad + h, by + pad + h) - Math.max(ay + pad, by + pad);
  return { ox, oy };
}

function spawnShards(x, y) {
  for (let i = 0; i < SHARD_COUNT; i++) {
    const angle = (Math.PI * 2 * i) / SHARD_COUNT + Math.random() * 0.6;
    const speed = 220 + Math.random() * 160;
    state.shards.push({
      x, y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      life: 0.9,
      maxLife: 0.9,
      rot: Math.random() * Math.PI,
      spin: (Math.random() - 0.5) * 12,
      size: 8 + Math.random() * 8,
    });
  }
}

function handleHit(obstacle) {
  state.lives -= 1;
  state.shakeTimer = SHAKE_TIME;
  state.invulnTimer = SHAKE_TIME;
  state.obstacles = state.obstacles.filter((o) => o !== obstacle);
  spawnShards(state.playerX, PLAYER_Y + CAR_H / 2);
  if (state.lives <= 0) {
    state.over = true;
    state.best = Math.max(state.best, Math.floor(state.score));
  }
}

function updateEffects(realDt) {
  state.shakeTimer = Math.max(0, state.shakeTimer - realDt);
  state.invulnTimer = Math.max(0, state.invulnTimer - realDt);
  for (const s of state.shards) {
    s.x += s.vx * realDt;
    s.y += s.vy * realDt;
    s.rot += s.spin * realDt;
    s.life -= realDt;
  }
  state.shards = state.shards.filter((s) => s.life > 0);
  if (state.toasts.length) {
    state.toasts[0].age += realDt;
    if (state.toasts[0].age >= TOAST_TIME) state.toasts.shift();
  }
}

function updateParticles(realDt) {
  for (const p of state.particles) {
    p.x += p.vx * realDt;
    p.y += p.vy * realDt;
    p.life -= realDt;
  }
  state.particles = state.particles.filter((p) => p.life > 0);
}

function updateWeather(realDt) {
  state.clock += realDt;
  state.weatherTime += realDt;
  if (state.weatherTime >= WEATHER_PERIOD) {
    state.weatherTime -= WEATHER_PERIOD;
    state.weatherIndex = (state.weatherIndex + 1) % WEATHER_ORDER.length;
    state.weather = WEATHER_ORDER[state.weatherIndex];
    state.toasts.push({ title: 'WEATHER', text: WEATHER_NAMES[state.weather], age: 0 });
  }
  for (const q of state.laneQueue) q.wait -= realDt;
  while (state.laneQueue.length && state.laneQueue[0].wait <= 0) {
    shiftLane(state.laneQueue.shift().delta);
  }
  if (state.weather === 'fog') {
    state.mistCarry += MIST_RATE * realDt;
    while (state.mistCarry >= 1) {
      state.mistCarry -= 1;
      state.mist.push({
        x: Math.random() * W,
        y: -40,
        vx: (Math.random() - 0.5) * 20,
        vy: 30 + Math.random() * 40,
        r: 30 + Math.random() * 40,
      });
    }
  }
  for (const m of state.mist) {
    m.x += m.vx * realDt;
    m.y += m.vy * realDt;
  }
  state.mist = state.mist.filter((m) => m.y < H + 80);
}

function trackNearMiss() {
  for (const o of state.obstacles) {
    if (o.passed) continue;
    const inBand = o.y < PLAYER_Y + CAR_H && o.y + CAR_H > PLAYER_Y;
    if (inBand) {
      o.minGap = Math.min(o.minGap, Math.abs(o.x - state.playerX) - CAR_W);
    } else if (o.y >= PLAYER_Y + CAR_H) {
      o.passed = true;
      if (o.minGap <= NEAR_MISS_GAP && state.invulnTimer <= 0) unlock('nearMiss');
    }
  }
}

function updateAchievements(realDt) {
  state.nitroTime = state.nitro ? state.nitroTime + realDt : 0;
  if (state.nitroTime >= SPEED_DEMON_TIME) unlock('speedDemon');
  if (state.score >= SURVIVOR_SCORE) unlock('survivor');
  trackNearMiss();
}

function update(realDt) {
  updateParticles(realDt);
  updateEffects(realDt);
  if (state.over) return;

  state.nitro = !!(keys.ArrowUp || keys.KeyW);
  const dt = realDt * (state.nitro ? NITRO_TIME_SCALE : 1);
  if (state.nitro) emitExhaust(3);
  updateWeather(realDt);

  state.speed += 6 * dt;
  const mult = state.nitro ? NITRO_SCORE_MULT : 1;
  state.score += state.speed * dt * 0.05 * mult;
  state.gridOffset = (state.gridOffset + state.speed * dt) % 60;

  const target = laneCenter(state.lane);
  const traction = state.weather === 'rain' ? RAIN_TRACTION : 1;
  state.playerX += (target - state.playerX) * Math.min(1, dt * 14 * traction);

  state.spawnTimer -= dt;
  if (state.spawnTimer <= 0) {
    spawnObstacle();
    const base = Math.max(0.35, 1.1 - state.speed / 1200) + Math.random() * 0.4;
    state.spawnTimer = base * (state.weather === 'fog' ? FOG_SPAWN_FACTOR : 1);
  }

  for (const o of state.obstacles) o.y += state.speed * 0.8 * dt;
  state.obstacles = state.obstacles.filter((o) => o.y < H + CAR_H);

  updateAchievements(realDt);

  if (state.invulnTimer > 0) return;
  for (const o of state.obstacles) {
    const { ox, oy } = overlapPixels(o.x, o.y, state.playerX, PLAYER_Y);
    if (ox > 0 && oy > 0) {
      handleHit(o);
      break;
    }
  }
}

function drawRoad() {
  ctx.fillStyle = '#12002a';
  ctx.fillRect(0, 0, W, H);

  ctx.fillStyle = '#1b0640';
  ctx.fillRect(ROAD_X, 0, LANE_W * LANES, H);

  ctx.strokeStyle = '#ff2bd6';
  ctx.lineWidth = 2;
  ctx.shadowColor = '#ff2bd6';
  ctx.shadowBlur = 10;
  ctx.strokeRect(ROAD_X, -2, LANE_W * LANES, H + 4);
  ctx.shadowBlur = 0;

  ctx.strokeStyle = 'rgba(125, 249, 255, 0.35)';
  ctx.lineWidth = 1;
  for (let y = -60 + state.gridOffset; y < H; y += 60) {
    ctx.beginPath();
    ctx.moveTo(ROAD_X, y);
    ctx.lineTo(ROAD_X + LANE_W * LANES, y);
    ctx.stroke();
  }
  for (let i = 1; i < LANES; i++) {
    ctx.beginPath();
    ctx.setLineDash([24, 24]);
    ctx.lineDashOffset = -state.gridOffset;
    ctx.moveTo(ROAD_X + LANE_W * i, 0);
    ctx.lineTo(ROAD_X + LANE_W * i, H);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function drawCar(x, y, color) {
  ctx.shadowColor = color;
  ctx.shadowBlur = 14;
  ctx.fillStyle = color;
  ctx.fillRect(x - CAR_W / 2, y, CAR_W, CAR_H);
  ctx.shadowBlur = 0;
  ctx.fillStyle = '#0a0014';
  ctx.fillRect(x - CAR_W / 2 + 8, y + 16, CAR_W - 16, 22);
  ctx.fillRect(x - CAR_W / 2 + 8, y + 62, CAR_W - 16, 14);
}

function drawExhaust() {
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (const p of state.particles) {
    const t = p.life / p.maxLife;
    ctx.globalAlpha = Math.min(1, t);
    ctx.fillStyle = t > 0.5 ? '#ff7ae8' : '#ff1493';
    ctx.shadowColor = '#ff1493';
    ctx.shadowBlur = 12;
    ctx.fillRect(p.x - p.size / 2, p.y, p.size, p.size * 1.6);
  }
  ctx.restore();
}

function drawShards() {
  for (const s of state.shards) {
    ctx.save();
    ctx.translate(s.x, s.y);
    ctx.rotate(s.rot);
    ctx.globalAlpha = s.life / s.maxLife;
    ctx.fillStyle = '#ff2bd6';
    ctx.shadowColor = '#ff2bd6';
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.moveTo(0, -s.size / 2);
    ctx.lineTo(s.size / 2, s.size / 2);
    ctx.lineTo(-s.size / 2, s.size / 3);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
}

function drawLives() {
  ctx.textAlign = 'left';
  ctx.font = '20px "Courier New", monospace';
  ctx.fillStyle = '#ff2bd6';
  let hearts = '';
  for (let i = 0; i < MAX_LIVES; i++) hearts += i < state.lives ? '\u2665 ' : '\u2661 ';
  ctx.fillText(hearts, 12, 54);
  ctx.textAlign = 'right';
  ctx.fillStyle = '#7df9ff';
  ctx.fillText(state.weather.toUpperCase(), W - 12, 54);
}

function drawWeather() {
  if (state.weather === 'fog') {
    for (const m of state.mist) {
      ctx.fillStyle = 'rgba(125, 249, 255, 0.07)';
      ctx.beginPath();
      ctx.arc(m.x, m.y, m.r, 0, Math.PI * 2);
      ctx.fill();
    }
    const alpha = 0.2 + 0.1 * Math.sin(state.clock * 1.5);
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, 'rgba(255, 43, 214, ' + alpha + ')');
    grad.addColorStop(1, 'rgba(125, 249, 255, ' + alpha * 0.6 + ')');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, H);
  }
  if (state.weather === 'rain') {
    ctx.fillStyle = 'rgba(30, 60, 140, 0.14)';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = 'rgba(160, 200, 255, 0.45)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < 45; i++) {
      const x = (i * 47) % W;
      const y = (i * 131 + state.clock * 700) % H;
      ctx.moveTo(x, y);
      ctx.lineTo(x - 4, y + 14);
    }
    ctx.stroke();
  }
}

function drawToast() {
  const toast = state.toasts[0];
  if (!toast) return;
  const slide = 0.4;
  const showFor = TOAST_TIME - slide;
  let t = 1;
  if (toast.age < slide) t = toast.age / slide;
  else if (toast.age > showFor) t = (TOAST_TIME - toast.age) / slide;
  const eased = 1 - Math.pow(1 - Math.max(0, Math.min(1, t)), 3);
  const y = -60 + 90 * eased;
  ctx.save();
  ctx.fillStyle = 'rgba(10, 0, 20, 0.92)';
  ctx.strokeStyle = '#ffe14d';
  ctx.lineWidth = 2;
  ctx.shadowColor = '#ffe14d';
  ctx.shadowBlur = 14;
  ctx.fillRect(W / 2 - 150, y, 300, 48);
  ctx.strokeRect(W / 2 - 150, y, 300, 48);
  ctx.shadowBlur = 0;
  ctx.textAlign = 'center';
  ctx.fillStyle = '#ffe14d';
  ctx.font = '12px "Courier New", monospace';
  ctx.fillText(toast.title, W / 2, y + 18);
  ctx.fillStyle = '#7df9ff';
  ctx.font = 'bold 20px "Courier New", monospace';
  ctx.fillText(toast.text, W / 2, y + 39);
  ctx.restore();
}

function drawHud() {
  ctx.fillStyle = '#7df9ff';
  ctx.font = '20px "Courier New", monospace';
  ctx.textAlign = 'left';
  ctx.fillText('SCORE ' + Math.floor(state.score), 12, 28);
  ctx.textAlign = 'right';
  ctx.fillText('SPEED ' + Math.floor(state.speed), W - 12, 28);
  if (state.nitro) {
    ctx.fillStyle = '#ff1493';
    ctx.textAlign = 'center';
    ctx.fillText('NITRO x2', W / 2, 28);
  }
}

function drawGameOver() {
  ctx.fillStyle = 'rgba(10, 0, 20, 0.8)';
  ctx.fillRect(0, 0, W, H);
  ctx.textAlign = 'center';
  ctx.fillStyle = '#ff2bd6';
  ctx.font = 'bold 56px "Courier New", monospace';
  ctx.fillText('GAME OVER', W / 2, H / 2 - 40);
  ctx.fillStyle = '#7df9ff';
  ctx.font = '24px "Courier New", monospace';
  ctx.fillText('Score ' + Math.floor(state.score), W / 2, H / 2 + 10);
  ctx.fillText('Best ' + state.best, W / 2, H / 2 + 44);
  ctx.fillStyle = '#ffe14d';
  ctx.font = '18px "Courier New", monospace';
  ctx.fillText('Press SPACE to race again', W / 2, H / 2 + 100);
}

function render() {
  ctx.save();
  if (state.shakeTimer > 0) {
    const mag = SHAKE_MAGNITUDE * (state.shakeTimer / SHAKE_TIME);
    ctx.translate((Math.random() - 0.5) * 2 * mag, (Math.random() - 0.5) * 2 * mag);
  }
  drawRoad();
  for (const o of state.obstacles) drawCar(o.x, o.y, o.color);
  drawExhaust();
  const blink = state.invulnTimer > 0 && Math.floor(state.invulnTimer * 10) % 2 === 0;
  if (!blink && !(state.over && state.lives <= 0)) drawCar(state.playerX, PLAYER_Y, '#ff2bd6');
  drawShards();
  drawWeather();
  drawHud();
  drawLives();
  drawToast();
  if (state.over) drawGameOver();
  ctx.restore();
}

let last = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  update(dt);
  render();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
