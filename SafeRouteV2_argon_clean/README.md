# SafeRoute DC

SafeRoute DC scores walking routes in Washington DC by safety (0–100), using historical crime data weighted by type, recency, and time of day.

## Tech Stack

| Layer | Tech |
|---|---|
| Frontend | React 18 + Vite |
| Backend | Node.js / Express (ES modules) |
| Database | MSSQL |
| Maps | Google Maps Platform (Directions, Places, Geocoding) |
| Crime data | DC Open Data (MPD FeatureServer) |

## Project Structure

```
SafeRoute-DC/
  package.json          ← workspace root (npm workspaces)
  server/
    scoring.js          ← pure safety scoring math (no Express dependency)
    app.js              ← Express routes
    db.js               ← DB pool + crime ingestion
    server.js           ← startup + sync loop
    tests/
      routing.test.js
  client/
    vite.config.js      ← proxies /api → Express in dev
    src/
      App.jsx
      components/
        SearchPanel.jsx
        MapView.jsx
        RouteCard.jsx
        DirectionsPanel.jsx
      hooks/
        useSafetyRoutes.js   ← directions + scoring logic
        useUserLocation.js
      utils/
        scoring.js           ← color/label helpers
```

## Getting Started

### Prerequisites
- Node.js 22+
- Google Maps API key (Directions API, Places API, Geocoding API enabled)
- MSSQL database

### Installation

```bash
git clone https://github.com/yourusername/saferoute-dc.git
cd saferoute-dc
npm install          # installs all workspaces at once
```

### Configuration

```bash
# Server
cp server/.env.example server/.env
# Fill in DB_USER, DB_PASSWORD, DB_SERVER, DB_NAME

# Client
cp client/.env.example client/.env.local
# Fill in VITE_GMAPS_KEY
```

### Development

```bash
npm run dev
# Starts both Express (port 4000) and Vite (port 5173) concurrently.
# Vite proxies /api/* to Express, so no CORS config needed.
# Open http://localhost:5173
```

### Tests

```bash
npm test
# Runs Jest against the server workspace
```

### Production build

```bash
npm run build
# Builds React → server/public/
# Express serves it statically — single process, no separate static host needed
```

## Safety Scoring Model

Each route is scored 0–100 based on crimes within **0.18 km** of the route (the standard pedestrian urban planning threshold).

Each crime is weighted by:

| Factor | Implementation |
|---|---|
| **Proximity** | Haversine distance ≤ 0.18 km of any route point |
| **Severity** | `HOMICIDE` = 5×, `ROBBERY` = 3×, `THEFT` = 2×, etc. |
| **Recency** | `0.8^age` — a 5-year-old crime contributes ~33% as much |
| **Time of day** | Late night 1.6×, evening 1.25×, early morning 0.85× |

The weighted total is normalized per km, then passed through exponential decay:

```
safetyScore = round(100 × e^(−0.0004 × weightedCrimesPerKm))
```

### Adding new scoring factors

All scoring logic lives in `server/scoring.js` as pure functions. To add a factor:

1. Write a pure function: `(crime, context) => multiplier`
2. Apply it in `scoreCrime()`
3. Add the new context field to the `/api/safetyScore` request body
4. Update the hook in `client/src/hooks/useSafetyRoutes.js` to pass the new field

The separation means you can swap in an ML-based weight function later without touching Express or React.
