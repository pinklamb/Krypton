import { useEffect, useRef, useImperativeHandle, forwardRef } from "react";
import { getRouteColor } from "../utils/scoring.js";
import styles from "./MapView.module.css";

/**
 * MapView
 *
 * Owns the google.maps.Map instance. Exposes an imperative handle so the
 * parent (App) can call:
 *   - mapRef.current.renderRoutes(scoredRoutes, selectedIndex)
 *   - mapRef.current.highlightRoute(scoredRoute)
 *   - mapRef.current.openDirections(scoredRoute)
 *   - mapRef.current.clear()
 *
 * All map mutations go through this component — nothing else touches
 * the Maps SDK directly.
 */
export const MapView = forwardRef(function MapView({ onMapReady }, ref) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const renderersRef = useRef([]);
  const dirPanelRef = useRef(null);

  useEffect(() => {
    if (!window.google || mapRef.current) return;

    mapRef.current = new google.maps.Map(containerRef.current, {
      center: { lat: 38.9072, lng: -77.0369 },
      zoom: 13,
      mapTypeControl: false,
      fullscreenControl: false,
      streetViewControl: false,
      zoomControlOptions: { position: google.maps.ControlPosition.RIGHT_BOTTOM },
      styles: darkMapStyles(),
    });

    onMapReady?.(mapRef.current);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Expose imperative methods to parent via ref
  useImperativeHandle(ref, () => ({
    getDirectionsService() {
      if (!mapRef.current) return null;
      return new google.maps.DirectionsService();
    },

    /** Draw all scored routes; highlight the selected one */
    renderRoutes(scoredRoutes, selectedIndex = null) {
      clearRenderers();

      scoredRoutes.forEach(({ index, route, scoring, directionsResponse }) => {
        const isSelected = selectedIndex === index;
        const renderer = new google.maps.DirectionsRenderer({
          map: mapRef.current,
          directions: directionsResponse,
          routeIndex: index,
          polylineOptions: {
            strokeColor: getRouteColor(scoring.safetyScore),
            strokeWeight: isSelected ? 6 : 4,
            strokeOpacity: isSelected ? 1 : 0.5,
          },
          suppressMarkers: index > 0,
        });
        renderersRef.current.push(renderer);
      });
    },

    /** Zoom map to a single route */
    fitRoute(scoredRoute) {
      const path = google.maps.geometry.encoding.decodePath(
        scoredRoute.route.overview_polyline
      );
      const bounds = new google.maps.LatLngBounds();
      path.forEach((p) => bounds.extend(p));
      mapRef.current.fitBounds(bounds, { padding: 60 });
    },

    /** Open turn-by-turn directions panel */
    openDirections(scoredRoute, panelEl) {
      new google.maps.DirectionsRenderer({
        map: mapRef.current,
        directions: scoredRoute.directionsResponse,
        routeIndex: scoredRoute.index,
        panel: panelEl,
        suppressMarkers: false,
      });
    },

    clear() {
      clearRenderers();
    },
  }));

  function clearRenderers() {
    renderersRef.current.forEach((r) => r.setMap(null));
    renderersRef.current = [];
  }

  return <div ref={containerRef} className={styles.map} aria-label="Route map" />;
});

// ── Dark map style ────────────────────────────────────────────────────────────

function darkMapStyles() {
  return [
    { elementType: "geometry",            stylers: [{ color: "#1a1d2e" }] },
    { elementType: "labels.text.fill",    stylers: [{ color: "#6b7494" }] },
    { elementType: "labels.text.stroke",  stylers: [{ color: "#1a1d2e" }] },
    { featureType: "road",                elementType: "geometry",          stylers: [{ color: "#252a3d" }] },
    { featureType: "road.arterial",       elementType: "geometry",          stylers: [{ color: "#2d3249" }] },
    { featureType: "road.highway",        elementType: "geometry",          stylers: [{ color: "#333752" }] },
    { featureType: "road",                elementType: "labels.text.fill",  stylers: [{ color: "#5a6080" }] },
    { featureType: "water",               elementType: "geometry",          stylers: [{ color: "#0e1120" }] },
    { featureType: "poi",                 stylers: [{ visibility: "off" }] },
    { featureType: "transit",             stylers: [{ visibility: "off" }] },
    { featureType: "administrative",      elementType: "geometry.stroke",   stylers: [{ color: "#2a2f48" }] },
  ];
}
