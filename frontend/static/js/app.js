let allAirlines = [];
let airlinesEventSource = null;

const MOCK_AIRLINES = [
  {
    display_name: "Arajet (PUJ)",
    name: "Arajet",
    iata_code: "PUJ",
    country: "Dominican Republic",
  },
  {
    display_name: "Air Century (SDQ)",
    name: "Air Century",
    iata_code: "SDQ",
    country: "Dominican Republic",
  },
  {
    display_name: "JetBlue (PUJ)",
    name: "JetBlue",
    iata_code: "PUJ",
    country: "Dominican Republic",
  },
];

const MOCK_RESULTS = {
  recommendations: [
    {
      type: "single",
      hotel_name: "Grand Bavaro Suites",
      stars: 5,
      distance_km: 12.4,
      total_price: 4250,
      score_label: "Excellent",
      score_percentage: 93,
      score_breakdown: {
        price: 0.19,
        distance: 0.15,
        stars: 0.1,
        priority: 0.2,
        meals: 0.04,
      },
      room_combination: { single: 0, double: 8, triple: 2, quadruple: 0 },
      meals: { breakfast: true, lunch: true, dinner: false },
      all_inclusive: true,
      priority: "high",
    },
    {
      type: "multi",
      hotels_used: 2,
      hotels_coords: [
        { name: "Coral Reef", lat: 18.65, lng: -68.36 },
        { name: "Tropical Inn", lat: 18.66, lng: -68.37 },
      ],
      allocations: [
        {
          hotel_name: "Coral Reef",
          stars: 4,
          distance_km: 12.1,
          assigned_passengers: 10,
          priority: "1",
          is_estimated: true,
          groups: ["A"],
          rooms: { single: 20, double: 0, triple: 0, quadruple: 0 },
          meals: { breakfast: true, lunch: false, dinner: true },
          all_inclusive: true,
        },
        {
          hotel_name: "Tropical Inn",
          stars: 3,
          distance_km: 14.5,
          assigned_passengers: 8,
          priority: "2",
          is_estimated: false,
          groups: ["A", "B"],
          rooms: { single: 15, double: 0, triple: 0, quadruple: 0 },
          meals: { breakfast: true, lunch: true, dinner: false },
        },
      ],
      passengers_unassigned: 2,
    },
  ],
};

function byId(id) {
  return document.getElementById(id);
}

function setSseState(online) {
  const dot = byId("sseDot");
  const text = byId("sseText");
  if (dot) {
    dot.classList.toggle("online", online);
    dot.classList.toggle("offline", !online);
  }
  if (text) {
    text.textContent = online
      ? "Live updates connected"
      : "Live updates disconnected";
  }
}

function applyAirlinesData(airlines) {
  allAirlines = Array.isArray(airlines) ? airlines : [];
  const select = byId("airlineSelect");
  const previous = select.value;

  const options = ['<option value="">Select airline...</option>'];
  allAirlines.forEach((a) => {
    const label = a.group && a.group !== 'UNKNOWN'
      ? `${a.name} (${a.iata_code} - ${a.group})`
      : a.display_name;
    options.push(
      `<option value="${escapeHtml(a.display_name)}">${escapeHtml(label)}</option>`,
    );
  });
  select.innerHTML = options.join("");

  if (previous && allAirlines.some((a) => a.display_name === previous)) {
    select.value = previous;
  }
  populateDestination(select.value);
}

function populateDestination(fromDisplayName) {
  const destinationSelect = byId("destinationSelect");
  destinationSelect.innerHTML =
    '<option value="">Select destination...</option>';
  if (!fromDisplayName) return;

  const selected = allAirlines.find((a) => a.display_name === fromDisplayName);
  if (!selected) return;

  const candidates = allAirlines.filter((a) => a.country === selected.country);
  candidates.forEach((a) => {
    const option = document.createElement("option");
    option.value = a.display_name;
    const groupSuffix = a.group && a.group !== 'UNKNOWN' ? ` - ${a.group}` : '';
    option.textContent = `${a.name} (${a.iata_code}${groupSuffix})`;
    destinationSelect.appendChild(option);
  });

  // Always default destination to the exact same airline selected in "from".
  const exactMatch = candidates.find((a) => a.display_name === fromDisplayName);
  if (exactMatch) {
    destinationSelect.value = exactMatch.display_name;
    return;
  }

  // Fallback: same airport (IATA), even if airline differs.
  const sameIata = candidates.find((a) => a.iata_code === selected.iata_code);
  if (sameIata) {
    destinationSelect.value = sameIata.display_name;
  } else if (candidates.length > 0) {
    destinationSelect.value = candidates[0].display_name;
  }
}

async function fetchAirlinesFallback() {
  try {
    const res = await fetch("/airlines");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!Array.isArray(data) || data.length === 0) {
      throw new Error("Airlines API returned no data");
    }
    applyAirlinesData(data);
    setSseState(false);
  } catch (err) {
    console.warn("Airlines fallback failed:", err);
    applyAirlinesData(MOCK_AIRLINES);
    setError("Could not load live airlines. Showing test data.");
  }
}

function initAirlinesStream() {
  if (typeof EventSource === "undefined") {
    setSseState(false);
    fetchAirlinesFallback();
    return;
  }

  try {
    airlinesEventSource = new EventSource("/airlines/stream");
    airlinesEventSource.onopen = () => setSseState(true);
    airlinesEventSource.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data);
        applyAirlinesData(parsed);
      } catch {
        // Ignore invalid SSE payloads.
      }
    };
    airlinesEventSource.onerror = () => {
      setSseState(false);
      airlinesEventSource.close();
      fetchAirlinesFallback();
      setTimeout(initAirlinesStream, 30000);
    };
  } catch {
    setSseState(false);
    fetchAirlinesFallback();
  }
}

function getScoreClass(label, pct) {
  const lower = String(label || "").toLowerCase();
  if (lower === "excellent" || lower === "very good" || pct >= 85)
    return "good";
  if (lower === "good" || pct >= 60) return "mid";
  return "low";
}

function formatMeals(meals = {}, allInclusive = false) {
  if (allInclusive) return "All inclusive";
  const flags = [
    meals.breakfast ? "Breakfast" : null,
    meals.lunch ? "Lunch" : null,
    meals.dinner ? "Dinner" : null,
  ].filter(Boolean);
  return flags.length ? flags.join(" · ") : "No meals included";
}

function formatDuration(seconds) {
  const totalMinutes = Math.round(Number(seconds) / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatGroups(groups) {
  if (!Array.isArray(groups) || groups.length === 0) return "";
  return `Groups: ${groups.join(", ")}`;
}

function formatRooms(rooms) {
  if (!rooms) return "";
  const total =
    (rooms.single || 0) +
    (rooms.double || 0) +
    (rooms.triple || 0) +
    (rooms.quadruple || 0);
  return total > 0 ? `Availability: ${total}` : "No availability";
}

function renderAllocationCard(alloc) {
  const groupsText = formatGroups(alloc.groups);
  const roomsText = formatRooms(alloc.rooms);
  return `
    <article class="hotel-card">
      <div class="result-top">
        <div>
          <h4 class="title">${escapeHtml(alloc.hotel_name || "Hotel")}</h4>
          <p class="meta">${"⭐".repeat(Number(alloc.stars || 0)) || "No stars"}</p>
        </div>
      </div>
      <div class="meta">Distance: ${Number(alloc.distance_km || 0).toFixed(1)} km</div>
      <div class="meta meta-info-row">
        ${alloc.priority ? `<span class="priority-tag priority-${escapeHtml(alloc.priority)}">Priority: ${escapeHtml(alloc.priority)}</span>` : ""}
        ${alloc.assigned_passengers != null ? `<span class="pax-tag">Pax: ${Number(alloc.assigned_passengers)}</span>` : ""}
        ${alloc.is_estimated ? '<span class="badge badge-estimated">Estimated</span>' : ""}
      </div>
      ${groupsText ? `<p class="meta">${escapeHtml(groupsText)}</p>` : ""}
      ${roomsText ? `<p class="meta">${escapeHtml(roomsText)}</p>` : ""}
      <p class="meta">Meals: ${formatMeals(alloc.meals, alloc.all_inclusive)}</p>
    </article>
  `;
}

function renderResults(data) {
  const results = byId("results");
  const list = Array.isArray(data?.recommendations) ? data.recommendations : [];
  if (!list.length) {
    results.innerHTML =
      '<p class="meta">No recommendations available.</p>';
    return;
  }

  results.innerHTML = list
    .map((item, idx) => {
      if (item.type === "multi") {
        const hotelCards = (item.allocations || []).map(renderAllocationCard).join("");
        const multiBadges = [
          item.is_overflow_forced ? '<span class="badge badge-overflow">Overflow</span>' : "",
          item.pet_friendly ? '<span class="badge badge-pet">Pet Friendly 🐾</span>' : "",
        ].filter(Boolean).join("");
        return `
          <article class="result-card result-card--multi">
            <div class="multi-group-header">
              <h3 class="title">Multi-Hotel &middot; ${Number(item.hotels_used || 0)} hotels</h3>
            </div>
            ${multiBadges ? `<div class="badges">${multiBadges}</div>` : ""}
            <div class="multi-hotel-cards">${hotelCards}</div>
          </article>
        `;
      }

      const pct = Number(item.score_percentage || 0);
      const scoreClass = getScoreClass(item.score_label, pct);
      const breakdownRows = Object.entries(item.score_breakdown || {})
        .map(
          ([k, v]) =>
            `<div class="break-row"><span>${escapeHtml(k)}</span><span>${Number(v).toFixed(3)}</span></div>`,
        )
        .join("");

      return `
        <article class="result-card">
          <div class="result-top">
            <div>
              <h3 class="title">${escapeHtml(item.hotel_name || "Hotel")}</h3>
              <p class="meta">${"⭐".repeat(Number(item.stars || 0)) || "No stars"}</p>
            </div>
          </div>
          <div class="meta">Distance: ${Number(item.distance_km || 0).toFixed(1)} km</div>
          ${item.duration_seconds != null ? `<div class="meta">Estimated travel time: ${formatDuration(item.duration_seconds)}</div>` : ""}
          <div class="meta meta-info-row">
            ${item.priority ? `<span class="priority-tag priority-${escapeHtml(item.priority)}">Priority: ${escapeHtml(item.priority)}</span>` : ""}
            ${item.assigned_passengers != null ? `<span class="pax-tag">Pax: ${Number(item.assigned_passengers)}</span>` : ""}
          </div>
          ${Array.isArray(item.groups) && item.groups.length ? `<p class="meta">${escapeHtml(formatGroups(item.groups))}</p>` : ""}
          ${item.rooms ? `<p class="meta">${escapeHtml(formatRooms(item.rooms))}</p>` : ""}
          <div class="badges">
            ${idx === 0 ? '<span class="badge badge-best">Best Option</span>' : ""}
            ${item.is_estimated ? '<span class="badge badge-estimated">Estimated</span>' : ""}
            ${Number(item.passengers_unassigned || 0) > 0 ? '<span class="badge badge-overflow">Overflow</span>' : ""}
            ${item.pet_friendly ? '<span class="badge badge-pet">Pet Friendly 🐾</span>' : ""}
          </div>
          <div class="score-wrap">
            <div class="score-line">
              <span>${escapeHtml(item.score_label || "Score")}</span>
              <strong>${pct}%</strong>
            </div>
            <div class="score-bar"><div class="score-fill ${scoreClass}" style="width:${Math.max(0, Math.min(100, pct))}%"></div></div>
          </div>
          <button type="button" class="toggle-breakdown">View breakdown</button>
          <div class="score-breakdown">${breakdownRows || '<div class="break-row"><span>No details</span><span>-</span></div>'}</div>
          <p class="meta">Meals: ${formatMeals(item.meals, item.all_inclusive)}</p>
        </article>
      `;
    })
    .join("");

  results.querySelectorAll(".toggle-breakdown").forEach((btn) => {
    btn.addEventListener("click", () => {
      const panel = btn.nextElementSibling;
      panel.classList.toggle("open");
    });
  });
}

function getWeightsIfChanged() {
  const refs = [
    ["distance", byId("weightDistance")],
    ["priority", byId("weightPriority")],
    ["meals", byId("weightMeals")],
  ];
  if (refs.some(([, el]) => !el)) return null;
  const anyChanged = refs.some(([, el]) => Number(el.value) !== 1);
  if (!anyChanged) return null;

  const weights = {};
  refs.forEach(([k, el]) => {
    weights[k] = Number(el.value);
  });
  return weights;
}

function getFiltersIfChanged() {
  const maxPriceEl = byId("maxPrice");
  if (!maxPriceEl) return null;
  const maxPrice = Number(maxPriceEl.value || 0);
  if (maxPrice > 0) return { max_price: maxPrice };
  return null;
}

function buildPayload() {
  const passengers = Number(byId("passengers").value || 0);
  const airline = byId("airlineSelect").value;
  const destination = byId("destinationSelect").value;
  const checkIn = byId("checkIn").value;
  const checkOut = byId("checkOut").value;

  const payload = { passengers, airline, destination };
  const pets = byId("hasPets")?.checked ?? false;
  if (pets) payload.pets = true;
  const weights = getWeightsIfChanged();
  const filters = getFiltersIfChanged();
  if (weights) payload.weights = weights;
  if (filters) payload.filters = filters;
  if (checkIn && checkOut) {
    payload.check_in = new Date(checkIn).toISOString();
    payload.check_out = new Date(checkOut).toISOString();
  }
  return payload;
}

function setLoading(loading) {
  byId("loading").classList.toggle("hidden", !loading);
  byId("submitBtn").disabled = loading;
}

function clearFieldErrors() {
  document.querySelectorAll(".field--invalid").forEach((el) => {
    el.classList.remove("field--invalid");
  });
}

function markFieldInvalid(fieldId) {
  if (!fieldId) return;
  const control = byId(fieldId);
  const field = control?.closest(".field");
  if (field) field.classList.add("field--invalid");
}

function setError(title = "", message = "") {
  const box = byId("errorBox");
  const titleEl = byId("errorTitle");
  const messageEl = byId("errorMessage");
  if (!title && !message) {
    box.classList.add("hidden");
    titleEl.textContent = "";
    messageEl.textContent = "";
    clearFieldErrors();
    return;
  }
  titleEl.textContent = title;
  messageEl.textContent = message;
  box.classList.remove("hidden");
}

function setWarning(title = "", message = "") {
  const box = byId("warningBox");
  const titleEl = byId("warningTitle");
  const messageEl = byId("warningMessage");
  if (!box || !titleEl || !messageEl) return;
  if (!title && !message) {
    box.classList.add("hidden");
    titleEl.textContent = "";
    messageEl.textContent = "";
    return;
  }
  titleEl.textContent = title;
  messageEl.textContent = message;
  box.classList.remove("hidden");
}

function validateForm() {
  const passengers = Number(byId("passengers").value || 0);
  const airline = byId("airlineSelect").value.trim();
  const destination = byId("destinationSelect").value.trim();

  if (!airline) {
    return {
      ok: false,
      fieldId: "airlineSelect",
      title: "Airline not selected",
      message:
        "Please select an airline in Airline From to get recommendations.",
    };
  }
  if (!destination) {
    return {
      ok: false,
      fieldId: "destinationSelect",
      title: "Destination not selected",
      message: "Please select a destination to continue.",
    };
  }
  if (!Number.isFinite(passengers) || passengers < 1) {
    return {
      ok: false,
      fieldId: "passengers",
      title: "Invalid passengers",
      message: "Enter at least 1 passenger.",
    };
  }
  return { ok: true };
}

function friendlyApiError(raw = "") {
  const text = String(raw).toLowerCase();
  if (
    text.includes("'airline' is required") ||
    text.includes("unknown airline")
  ) {
    return {
      fieldId: "airlineSelect",
      title: "Airline not selected",
      message:
        "Please select an airline in Airline From to get recommendations.",
    };
  }
  if (text.includes("destination")) {
    return {
      fieldId: "destinationSelect",
      title: "Invalid destination",
      message: "Select a valid destination from the list.",
    };
  }
  return {
    fieldId: null,
    title: "Could not get recommendations",
    message: raw || "An unexpected error occurred. Please try again.",
  };
}

async function submitForm(event) {
  event.preventDefault();
  setError("");
  setWarning("");
  clearFieldErrors();

  const validation = validateForm();
  if (!validation.ok) {
    markFieldInvalid(validation.fieldId);
    setError(validation.title, validation.message);
    return;
  }

  setLoading(true);

  const payload = buildPayload();
  try {
    const res = await fetch("/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      const friendly = friendlyApiError(errBody.error || `Error ${res.status}`);
      markFieldInvalid(friendly.fieldId);
      setError(friendly.title, friendly.message);
      byId("results").innerHTML = "";
      return;
    }
    const data = await res.json();
    renderResults(data);

    const messages = [];
    const petsRequested = byId("hasPets")?.checked ?? false;
    const recs = Array.isArray(data.recommendations) ? data.recommendations : [];

    if (petsRequested && recs.length > 0 && !recs.some((r) => r.pet_friendly)) {
      messages.push("No pet-friendly hotels were found.");
    }
    if (Array.isArray(data.warnings) && data.warnings.length > 0) {
      messages.push(...data.warnings);
    }

    if (messages.length > 0) {
      const title =
        petsRequested && messages[0].includes("pet-friendly")
          ? "Pet friendly"
          : "Warning";
      setWarning(title, messages.join(" "));
    } else {
      setWarning("");
    }
    window.dispatchEvent(new CustomEvent("recommendations", { detail: data }));
  } catch (err) {
    const friendly = friendlyApiError(err.message);
    markFieldInvalid(friendly.fieldId);
    setError(friendly.title, friendly.message);
    byId("results").innerHTML = "";
  } finally {
    setLoading(false);
  }
}

function bindWeightLabels() {
  const pairs = [
    ["weightDistance", "weightDistanceVal"],
    ["weightPriority", "weightPriorityVal"],
    ["weightMeals", "weightMealsVal"],
  ];
  pairs.forEach(([inputId, labelId]) => {
    const input = byId(inputId);
    const label = byId(labelId);
    if (!input || !label) return;
    const sync = () => {
      label.textContent = input.value;
    };
    input.addEventListener("input", sync);
    sync();
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

document.addEventListener("DOMContentLoaded", () => {
  byId("recommendationForm").addEventListener("submit", submitForm);
  byId("airlineSelect").addEventListener("change", (e) => {
    populateDestination(e.target.value);
    setError("");
    setWarning("");
  });
  ["destinationSelect", "passengers"].forEach((id) => {
    byId(id).addEventListener("change", () => {
      setError("");
      setWarning("");
    });
    byId(id).addEventListener("input", () => {
      setError("");
      setWarning("");
    });
  });
  bindWeightLabels();
  initAirlinesStream();
});
