import { useRef, useEffect } from "react";
import { createAutocomplete, resolveTrip, reverseGeocode, hourOf, currentTimeValue } from "../utils/places.js";

/**
 * useSearchForm
 *
 * Owns the search form's input refs and handlers: Places autocomplete,
 * resolving the typed trip, "use my location" and "now". No markup.
 */
export function useSearchForm({ onSubmit, onLocationRequest }) {
  const startRef = useRef(null);
  const destRef = useRef(null);
  const startACRef = useRef(null);
  const destACRef = useRef(null);
  const timeRef = useRef(null);

  // Wire Google Places Autocomplete once Maps is ready
  useEffect(() => {
    if (!window.google) return;
    startACRef.current = createAutocomplete(startRef.current);
    destACRef.current = createAutocomplete(destRef.current);
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const trip = await resolveTrip({
      from: { input: startRef.current, autocomplete: startACRef.current },
      to: { input: destRef.current, autocomplete: destACRef.current },
      onLocationRequest,
    });
    if (trip.error) return alert(trip.error);
    onSubmit({ ...trip, timeOfDay: hourOf(timeRef.current?.value) });
  };

  const handleLocationBtn = async () => {
    try {
      const address = await reverseGeocode(await onLocationRequest());
      if (address) startRef.current.value = address;
    } catch { /* user denied — silently ignore */ }
  };

  const setNow = () => {
    timeRef.current.value = currentTimeValue();
  };

  return { startRef, destRef, timeRef, handleSubmit, handleLocationBtn, setNow };
}
