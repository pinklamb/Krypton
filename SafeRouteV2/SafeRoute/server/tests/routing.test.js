import app from "../app.js";
import request from "supertest";

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const testRoutes = {
  unsafe: [
    { lat: 38.923, lng: -77.018 },
    { lat: 38.9235, lng: -77.0175 },
  ],
  moderate: [
    { lat: 38.925, lng: -77.0165 },
    { lat: 38.9255, lng: -77.016 },
  ],
  safe: [
    { lat: 38.927, lng: -77.0145 },
    { lat: 38.9275, lng: -77.014 },
  ],
  remote: [
    // Middle of the ocean — no crimes anywhere near
    { lat: 0.0, lng: 0.0 },
    { lat: 0.001, lng: 0.001 },
  ],
};

function generateCrimes(route, count, type, year = 2025) {
  return Array.from({ length: count }, (_, i) => {
    const t = i / count;
    const idx = Math.floor(t * (route.length - 1));
    const p = route[idx];
    return {
      lat: p.lat + (Math.random() - 0.5) * 0.0002,
      lng: p.lng + (Math.random() - 0.5) * 0.0002,
      crime_type: type,
      year,
    };
  });
}

const allCrimes = [
  ...generateCrimes(testRoutes.unsafe, 300, "HOMICIDE"),
  ...generateCrimes(testRoutes.moderate, 100, "ROBBERY"),
  ...generateCrimes(testRoutes.safe, 50, "THEFT/OTHER"),
  // One crime per year at a fixed location to test recency decay
  ...[2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025].map((year) => ({
    lat: 38.923,
    lng: -77.018,
    crime_type: "THEFT/OTHER",
    year,
  })),
];

// ---------------------------------------------------------------------------
// Safety score ranking
// ---------------------------------------------------------------------------

describe("POST /api/safetyScore — score ranking", () => {
  test("unsafe < moderate < safe", async () => {
    const [unsafeRes, modRes, safeRes] = await Promise.all([
      request(app).post("/api/safetyScore").send({ route: testRoutes.unsafe, crimeData: allCrimes }),
      request(app).post("/api/safetyScore").send({ route: testRoutes.moderate, crimeData: allCrimes }),
      request(app).post("/api/safetyScore").send({ route: testRoutes.safe, crimeData: allCrimes }),
    ]);

    expect(unsafeRes.status).toBe(200);
    expect(unsafeRes.body.safetyScore).toBeLessThan(modRes.body.safetyScore);
    expect(modRes.body.safetyScore).toBeLessThan(safeRes.body.safetyScore);
  });
});

// ---------------------------------------------------------------------------
// Time-of-day weighting
// ---------------------------------------------------------------------------

describe("POST /api/safetyScore — time-of-day weighting", () => {
  test("night score < morning score on a route with crimes nearby", async () => {
    const morningRes = await request(app)
      .post("/api/safetyScore")
      .send({ route: testRoutes.unsafe, timeOfDay: 7, crimeData: allCrimes });

    const nightRes = await request(app)
      .post("/api/safetyScore")
      .send({ route: testRoutes.unsafe, timeOfDay: 23, crimeData: allCrimes });

    expect(morningRes.status).toBe(200);
    expect(nightRes.status).toBe(200);
    // Night multiplier is 1.6x vs morning 0.85x → night should score lower
    expect(nightRes.body.safetyScore).toBeLessThan(morningRes.body.safetyScore);
  });

  test("evening score is between morning and night", async () => {
    const [morningRes, eveningRes, nightRes] = await Promise.all([
      request(app).post("/api/safetyScore").send({ route: testRoutes.unsafe, timeOfDay: 7, crimeData: allCrimes }),
      request(app).post("/api/safetyScore").send({ route: testRoutes.unsafe, timeOfDay: 18, crimeData: allCrimes }),
      request(app).post("/api/safetyScore").send({ route: testRoutes.unsafe, timeOfDay: 23, crimeData: allCrimes }),
    ]);

    expect(eveningRes.body.safetyScore).toBeLessThanOrEqual(morningRes.body.safetyScore);
    expect(nightRes.body.safetyScore).toBeLessThanOrEqual(eveningRes.body.safetyScore);
  });

  test("omitting timeOfDay returns a valid score", async () => {
    const res = await request(app)
      .post("/api/safetyScore")
      .send({ route: testRoutes.safe, crimeData: allCrimes });

    expect(res.status).toBe(200);
    expect(res.body.safetyScore).toBeGreaterThanOrEqual(0);
    expect(res.body.safetyScore).toBeLessThanOrEqual(100);
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

describe("POST /api/safetyScore — edge cases", () => {
  test("empty route returns 400", async () => {
    const res = await request(app)
      .post("/api/safetyScore")
      .send({ route: [], crimeData: allCrimes });

    expect(res.status).toBe(400);
    expect(res.body.error).toBe("No route provided");
  });

  test("single-point route returns 400", async () => {
    const res = await request(app)
      .post("/api/safetyScore")
      .send({ route: [{ lat: 38.9, lng: -77.0 }], crimeData: allCrimes });

    expect(res.status).toBe(400);
  });

  test("no crimes nearby → score near 100", async () => {
    const res = await request(app)
      .post("/api/safetyScore")
      .send({ route: testRoutes.remote, crimeData: allCrimes });

    expect(res.status).toBe(200);
    expect(res.body.safetyScore).toBeGreaterThanOrEqual(90);
    expect(res.body.crimeCount).toBe(0);
  });

  test("response includes expected fields", async () => {
    const res = await request(app)
      .post("/api/safetyScore")
      .send({ route: testRoutes.safe, crimeData: allCrimes });

    expect(res.body).toHaveProperty("safetyScore");
    expect(res.body).toHaveProperty("weightedPerKm");
    expect(res.body).toHaveProperty("routeDistanceKm");
    expect(res.body).toHaveProperty("crimeCount");
  });
});

// ---------------------------------------------------------------------------
// Crime data fixture integrity
// ---------------------------------------------------------------------------

describe("Test fixture integrity", () => {
  const expectedYears = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025];

  test("all expected years are present in fixture", () => {
    const years = new Set(allCrimes.map((c) => c.year));
    expectedYears.forEach((y) => expect(years.has(y)).toBe(true));
  });

  test("most recent year has crimes", () => {
    const mostRecent = Math.max(...expectedYears);
    expect(allCrimes.filter((c) => c.year === mostRecent).length).toBeGreaterThan(0);
  });
});
