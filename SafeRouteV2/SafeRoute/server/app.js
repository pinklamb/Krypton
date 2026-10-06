import path from "path";
import { fileURLToPath } from "url";
import express from "express";
import sql from "mssql";
import { pool } from "./db.js";
import { calculateSafetyScore } from "./scoring.js";

const app = express();
app.use(express.json());

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

app.use(express.static(path.join(__dirname, "client")));

app.get("/", (_, res) => {
  res.sendFile(path.join(__dirname, "client", "index.html"));
});


app.post("/api/safetyScore", async (req, res) => {
  try {
    const { route, timeOfDay, crimeData } = req.body;

    if (!Array.isArray(route) || route.length < 2) {
      return res.status(400).json({ error: "No route provided" });
    }

    let crimes = [];

    if (Array.isArray(crimeData)) {
    
      crimes = crimeData.map((c) => ({
        latitude: c.lat,
        longitude: c.lng,
        crime_type: c.crime_type,
        year: c.year ?? new Date().getFullYear(),
      }));
    } else {
      if (!pool?.connected) {
        return res.status(500).json({ error: "Database connection not ready" });
      }

      // Build a bounding box with a small buffer so we catch crimes just
      // outside the literal route envelope.
      const lats = route.map((p) => p.lat);
      const lngs = route.map((p) => p.lng);
      const buffer = 0.003; // slightly wider than PROXIMITY_THRESHOLD_KM

      const result = await pool
        .request()
        .input("minLat", sql.Decimal(10, 7), Math.min(...lats) - buffer)
        .input("maxLat", sql.Decimal(10, 7), Math.max(...lats) + buffer)
        .input("minLon", sql.Decimal(10, 7), Math.min(...lngs) - buffer)
        .input("maxLon", sql.Decimal(10, 7), Math.max(...lngs) + buffer)
        .query(`
          SELECT crime_type, latitude, longitude, YEAR(date_occurred) AS year
          FROM   crimes
          WHERE  latitude  BETWEEN @minLat AND @maxLat
            AND  longitude BETWEEN @minLon AND @maxLon
        `);

      crimes = result.recordset;
    }

    const result = calculateSafetyScore(route, crimes, {
      timeOfDay: timeOfDay ?? null,
      currentYear: new Date().getFullYear(),
    });

    res.json(result);
  } catch (err) {
    console.error("Safety score error:", err);
    res.status(500).json({ error: "Failed to calculate safety score" });
  }
});

export default app;
