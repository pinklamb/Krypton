
export const CRIME_TYPE_WEIGHTS = {
  HOMICIDE: 5,
  "SEX ABUSE": 4,
  "ASSAULT WITH DANGEROUS WEAPON": 4,
  ROBBERY: 3,
  "MOTOR VEHICLE THEFT": 3,
  "THEFT/AUTO": 2,
  "THEFT/OTHER": 2,
};

const DEFAULT_WEIGHT = 1;

export const PROXIMITY_THRESHOLD_KM = 0.18;

export const DECAY_FACTOR = 0.0004;

export const RECENCY_DECAY_BASE = 0.8;


export function getTimeOfDayMultiplier(hour) {
  if (hour === null || hour === undefined) return 1.0;
  const h = Number(hour);
  if (h >= 22 || h < 5) return 1.6;   // Late night — highest risk
  if (h >= 17 && h < 20) return 1.25; // Evening — elevated
  if (h >= 5 && h < 9) return 0.85;   // Early morning — lower risk
  return 1.0;
}


export function getDistanceKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function getRouteLengthKm(route) {
  return route.reduce((acc, point, i) => {
    if (i === 0) return 0;
    return acc + getDistanceKm(route[i - 1].lat, route[i - 1].lng, point.lat, point.lng);
  }, 0);
}


export function isCrimeNearRoute(crime, route) {
  return route.some(
    (point) =>
      getDistanceKm(crime.latitude, crime.longitude, point.lat, point.lng) <=
      PROXIMITY_THRESHOLD_KM
  );
}

/**
@param {object} crime   - { crime_type, year, latitude, longitude }
@param {object} context - { currentYear, timeOfDay }
@returns {number}
 */
export function scoreCrime(crime, context) {
  const { currentYear = new Date().getFullYear(), timeOfDay = null } = context;

  const typeWeight = CRIME_TYPE_WEIGHTS[crime.crime_type?.toUpperCase()] ?? DEFAULT_WEIGHT;
  const age = Math.max(0, currentYear - (crime.year ?? currentYear));
  const recencyWeight = Math.pow(RECENCY_DECAY_BASE, age);
  const timeMultiplier = getTimeOfDayMultiplier(timeOfDay);

  return typeWeight * recencyWeight * timeMultiplier;
}

/** 
 * @param {Array}  route    - [{ lat, lng }, ...]
 * @param {Array}  crimes   - [{ latitude, longitude, crime_type, year }, ...]
 * @param {object} options  - { timeOfDay?: number (0–23), currentYear?: number }
 * @returns {{ safetyScore, weightedPerKm, routeDistanceKm, crimeCount }}
 */
export function calculateSafetyScore(route, crimes, options = {}) {
  const { timeOfDay = null, currentYear = new Date().getFullYear() } = options;
  const context = { currentYear, timeOfDay };

  const routeDistanceKm = Math.max(getRouteLengthKm(route), 1.0);

  let weightedTotal = 0;
  const counted = new Set();

  for (const crime of crimes) {
    if (!isCrimeNearRoute(crime, route)) continue;

   
    const key = `${crime.latitude}|${crime.longitude}|${crime.crime_type}|${crime.year}`;
    if (counted.has(key)) continue;
    counted.add(key);

    weightedTotal += scoreCrime(crime, context);
  }

  const weightedPerKm = weightedTotal / routeDistanceKm;
  const safetyScore = Math.max(0, Math.round(100 * Math.exp(-DECAY_FACTOR * weightedPerKm)));

  return {
    safetyScore,
    weightedPerKm,
    routeDistanceKm,
    crimeCount: counted.size,
  };
}
