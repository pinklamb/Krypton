const DC_BOUNDS = {
  north: 38.9955, south: 38.7916,
  east: -76.9094, west: -77.1198,
};

/** Google Places Autocomplete restricted to the DC bounding box. */
export function createAutocomplete(inputEl) {
  const bounds = new google.maps.LatLngBounds(
    { lat: DC_BOUNDS.south, lng: DC_BOUNDS.west },
    { lat: DC_BOUNDS.north, lng: DC_BOUNDS.east }
  );
  return new google.maps.places.Autocomplete(inputEl, { bounds, strictBounds: true });
}

function geocodeAddress(address) {
  const geocoder = new google.maps.Geocoder();
  return new Promise((resolve, reject) =>
    geocoder.geocode({ address }, (res, status) =>
      status === "OK" && res[0] ? resolve(res[0].geometry.location) : reject(new Error(status))
    )
  );
}

/** The formatted address for a coordinate, or null when Google has none. */
export function reverseGeocode(location) {
  const geocoder = new google.maps.Geocoder();
  return new Promise((resolve) =>
    geocoder.geocode({ location }, (results, status) =>
      resolve(status === "OK" && results[0] ? results[0].formatted_address : null)
    )
  );
}

/**
 * A picked suggestion wins, then typed text; with `fallbackToUser` an empty
 * field means "my location". Resolves to null when there is nothing to use.
 */
async function resolvePlace({ input, autocomplete, onLocationRequest, fallbackToUser = false }) {
  const place = autocomplete?.getPlace();
  if (place?.geometry) return place.geometry.location;

  const address = input.value.trim();
  if (address) return geocodeAddress(address);
  if (!fallbackToUser) return null;

  const loc = await onLocationRequest();
  return new google.maps.LatLng(loc.lat, loc.lng);
}

/** { origin, destination } for the form, or { error } with the message to show. */
export async function resolveTrip({ from, to, onLocationRequest }) {
  try {
    const destination = await resolvePlace({ ...to, onLocationRequest });
    if (!destination) return { error: "Please enter a destination." };
    const origin = await resolvePlace({ ...from, onLocationRequest, fallbackToUser: true });
    if (!origin) return { error: "Please enter a starting point." };
    return { origin, destination };
  } catch {
    return { error: "Could not resolve location. Please try again." };
  }
}

/** Hour of day from an <input type="time"> value like "17:30", or null when empty. */
export function hourOf(timeValue) {
  return timeValue ? parseInt(timeValue.split(":")[0], 10) : null;
}

/** The current time as an <input type="time"> value. */
export function currentTimeValue(now = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(now.getHours())}:${pad(now.getMinutes())}`;
}
