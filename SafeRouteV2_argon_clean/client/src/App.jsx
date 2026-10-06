import { useState, useRef, useEffect, useCallback } from "react";
import { SearchPanel } from "./components/SearchPanel.jsx";
import { MapView } from "./components/MapView.jsx";
import { RouteResults } from "./components/RouteResults.jsx";
import { DirectionsPanel } from "./components/DirectionsPanel.jsx";
import { useGoogleMaps } from "./hooks/useGoogleMaps.js";
import { useSafetyRoutes } from "./hooks/useSafetyRoutes.js";
import { useTripFlow } from "./hooks/useTripFlow.js";
import { useUserLocation } from "./hooks/useUserLocation.js";
import styles from "./App.module.css";

export default function App() {
  const mapViewRef = useRef(null);
  const mapsReady = useGoogleMaps();
  const [directionsService, setDirectionsService] = useState(null);

  const { getLocation } = useUserLocation();
  const { routes, status, errorMsg, findRoutes } = useSafetyRoutes({ directionsService });
  const trip = useTripFlow({ findRoutes, mapViewRef });

  const handleMapReady = useCallback(() => {
    const svc = mapViewRef.current?.getDirectionsService();
    if (svc) setDirectionsService(svc);
  }, []);

  useEffect(() => {
    if (routes.length > 0) {
      mapViewRef.current?.renderRoutes(routes, trip.selectedIndex);
    }
  }, [routes, trip.selectedIndex]);

  return (
    <div className={styles.layout}>
      {/* Map — always rendered; MapView initialises once mapsReady */}
      {mapsReady && <MapView ref={mapViewRef} onMapReady={handleMapReady} />}

      <SearchPanel
        onSubmit={trip.handleSubmit}
        onLocationRequest={getLocation}
        loading={status === "loading"}
        isExpanded={trip.panelExpanded}
        onToggleExpand={trip.togglePanel}
      >
        <RouteResults
          status={status}
          errorMsg={errorMsg}
          routes={routes}
          selectedIndex={trip.selectedIndex}
          onSelect={trip.handleSelect}
          onStart={trip.handleStart}
        />
      </SearchPanel>

      {/* Turn-by-turn panel — mounts only when a route is started */}
      <DirectionsPanel
        scoredRoute={trip.activeDirections}
        onClose={trip.closeDirections}
        mapRef={mapViewRef}
      />
    </div>
  );
}
