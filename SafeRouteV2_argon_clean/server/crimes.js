import sql from "mssql";
import { pool } from "./db.js";

// Slightly wider than PROXIMITY_THRESHOLD_KM, so crimes just outside the
// literal route envelope are still caught.
const BOX_BUFFER_DEGREES = 0.003;

/** Crimes the client supplied inline (used by tests and offline demos). */
export function crimesFromRequest(crimeData) {
  return crimeData.map((c) => ({
    latitude: c.lat,
    longitude: c.lng,
    crime_type: c.crime_type,
    year: c.year ?? new Date().getFullYear(),
  }));
}

/** Crimes inside the route's bounding box, read from the database. */
export async function crimesNearRoute(route) {
  const lats = route.map((p) => p.lat);
  const lngs = route.map((p) => p.lng);

  const result = await pool
    .request()
    .input("minLat", sql.Decimal(10, 7), Math.min(...lats) - BOX_BUFFER_DEGREES)
    .input("maxLat", sql.Decimal(10, 7), Math.max(...lats) + BOX_BUFFER_DEGREES)
    .input("minLon", sql.Decimal(10, 7), Math.min(...lngs) - BOX_BUFFER_DEGREES)
    .input("maxLon", sql.Decimal(10, 7), Math.max(...lngs) + BOX_BUFFER_DEGREES)
    .query(`
      SELECT crime_type, latitude, longitude, YEAR(date_occurred) AS year
      FROM   crimes
      WHERE  latitude  BETWEEN @minLat AND @maxLat
        AND  longitude BETWEEN @minLon AND @maxLon
    `);

  return result.recordset;
}
