import styles from "./SearchPanel.module.css";

const PIN_PATH = "M14 2C8.477 2 4 6.477 4 12c0 7.5 10 16 10 16s10-8.5 10-16c0-5.523-4.477-10-10-10z";

const strokeProps = {
  fill: "none",
  stroke: "currentColor",
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": "true",
};

export function PinIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 28 28" fill="none" aria-hidden="true">
      <path d={PIN_PATH} fill="currentColor" opacity="0.15"/>
      <path d={PIN_PATH} stroke="currentColor" strokeWidth="1.5" fill="none"/>
      <circle cx="14" cy="12" r="3.5" fill="currentColor"/>
    </svg>
  );
}

export function LocIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" strokeWidth="2" {...strokeProps}>
      <circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M1 12h4M19 12h4"/>
    </svg>
  );
}

export function RouteIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" strokeWidth="2.5" {...strokeProps}>
      <circle cx="6" cy="19" r="3"/><path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15"/>
      <circle cx="18" cy="5" r="3"/>
    </svg>
  );
}

export function RouteConnector() {
  return (
    <div className={styles.connector} aria-hidden="true">
      <span className={styles.dot} />
      <span className={styles.line} />
      <span className={`${styles.dot} ${styles.dotDest}`} />
    </div>
  );
}

export function Spinner() {
  return <span className={styles.spinner} aria-hidden="true" />;
}
