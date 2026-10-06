import { useState, useCallback } from "react";

const MOBILE_MAX_WIDTH = 720;

/**
 * useTripFlow
 *
 * The UI state around a trip: which route card is selected, which route's
 * turn-by-turn panel is open, and whether the search panel is expanded.
 */
export function useTripFlow({ findRoutes, mapViewRef }) {
  const [selectedIndex, setSelectedIndex] = useState(null);
  const [activeDirections, setActiveDirections] = useState(null);
  const [panelExpanded, setPanelExpanded] = useState(true);

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
  }, [mapViewRef]);

  const handleStart = useCallback((scoredRoute) => {
    setActiveDirections(scoredRoute);
    // On mobile, collapse the search panel to show the map
    if (window.innerWidth <= MOBILE_MAX_WIDTH) setPanelExpanded(false);
  }, []);

  const closeDirections = useCallback(() => setActiveDirections(null), []);
  const togglePanel = useCallback(() => setPanelExpanded((v) => !v), []);

  return {
    selectedIndex, activeDirections, panelExpanded,
    handleSubmit, handleSelect, handleStart, closeDirections, togglePanel,
  };
}
