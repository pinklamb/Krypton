import { useState, useCallback, useRef } from "react";

const fallbackScoring = () => ({ safetyScore: 50, weightedPerKm: 0, routeDistanceKm: 0, crimeCount: 0 });

/** Google Directions walking alternatives, rejecting with `Directions: <status>`. */
function requestWalkingRoutes(directionsService, origin, destination) {
  return new Promise((resolve, reject) => {
    directionsService.route(
      {
        origin,
        destination,
        travelMode: google.maps.TravelMode.WALKING,
        provideRouteAlternatives: true,
      },
      (res, status) => (status === "OK" ? resolve(res) : reject(new Error(`Directions: ${status}`)))
    );
  });
}

/** The server's score for one route's coordinates; a neutral score if the server errors. */
async function fetchScoring(route, timeOfDay, signal) {
  const path = google.maps.geometry.encoding.decodePath(route.overview_polyline);
  const routeCoords = path.map((p) => ({ lat: p.lat(), lng: p.lng() }));
  const res = await fetch("/api/safetyScore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ route: routeCoords, timeOfDay }),
    signal,
  });
  return res.ok ? res.json() : fallbackScoring();
}

/** One scored route, or null if the request was aborted. */
async function scoreRoute({ route, index, response, timeOfDay, signal }) {
  const scored = { index, route, leg: route.legs[0], directionsResponse: response };
  try {
    return { ...scored, scoring: await fetchScoring(route, timeOfDay, signal) };
  } catch (err) {
    return err.name === "AbortError" ? null : { ...scored, scoring: fallbackScoring() };
  }
}

/** Scored routes for a trip; null if aborted; rejects only when Directions fails. */
async function loadScoredRoutes({ directionsService, origin, destination, timeOfDay, signal }) {
  const response = await requestWalkingRoutes(directionsService, origin, destination);
  if (signal.aborted) return null;

  const scoring = response.routes.map((route, index) =>
    scoreRoute({ route, index, response, timeOfDay, signal })
  );
  return (await Promise.all(scoring)).filter(Boolean);
}

/** Cancel any in-flight request and return the signal for the new one. */
function startRequest(abortRef) {
  abortRef.current?.abort();
  abortRef.current = new AbortController();
  return abortRef.current.signal;
}

/** The state to show once a search settles; null if a newer search made it stale. */
async function searchOutcome(args) {
  try {
    const routes = await loadScoredRoutes(args);
    return routes && !args.signal.aborted ? { status: "success", routes, errorMsg: null } : null;
  } catch (err) {
    return args.signal.aborted ? null : { status: "error", routes: [], errorMsg: err.message };
  }
}

/**
 * useSafetyRoutes
 *
 * Encapsulates the full route-request lifecycle:
 *   1. Calls Google Directions API for up to 3 alternatives
 *   2. POSTs each route's polyline to /api/safetyScore in parallel
 *   3. Returns scored + ranked routes to the UI
 *
 * The hook owns no DOM — it just manages async state.
 * Components call `findRoutes(origin, dest, timeOfDay)` and read `routes`.
 *
 * @param {{ directionsService: google.maps.DirectionsService | null }} deps
 */
export function useSafetyRoutes({ directionsService }) {
  /** @type {[import("../types").ScoredRoute[], Function]} */
  const [routes, setRoutes] = useState([]);
  const [status, setStatus] = useState("idle"); // "idle" | "loading" | "success" | "error"
  const [errorMsg, setErrorMsg] = useState(null);

  // Abort controller so stale requests don't clobber newer ones
  const abortRef = useRef(null);

  const findRoutes = useCallback(
    async (origin, destination, timeOfDay = null) => {
      if (!directionsService) return;

      const signal = startRequest(abortRef);

      setStatus("loading");
      setRoutes([]);
      setErrorMsg(null);

      const outcome = await searchOutcome({ directionsService, origin, destination, timeOfDay, signal });
      if (!outcome) return;

      setRoutes(outcome.routes);
      setStatus(outcome.status);
      setErrorMsg(outcome.errorMsg);
    },
    [directionsService]
  );

  const clear = useCallback(() => {
    abortRef.current?.abort();
    setRoutes([]);
    setStatus("idle");
    setErrorMsg(null);
  }, []);

  return { routes, status, errorMsg, findRoutes, clear };
}
