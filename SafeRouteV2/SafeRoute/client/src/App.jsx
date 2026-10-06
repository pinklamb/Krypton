import { useState, useRef, useEffect, useCallback } from "react";
import { SearchPanel } from "./components/SearchPanel.jsx";
import { MapView } from "./components/MapView.jsx";
import { RouteCard } from "./components/RouteCard.jsx";
import { DirectionsPanel } from "./components/DirectionsPanel.jsx";
import { useSafetyRoutes } from "./hooks/useSafetyRoutes.js";
import { useUserLocation } from "./hooks/useUserLocation.js";
import styles from "./App.module.css";


export default function App() {
  const mapViewRef = useRef(null);
  const [mapsReady, setMapsReady] = useState(false);
  const [directionsService, setDirectionsService] = useState(null);
  const [selectedIndex, setSelectedIndex] = useState(null);
  const [activeDirections, setActiveDirections] = useState(null); 
  const [panelExpanded, setPanelExpanded] = useState(true);

  const { getLocation } = useUserLocation();
  const { routes, status, errorMsg, findRoutes, clear } = useSafetyRoutes({ directionsService });

  useEffect(() => {
    if (window.google) { setMapsReady(true); return; }
    const script = document.createElement("script");
    script.src = `https://maps.googleapis.com/maps/api/js?key=AIzaSyCkSFf-FJjXKwepF7cs7EdjDCGP0-Y-pKU&libraries=places,geometry&v=weekly`;
    script.async = true;
    script.onload = () => setMapsReady(true);
    script.onerror = () => console.error("Google Maps failed to load");
    document.head.appendChild(script);
  }, []);


  const handleMapReady = useCallback(() => {
    const svc = mapViewRef.current?.getDirectionsService();
    if (svc) setDirectionsService(svc);
  }, []);

 
  useEffect(() => {
    if (routes.length > 0) {
      mapViewRef.current?.renderRoutes(routes, selectedIndex);
    }
  }, [routes, selectedIndex]);

  const handleSubmit = useCallback(async ({ origin, destination, timeOfDay }) => {
    setSelectedIndex(null);
    setActiveDirections(null);
    await findRoutes(origin, destination, timeOfDay);
    setPanelExpanded(true);
  }, [findRoutes]);

  const handleSelect = useCallback((scoredRoute) => {
    setSelectedIndex(scoredRoute.index);
    setActiveDirections(null);
    mapViewRef.current?.fitRoute(scoredRoute);
  }, []);

  const handleStart = useCallback((scoredRoute) => {
    setActiveDirections(scoredRoute);
    // On mobile, collapse the search panel to show the map
    if (window.innerWidth <= 720) setPanelExpanded(false);
  }, []);

  const handleCloseDirections = useCallback(() => {
    setActiveDirections(null);
  }, []);

  return (
    <div className={styles.layout}>
      {/* Map — always rendered; MapView initialises once mapsReady */}
      {mapsReady && (
        <MapView ref={mapViewRef} onMapReady={handleMapReady} />
      )}

     
      <SearchPanel
        onSubmit={handleSubmit}
        onLocationRequest={getLocation}
        loading={status === "loading"}
        isExpanded={panelExpanded}
        onToggleExpand={() => setPanelExpanded((v) => !v)}
      >
        {status === "loading" && (
          <div className={styles.loadingState}>
            <span className={styles.spinner} />
            <p>Scoring routes…</p>
          </div>
        )}

        {status === "error" && (
          <p className={styles.error}>{errorMsg ?? "Could not find routes. Try again."}</p>
        )}

        {status === "success" && routes.length === 0 && (
          <p className={styles.empty}>No routes found for that trip.</p>
        )}

        {routes.map((sr, i) => (
          <RouteCard
            key={sr.index}
            scoredRoute={sr}
            isSelected={selectedIndex === sr.index}
            onSelect={() => handleSelect(sr)}
            onStart={() => handleStart(sr)}
            animationDelay={i * 80}
          />
        ))}
      </SearchPanel>

      {/* Turn-by-turn panel — mounts only when a route is started */}
      <DirectionsPanel
        scoredRoute={activeDirections}
        onClose={handleCloseDirections}
        mapRef={mapViewRef}
      />
    </div>
  );
}
