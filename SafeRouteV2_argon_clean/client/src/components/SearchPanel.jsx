import { useSearchForm } from "../hooks/useSearchForm.js";
import { PinIcon, LocIcon, RouteIcon, RouteConnector, Spinner } from "./icons.jsx";
import styles from "./SearchPanel.module.css";

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
  const form = useSearchForm({ onSubmit, onLocationRequest });

  return (
    <aside className={`${styles.panel} ${isExpanded ? "" : styles.collapsed}`}>
      <PanelHeader isExpanded={isExpanded} onToggleExpand={onToggleExpand} />

      <form className={styles.form} onSubmit={form.handleSubmit} noValidate>
        <div className={styles.inputGroup}>
          <label htmlFor="startInput" className={styles.label}>From</label>
          <div className={styles.inputRow}>
            <input
              id="startInput"
              ref={form.startRef}
              type="text"
              placeholder="Starting point"
              className={styles.input}
              autoComplete="off"
            />
            <button
              type="button"
              className={styles.locBtn}
              onClick={form.handleLocationBtn}
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
            ref={form.destRef}
            type="text"
            placeholder="Destination"
            className={styles.input}
            autoComplete="off"
          />
        </div>

        <div className={styles.timeRow}>
          <label htmlFor="timeInput" className={styles.label}>Departing</label>
          <input id="timeInput" ref={form.timeRef} type="time" className={`${styles.input} ${styles.timeInput}`} />
          <button type="button" className={styles.nowBtn} onClick={form.setNow}>Now</button>
        </div>

        <SubmitButton loading={loading} />
      </form>

      {/* Route cards injected here by parent */}
      <div className={styles.results}>
        {children}
      </div>
    </aside>
  );
}

function PanelHeader({ isExpanded, onToggleExpand }) {
  return (
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
  );
}

function SubmitButton({ loading }) {
  return (
    <button type="submit" className={styles.submitBtn} disabled={loading}>
      {loading ? <Spinner /> : <RouteIcon />}
      {loading ? "Scoring routes…" : "Find safe routes"}
    </button>
  );
}
