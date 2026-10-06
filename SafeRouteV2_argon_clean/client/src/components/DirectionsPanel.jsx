import { useEffect, useRef } from "react";
import styles from "./DirectionsPanel.module.css";

/**
 * DirectionsPanel
 *
 * Renders Google Maps turn-by-turn directions into a slide-in panel.
 * The `mapRef.openDirections(route, panelEl)` call populates the DOM
 * element that this component renders.
 *
 * @param {{
 *   scoredRoute: import("../types").ScoredRoute | null,
 *   onClose: () => void,
 *   mapRef: React.RefObject
 * }} props
 */
export function DirectionsPanel({ scoredRoute, onClose, mapRef }) {
  const panelRef = useRef(null);

  useEffect(() => {
    if (!scoredRoute || !panelRef.current) return;
    panelRef.current.innerHTML = "";
    mapRef.current?.openDirections(scoredRoute, panelRef.current);
  }, [scoredRoute, mapRef]);

  if (!scoredRoute) return null;

  return (
    <div className={styles.panel}>
      <header className={styles.header}>
        <span className={styles.title}>Turn-by-turn</span>
        <button className={styles.closeBtn} onClick={onClose} aria-label="Close directions">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <path d="M18 6 6 18M6 6l12 12"/>
          </svg>
        </button>
      </header>
      <div ref={panelRef} className={styles.content} />
    </div>
  );
}
