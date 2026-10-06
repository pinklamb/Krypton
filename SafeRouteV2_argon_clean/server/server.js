import app from "./app.js";
import { startDB, updateCrimesFromDC } from "./db.js";
import { config } from "./config.js";

const PORT = config.PORT || 4000;


async function start() {

  await startDB();

  
  app.listen(PORT, () => {
    console.log(`Server running at http://localhost:${PORT}`);
  });


  runSyncLoop();
}


async function runSyncLoop() {
  while (true) {
    try {
      console.log("Starting crime data sync...");
      await updateCrimesFromDC();
      console.log("Sync complete. Next run in 1 hour.");
    } catch (err) {
      console.error("Sync failed:", err);
    }
    await new Promise((resolve) => setTimeout(resolve, 60 * 60 * 1000));
  }
}

start().catch((err) => {
  console.error("Failed to start server:", err);
  process.exit(1);
});
