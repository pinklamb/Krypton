import { RouteCard } from "./RouteCard.jsx";
import styles from "../App.module.css";

/** The status message (loading / error / empty) and the scored route cards. */
export function RouteResults({ status, errorMsg, routes, selectedIndex, onSelect, onStart }) {
  return (
    <>
      <StatusMessage status={status} errorMsg={errorMsg} empty={routes.length === 0} />
      {routes.map((sr, i) => (
        <RouteCard
          key={sr.index}
          scoredRoute={sr}
          isSelected={selectedIndex === sr.index}
          onSelect={() => onSelect(sr)}
          onStart={() => onStart(sr)}
          animationDelay={i * 80}
        />
      ))}
    </>
  );
}

function StatusMessage({ status, errorMsg, empty }) {
  if (status === "loading") {
    return (
      <div className={styles.loadingState}>
        <span className={styles.spinner} />
        <p>Scoring routes…</p>
      </div>
    );
  }
  if (status === "error") {
    return <p className={styles.error}>{errorMsg ?? "Could not find routes. Try again."}</p>;
  }
  if (status === "success" && empty) {
    return <p className={styles.empty}>No routes found for that trip.</p>;
  }
  return null;
}
