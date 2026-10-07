(function () {
  const canvas = document.getElementById('game');
  const ctx = canvas.getContext('2d');
  const world = Racer.world.create();
  let last = performance.now();

  Racer.input.onLaneChange = (delta) => Racer.world.requestLane(world, delta);
  Racer.input.onRestart = () => Racer.world.reset(world);
  Racer.input.bind(() => world.over);

  function frame(now) {
    const dt = Math.min(Racer.config.maxFrameTime, (now - last) / 1000);
    last = now;
    Racer.world.update(world, dt);
    Racer.world.render(ctx, world);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
