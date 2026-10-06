import sql from "mssql";
import { config } from "./config.js";

const CRIME_BASE_YEAR = 2018;
const CRIME_END_YEAR = new Date().getFullYear();
const CRIME_BASE_LAYER_ID = 7;         // DC GIS layer ID for 2025
const REQUEST_BATCH_SIZE = 1000;
const API_DELAY_MS = 3000;             // Open Data DC recommended delay
const MIN_RECORDS_TO_SKIP_YEAR = 20000;

// ---------------------------------------------------------------------------
// Connection pool
// ---------------------------------------------------------------------------

export const pool = new sql.ConnectionPool({
  user: config.DB_USER,
  password: config.DB_PASSWORD,
  server: config.DB_SERVER,
  port: parseInt(config.DB_PORT, 10),
  options: { encrypt: false, trustServerCertificate: true },
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getLayerIdForYear(year) {
  // Each calendar year maps to a specific DC GIS FeatureServer layer.
  // The 2025 layer is ID 7; adjust this offset if DC changes their numbering.
  return CRIME_BASE_LAYER_ID + (year - 2025);
}

function buildCrimeApiUrl(layerId, offset, limit) {
  const params = new URLSearchParams({
    where: "1=1",
    outFields: "CCN,REPORT_DAT,OFFENSE,LATITUDE,LONGITUDE,XBLOCK,YBLOCK",
    outSR: "4326",
    returnGeometry: "false",
    resultOffset: offset,
    resultRecordCount: limit,
    f: "json",
  });
  return `https://maps2.dcgis.dc.gov/dcgis/rest/services/FEEDS/MPD/FeatureServer/${layerId}/query?${params}`;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---------------------------------------------------------------------------
// DB startup
// ---------------------------------------------------------------------------

export async function startDB() {
  await pool.connect();
  console.log("Database connected.");
}

// ---------------------------------------------------------------------------
// Crime ingestion
// ---------------------------------------------------------------------------

/**
 * Fetches a single page of crime records from the DC GIS API.
 * Returns [] if the layer doesn't exist or has no data.
 */
async function fetchCrimeBatch(layerId, offset) {
  const url = buildCrimeApiUrl(layerId, offset, REQUEST_BATCH_SIZE);
  const res = await fetch(url);
  const data = await res.json();
  return Array.isArray(data.features) ? data.features : [];
}

/**
 * Upserts a batch of crimes into the DB using a staging temp table +
 * MERGE statement. Uses a fresh request per batch (not per row) to avoid
 * parameter accumulation bugs.
 */
async function saveCrimesBatch(crimes) {
  // Each call to saveCrimesBatch gets its own connection from the pool
  // so the temp table lifecycle is scoped to this transaction only.
  const transaction = new sql.Transaction(pool);
  await transaction.begin();

  try {
    const req = new sql.Request(transaction);

    await req.query(`
      CREATE TABLE #CrimesStage (
        crime_id       NVARCHAR(50)  PRIMARY KEY,
        crime_type     NVARCHAR(255),
        date_occurred  DATETIME,
        latitude       DECIMAL(10,7),
        longitude      DECIMAL(10,7)
      );
    `);

    // Build a single multi-row INSERT instead of N round-trips
    const rows = crimes
      .map((c) => {
        const { CCN, OFFENSE, REPORT_DAT, LATITUDE, LONGITUDE } = c.attributes;
        const dateStr = new Date(REPORT_DAT).toISOString();
        // Escape single quotes in strings
        const safeOffense = OFFENSE?.replace(/'/g, "''") ?? "";
        const safeCCN = CCN?.replace(/'/g, "''") ?? "";
        return `('${safeCCN}', '${safeOffense}', '${dateStr}', ${LATITUDE}, ${LONGITUDE})`;
      })
      .join(",\n");

    if (rows) {
      await req.query(`
        INSERT INTO #CrimesStage (crime_id, crime_type, date_occurred, latitude, longitude)
        VALUES ${rows};
      `);
    }

    await req.query(`
      MERGE crimes AS target
      USING #CrimesStage AS source ON target.crime_id = source.crime_id
      WHEN MATCHED THEN
        UPDATE SET
          crime_type    = source.crime_type,
          date_occurred = source.date_occurred,
          latitude      = source.latitude,
          longitude     = source.longitude
      WHEN NOT MATCHED THEN
        INSERT (crime_id, crime_type, date_occurred, latitude, longitude)
        VALUES (source.crime_id, source.crime_type, source.date_occurred,
                source.latitude, source.longitude);

      DROP TABLE #CrimesStage;
    `);

    await transaction.commit();
  } catch (err) {
    await transaction.rollback();
    throw err;
  }
}

/**
 * Syncs all crime records for a given year from the DC GIS API.
 * Resumes from the current DB count so interrupted runs don't re-fetch.
 */
async function syncYear(year) {
  const layerId = getLayerIdForYear(year);
  console.log(`\nSyncing ${year} (layer ${layerId})...`);

  // Resume from however many records we already have for this year
  const countResult = await pool
    .request()
    .query(`SELECT COUNT(*) AS n FROM crimes WHERE YEAR(date_occurred) = ${year}`);
  let offset = countResult.recordset[0].n;
  console.log(`  Resuming from offset ${offset}`);

  let totalInserted = 0;

  while (true) {
    const batch = await fetchCrimeBatch(layerId, offset);
    if (batch.length === 0) break;

    await saveCrimesBatch(batch);
    totalInserted += batch.length;
    offset += batch.length;
    console.log(`  ${year}: +${batch.length} (total ${totalInserted})`);

    await delay(API_DELAY_MS);
  }

  console.log(`  Done with ${year}. Inserted ${totalInserted} records.`);
  return totalInserted;
}

// ---------------------------------------------------------------------------
// Public sync entry point
// ---------------------------------------------------------------------------

/**
 * Checks which years need syncing and fetches missing/incomplete records.
 * Years with ≥ MIN_RECORDS_TO_SKIP_YEAR records are skipped.
 */
export async function updateCrimesFromDC() {
  const yearCounts = await getYearCounts();
  let grand = 0;

  for (let year = CRIME_BASE_YEAR; year <= CRIME_END_YEAR; year++) {
    if ((yearCounts[year] ?? 0) >= MIN_RECORDS_TO_SKIP_YEAR) {
      console.log(`Skipping ${year}: already has ${yearCounts[year]} records`);
      continue;
    }

    // Quick existence check before pulling the full year
    const probe = await fetchCrimeBatch(getLayerIdForYear(year), 0);
    if (probe.length === 0) {
      console.log(`Skipping ${year}: no data from API`);
      continue;
    }

    const inserted = await syncYear(year);
    grand += inserted;
  }

  console.log(`\nSync complete. Total new records: ${grand}`);
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

/**
 * Returns { year: count } for all years with crime data in the DB.
 */
export async function getYearCounts() {
  const result = await pool.request().query(`
    SELECT YEAR(date_occurred) AS year, COUNT(*) AS count
    FROM   crimes
    WHERE  date_occurred IS NOT NULL
    GROUP  BY YEAR(date_occurred)
  `);
  return Object.fromEntries(result.recordset.map((r) => [r.year, r.count]));
}
