import { pool } from "./db.js";
import { crimesFromRequest, crimesNearRoute } from "./crimes.js";
import { calculateSafetyScore } from "./scoring.js";

const isRoute = (route) => Array.isArray(route) && route.length >= 2;

/** POST /api/safetyScore: score a route against inline crimes or the crimes table. */
export async function safetyScoreHandler(req, res) {
  try {
    const { route, timeOfDay, crimeData } = req.body;
    const inlineCrimes = Array.isArray(crimeData);

    if (!isRoute(route)) return res.status(400).json({ error: "No route provided" });
    if (!inlineCrimes && !pool?.connected) {
      return res.status(500).json({ error: "Database connection not ready" });
    }

    const crimes = inlineCrimes ? crimesFromRequest(crimeData) : await crimesNearRoute(route);
    res.json(
      calculateSafetyScore(route, crimes, {
        timeOfDay: timeOfDay ?? null,
        currentYear: new Date().getFullYear(),
      })
    );
  } catch (err) {
    console.error("Safety score error:", err);
    res.status(500).json({ error: "Failed to calculate safety score" });
  }
}
