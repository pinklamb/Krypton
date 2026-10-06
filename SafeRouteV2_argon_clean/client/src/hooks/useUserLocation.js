import { useState, useCallback } from "react";

/**
 * useUserLocation
 *
 * Wraps navigator.geolocation with React state.
 * Returns { getLocation, location, loading, error }
 */
export function useUserLocation() {
  const [location, setLocation] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const getLocation = useCallback(() => {
    if (!("geolocation" in navigator)) {
      setError("Geolocation is not supported by this browser.");
      return Promise.reject(new Error("Not supported"));
    }

    setLoading(true);
    setError(null);

    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const loc = { lat: pos.coords.latitude, lng: pos.coords.longitude };
          setLocation(loc);
          setLoading(false);
          resolve(loc);
        },
        (err) => {
          setError("Could not get your location. Please allow access and try again.");
          setLoading(false);
          reject(err);
        }
      );
    });
  }, []);

  return { getLocation, location, loading, error };
}
