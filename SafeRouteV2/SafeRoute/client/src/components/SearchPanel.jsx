import { useRef, useEffect, useCallback } from "react";
import styles from "./SearchPanel.module.css";

const DC_BOUNDS = {
  north: 38.9955, south: 38.7916,
  east: -76.9094, west: -77.1198,
};

/**
 * SearchPanel
 *
 * Owns the search form UI. Calls onSubmit({ origin, destination, timeOfDay })
 * when the user submits. Does no routing or scoring itself.
 *
 * @param {{
 *   onSubmit: (params: { origin: google.maps.LatLng, destination: google.maps.LatLng, timeOfDay: number|null }) => void,
 *   onLocationRequest: () => Promise<{lat:number,lng:number}>,
 *   loading: boolean,
 *   isExpanded: boolean,
 *   onToggleExpand: () => void
 * }} props
 */
export function SearchPanel({ onSubmit, onLocationRequest, loading, isExpanded, onToggleExpand, children }) {
  const startRef = useRef(null);
  const destRef = useRef(null);
  const startACRef = useRef(null);
  const destACRef = useRef(null);
  const timeRef = useRef(null);

  // Wire Google Places Autocomplete once Maps is ready
  useEffect(() => {
    if (!window.google) return;
    const bounds = new google.maps.LatLngBounds(
      { lat: DC_BOUNDS.south, lng: DC_BOUNDS.west },
      { lat: DC_BOUNDS.north, lng: DC_BOUNDS.east }
    );
    const opts = { bounds, strictBounds: true };
    startACRef.current = new google.maps.places.Autocomplete(startRef.current, opts);
    destACRef.current  = new google.maps.places.Autocomplete(destRef.current, opts);
  }, []);

  const resolveInput = useCallback(async (inputEl, ac, fallbackToUser = false) => {
    const place = ac?.getPlace();
    if (place?.geometry) return place.geometry.location;

    const address = inputEl.value.trim();
    if (address) {
      const geocoder = new google.maps.Geocoder();
      const result = await new Promise((resolve, reject) =>
        geocoder.geocode({ address }, (res, status) =>
          status === "OK" && res[0]
            ? resolve(res[0].geometry.location)
            : reject(new Error(status))
        )
      );
      return result;
    }

    if (fallbackToUser) {
      const loc = await onLocationRequest();
      return new google.maps.LatLng(loc.lat, loc.lng);
    }

    return null;
  }, [onLocationRequest]);

  const handleSubmit = async (e) => {
    e.preventDefault();

    let origin, destination;
    try {
      destination = await resolveInput(destRef.current, destACRef.current);
      if (!destination) return alert("Please enter a destination.");
      origin = await resolveInput(startRef.current, startACRef.current, true);
      if (!origin) return alert("Please enter a starting point.");
    } catch {
      return alert("Could not resolve location. Please try again.");
    }

    const timeVal = timeRef.current?.value;
    const timeOfDay = timeVal ? parseInt(timeVal.split(":")[0], 10) : null;

    onSubmit({ origin, destination, timeOfDay });
  };

  const handleLocationBtn = async () => {
    try {
      const loc = await onLocationRequest();
      // Reverse geocode to fill the input
      const geocoder = new google.maps.Geocoder();
      geocoder.geocode({ location: loc }, (results, status) => {
        if (status === "OK" && results[0]) {
          startRef.current.value = results[0].formatted_address;
        }
      });
    } catch { /* user denied — silently ignore */ }
  };

  const setNow = () => {
    const now = new Date();
    timeRef.current.value = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  };

  return (
    <aside className={`${styles.panel} ${isExpanded ? "" : styles.collapsed}`}>
      <div className={styles.header}>
        <div className={styles.brand}>
          <PinIcon />
          <span>SafeRoute <em>DC</em></span>
        </div>
        {/* Mobile drag handle */}
        <button
          className={styles.handle}
          onClick={onToggleExpand}
          aria-label={isExpanded ? "Collapse panel" : "Expand panel"}
        />
      </div>

      <form className={styles.form} onSubmit={handleSubmit} noValidate>
        <div className={styles.inputGroup}>
          <label htmlFor="startInput" className={styles.label}>From</label>
          <div className={styles.inputRow}>
            <input
              id="startInput"
              ref={startRef}
              type="text"
              placeholder="Starting point"
              className={styles.input}
              autoComplete="off"
            />
            <button
              type="button"
              className={styles.locBtn}
              onClick={handleLocationBtn}
              aria-label="Use my location"
              title="Use my location"
            >
              <LocIcon />
            </button>
          </div>
        </div>

        <RouteConnector />

        <div className={styles.inputGroup}>
          <label htmlFor="destInput" className={styles.label}>To</label>
          <input
            id="destInput"
            ref={destRef}
            type="text"
            placeholder="Destination"
            className={styles.input}
            autoComplete="off"
          />
        </div>

        <div className={styles.timeRow}>
          <label htmlFor="timeInput" className={styles.label}>Departing</label>
          <input id="timeInput" ref={timeRef} type="time" className={`${styles.input} ${styles.timeInput}`} />
          <button type="button" className={styles.nowBtn} onClick={setNow}>Now</button>
        </div>

        <button type="submit" className={styles.submitBtn} disabled={loading}>
          {loading ? <Spinner /> : <RouteIcon />}
          {loading ? "Scoring routes…" : "Find safe routes"}
        </button>
      </form>

      {/* Route cards injected here by parent */}
      <div className={styles.results}>
        {children}
      </div>
    </aside>
  );
}

/* ---- small inline SVG icons ---- */
function PinIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 28 28" fill="none" aria-hidden="true">
      <path d="M14 2C8.477 2 4 6.477 4 12c0 7.5 10 16 10 16s10-8.5 10-16c0-5.523-4.477-10-10-10z"
        fill="currentColor" opacity="0.15"/>
      <path d="M14 2C8.477 2 4 6.477 4 12c0 7.5 10 16 10 16s10-8.5 10-16c0-5.523-4.477-10-10-10z"
        stroke="currentColor" strokeWidth="1.5" fill="none"/>
      <circle cx="14" cy="12" r="3.5" fill="currentColor"/>
    </svg>
  );
}
function LocIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M1 12h4M19 12h4"/>
    </svg>
  );
}
function RouteIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="6" cy="19" r="3"/><path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15"/>
      <circle cx="18" cy="5" r="3"/>
    </svg>
  );
}
function RouteConnector() {
  return (
    <div className={styles.connector} aria-hidden="true">
      <span className={styles.dot} />
      <span className={styles.line} />
      <span className={`${styles.dot} ${styles.dotDest}`} />
    </div>
  );
}
function Spinner() {
  return <span className={styles.spinner} aria-hidden="true" />;
}
