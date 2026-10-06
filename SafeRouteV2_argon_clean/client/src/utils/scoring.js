/**
 * scoring.js (client)
 *
 * Pure helpers that map a safety score → display values.
 * No Google Maps dependency — safe to import anywhere including tests.
 */

export function getRouteColor(score) {
  if (score >= 85) return "#2ecc71";
  if (score >= 65) return "#f0b429";
  if (score >= 40) return "#e07e25";
  return "#e05252";
}

export function getSafetyClass(score) {
  if (score >= 85) return "safe";
  if (score >= 65) return "mod";
  if (score >= 40) return "caution";
  return "danger";
}

export function getSafetyLabel(score) {
  if (score >= 85) return "Low risk";
  if (score >= 65) return "Moderate";
  if (score >= 40) return "Use caution";
  return "High risk";
}

export function getContextMessage(crimeCount) {
  if (crimeCount >= 50) return "High concentration of reported incidents nearby.";
  if (crimeCount >= 15) return "Moderate number of reported incidents nearby.";
  if (crimeCount > 0)   return `${crimeCount} reported incident${crimeCount !== 1 ? "s" : ""} within route corridor.`;
  return "No significant incidents on this route.";
}
