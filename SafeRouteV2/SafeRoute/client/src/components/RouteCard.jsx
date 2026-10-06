import { getRouteColor, getSafetyClass, getSafetyLabel, getContextMessage } from "../utils/scoring.js";
import styles from "./RouteCard.module.css";

/**
 * RouteCard
 *
 * Displays one scored route with safety badge, stats grid, score bar,
 * and context message. Calls onSelect when clicked, onStart when
 * "Start route" is pressed.
 *
 * @param {{
 *   scoredRoute: import("../types").ScoredRoute,
 *   isSelected: boolean,
 *   onSelect: () => void,
 *   onStart: () => void,
 *   animationDelay: number
 * }} props
 */
export function RouteCard({ scoredRoute, isSelected, onSelect, onStart, animationDelay = 0 }) {
  const { index, leg, scoring } = scoredRoute;
  const { safetyScore, crimeCount = 0, routeDistanceKm } = scoring;

  const color = getRouteColor(safetyScore);
  const cls = getSafetyClass(safetyScore);
  const label = getSafetyLabel(safetyScore);
  const context = getContextMessage(crimeCount);

  return (
    <article
      className={`${styles.card} ${isSelected ? styles.selected : ""}`}
      onClick={onSelect}
      style={{ animationDelay: `${animationDelay}ms` }}
      aria-selected={isSelected}
    >
      <header className={styles.header}>
        <span className={styles.routeName}>Route {index + 1}</span>
        <span className={`${styles.badge} ${styles[cls]}`}>{label}</span>
      </header>

      {/* Score bar */}
      <div className={styles.scoreBar} role="meter" aria-valuenow={safetyScore} aria-valuemin={0} aria-valuemax={100}>
        <div
          className={styles.scoreBarFill}
          style={{ width: `${safetyScore}%`, background: color }}
        />
      </div>

      {/* Stats grid */}
      <div className={styles.stats}>
        <Stat label="Safety" value={<><span style={{ color }}>{safetyScore}</span><small>/100</small></>} mono />
        <Stat label="Distance" value={leg.distance.text} mono />
        <Stat label="Walk time" value={leg.duration.text} mono />
        <Stat label="Incidents" value={crimeCount} mono />
      </div>

      <p className={styles.context}>{context}</p>

      {isSelected && (
        <button
          className={styles.startBtn}
          onClick={(e) => { e.stopPropagation(); onStart(); }}
        >
          Start route
        </button>
      )}
    </article>
  );
}

function Stat({ label, value, mono }) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span className={`${styles.statValue} ${mono ? styles.mono : ""}`}>{value}</span>
    </div>
  );
}
