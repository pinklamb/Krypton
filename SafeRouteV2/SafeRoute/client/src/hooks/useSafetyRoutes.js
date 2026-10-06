import { useState, useCallback, useRef } from "react";

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

      // Cancel any in-flight request
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setStatus("loading");
      setRoutes([]);
      setErrorMsg(null);

      // 1. Ask Google for walking routes
      let response;
      try {
        response = await new Promise((resolve, reject) => {
          directionsService.route(
            {
              origin,
              destination,
              travelMode: google.maps.TravelMode.WALKING,
              provideRouteAlternatives: true,
            },
            (res, status) =>
              status === "OK" ? resolve(res) : reject(new Error(`Directions: ${status}`))
          );
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        setStatus("error");
        setErrorMsg(err.message);
        return;
      }

      if (controller.signal.aborted) return;

      // 2. Score all routes in parallel
      const scoringPromises = response.routes.map(async (route, i) => {
        const path = google.maps.geometry.encoding.decodePath(route.overview_polyline);
        const routeCoords = path.map((p) => ({ lat: p.lat(), lng: p.lng() }));

        try {
          const res = await fetch("/api/safetyScore", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ route: routeCoords, timeOfDay }),
            signal: controller.signal,
          });
          const scoring = res.ok
            ? await res.json()
            : { safetyScore: 50, weightedPerKm: 0, routeDistanceKm: 0, crimeCount: 0 };

          return {
            index: i,
            route,
            leg: route.legs[0],
            scoring,
            directionsResponse: response,
          };
        } catch (err) {
          if (err.name === "AbortError") return null;
          return {
            index: i,
            route,
            leg: route.legs[0],
            scoring: { safetyScore: 50, weightedPerKm: 0, routeDistanceKm: 0, crimeCount: 0 },
            directionsResponse: response,
          };
        }
      });

      const results = (await Promise.all(scoringPromises)).filter(Boolean);

      if (controller.signal.aborted) return;

      setRoutes(results);
      setStatus("success");
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
