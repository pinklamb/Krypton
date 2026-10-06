import { useState, useEffect } from "react";

// Set VITE_GOOGLE_MAPS_API_KEY in client/.env (see .env.example); never commit the key.
const MAPS_URL = `https://maps.googleapis.com/maps/api/js?key=${import.meta.env.VITE_GOOGLE_MAPS_API_KEY}&libraries=places,geometry&v=weekly`;

/** True once the Google Maps script is loaded (injected on first use). */
export function useGoogleMaps() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (window.google) { setReady(true); return; }
    const script = document.createElement("script");
    script.src = MAPS_URL;
    script.async = true;
    script.onload = () => setReady(true);
    script.onerror = () => console.error("Google Maps failed to load");
    document.head.appendChild(script);
  }, []);

  return ready;
}
