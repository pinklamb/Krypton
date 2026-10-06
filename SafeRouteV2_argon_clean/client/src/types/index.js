/**
 * types/index.js
 *
 * JSDoc type definitions — shared shapes used across hooks and components.
 * No runtime cost. TypeScript-style safety via editor inference.
 *
 * When you're ready to add ML weights or new scoring factors, add the
 * shape here first, then the implementation in scoring.js on the server.
 */

/**
 * @typedef {{ lat: number, lng: number }} LatLng
 */

/**
 * @typedef {{
 *   safetyScore: number,
 *   weightedPerKm: number,
 *   routeDistanceKm: number,
 *   crimeCount: number
 * }} ScoringResult
 */

/**
 * @typedef {{
 *   route: import("@googlemaps/js-api-loader").google.maps.DirectionsRoute,
 *   leg: import("@googlemaps/js-api-loader").google.maps.DirectionsLeg,
 *   scoring: ScoringResult,
 *   index: number
 * }} ScoredRoute
 */

/**
 * @typedef {"idle" | "loading" | "success" | "error"} FetchStatus
 */

export {};
