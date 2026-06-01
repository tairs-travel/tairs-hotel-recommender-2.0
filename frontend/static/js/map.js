(function () {
  "use strict";

  const ROUTE_COLORS = [
    "#e63946", // red
    "#2a9d8f", // teal
    "#f4a261", // orange
    "#6a4c93", // purple
    "#06d6a0", // mint
    "#118ab2", // blue
    "#ffd166", // yellow
    "#ef476f", // pink
    "#8ecae6", // sky blue
    "#a8dadc", // light teal
  ];

  const drawnLayers = [];
  const drawnMarkers = [];

  const map = new maplibregl.Map({
    container: "map",
    style: "https://tiles.openfreemap.org/styles/bright",
    center: [-69.0, 18.7],
    zoom: 8,
  });

  map.addControl(new maplibregl.NavigationControl(), "top-right");
  map.addControl(new maplibregl.FullscreenControl(), "top-right");

  window.hotelMap = map;

  // ── Helpers ──────────────────────────────────────────────────────────────

  function clearMap() {
    drawnLayers.forEach((id) => {
      if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(id)) map.removeSource(id);
    });
    drawnLayers.length = 0;
    drawnMarkers.forEach((m) => m.remove());
    drawnMarkers.length = 0;
  }

  function addRouteLayer(geojson, color, layerId) {
    map.addSource(layerId, { type: "geojson", data: geojson });
    map.addLayer({
      id: layerId,
      type: "line",
      source: layerId,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": color, "line-width": 4, "line-opacity": 0.85 },
    });
    drawnLayers.push(layerId);
  }

  function addHotelMarker(lng, lat, name, color) {
    const el = document.createElement("div");
    el.title = name;
    Object.assign(el.style, {
      width: "34px",
      height: "34px",
      background: color,
      borderRadius: "50%",
      border: "3px solid #fff",
      boxShadow: "0 2px 8px rgba(0,0,0,.4)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      cursor: "pointer",
    });
    el.innerHTML = `<img src="/static/img/residential.png" alt="Hotel" style="width:18px;height:18px;display:block;object-fit:contain;filter:brightness(0) invert(1);">`;

    const marker = new maplibregl.Marker({ element: el })
      .setLngLat([lng, lat])
      .setPopup(
        new maplibregl.Popup({ offset: 28, closeButton: false }).setHTML(
          `<strong style="font-size:13px">${name}</strong>`,
        ),
      )
      .addTo(map);

    drawnMarkers.push(marker);
  }

  function addAirportMarker(lng, lat, name) {
    const el = document.createElement("div");
    el.title = name;
    Object.assign(el.style, {
      width: "34px",
      height: "34px",
      background: "#005bbb",
      borderRadius: "50%",
      border: "3px solid #fff",
      boxShadow: "0 2px 8px rgba(0,0,0,.4)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      cursor: "pointer",
    });
    el.innerHTML = `<img src="/static/img/plane.png" alt="Airport" style="width:20px;height:20px;display:block;object-fit:contain;filter:brightness(0) invert(1);">`;

    const marker = new maplibregl.Marker({ element: el })
      .setLngLat([lng, lat])
      .setPopup(
        new maplibregl.Popup({ offset: 28, closeButton: false }).setHTML(
          `<strong style="font-size:13px">${name}</strong>`,
        ),
      )
      .addTo(map);

    drawnMarkers.push(marker);
  }

  async function fetchOsrmRoute(originLng, originLat, destLng, destLat) {
    const coords = `${originLng},${originLat};${destLng},${destLat}`;
    const res = await fetch(
      `/osrm/route/${coords}?geometries=geojson&overview=full`,
    );
    if (!res.ok) throw new Error(`OSRM ${res.status}`);
    return res.json();
  }

  // ── Main handler ─────────────────────────────────────────────────────────

  map.on("load", () => {
    window.addEventListener("recommendations", async ({ detail }) => {
      clearMap();

      const airport = detail.airport;
      if (!airport || !airport.lat || !airport.lng) return;

      const airportName =
        detail.airport_name || detail.airport_code || "Airport";
      addAirportMarker(airport.lng, airport.lat, airportName);

      const bounds = new maplibregl.LngLatBounds();
      bounds.extend([airport.lng, airport.lat]);

      const recs = Array.isArray(detail.recommendations)
        ? detail.recommendations
        : [];

      let colorIdx = 0;

      for (let i = 0; i < recs.length; i++) {
        const rec = recs[i];

        // Build the list of hotel endpoints for this recommendation
        const hotels =
          rec.hotels_coords && rec.hotels_coords.length
            ? rec.hotels_coords
            : [{ name: rec.hotel_name, lat: rec.lat, lng: rec.lng }];

        for (const hotel of hotels) {
          if (!hotel.lat || !hotel.lng) continue;

          const color = ROUTE_COLORS[colorIdx % ROUTE_COLORS.length];
          colorIdx++;

          const layerId = `route-${i}-${hotel.name.replace(/\W+/g, "-")}`;

          try {
            const osrmData = await fetchOsrmRoute(
              airport.lng,
              airport.lat,
              hotel.lng,
              hotel.lat,
            );
            const geometry = osrmData.routes?.[0]?.geometry;
            if (geometry) {
              addRouteLayer(
                { type: "Feature", geometry, properties: {} },
                color,
                layerId,
              );
            }
          } catch (err) {
            console.warn(`Route failed for "${hotel.name}":`, err);
          }

          addHotelMarker(hotel.lng, hotel.lat, hotel.name, color);
          bounds.extend([hotel.lng, hotel.lat]);
        }
      }

      if (!bounds.isEmpty()) {
        map.fitBounds(bounds, { padding: 60, maxZoom: 13, duration: 800 });
      }
    });
  });
})();
