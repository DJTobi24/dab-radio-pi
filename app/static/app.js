/**
 * DAB+ Radio Web Interface
 * Frontend-Logik für Sender-Steuerung, Bluetooth & Favoriten
 */

// ─── State ──────────────────────────────────────────

const state = {
    stations: [],
    favorites: [],
    currentStation: null,
    isPlaying: false,
    volume: 40,
    btConnected: false,
    btConnectedMac: null,
    btScanning: false,
    dabScanning: false,
};

// ─── API Helper ─────────────────────────────────────

async function api(path, method = "GET", body = null) {
    const opts = { method, headers: {} };
    if (body) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
    }
    try {
        const res = await fetch(`/api${path}`, opts);
        return await res.json();
    } catch (e) {
        console.error("API error:", e);
        return null;
    }
}

// ─── Toast ──────────────────────────────────────────

function toast(message, type = "") {
    const existing = document.querySelector(".toast");
    if (existing) existing.remove();

    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = message;
    document.body.appendChild(el);

    requestAnimationFrame(() => {
        requestAnimationFrame(() => el.classList.add("show"));
    });

    setTimeout(() => {
        el.classList.remove("show");
        setTimeout(() => el.remove(), 300);
    }, 2500);
}

// ─── Tabs ───────────────────────────────────────────

document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
        tab.classList.add("active");
        document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");

        // Lade Daten beim Tab-Wechsel
        if (tab.dataset.tab === "bluetooth") loadBtDevices();
        if (tab.dataset.tab === "stations") loadStations();
        if (tab.dataset.tab === "favorites") loadFavorites();
    });
});

// ─── Volume ─────────────────────────────────────────

const volumeSlider = document.getElementById("volumeSlider");
const volValue = document.getElementById("volValue");
let volumeTimeout = null;

volumeSlider.addEventListener("input", () => {
    volValue.textContent = volumeSlider.value;
    state.volume = parseInt(volumeSlider.value);

    // Debounce API-Call
    clearTimeout(volumeTimeout);
    volumeTimeout = setTimeout(() => {
        api("/volume", "POST", { level: state.volume });
    }, 200);
});

// ─── Now Playing ────────────────────────────────────

function updateNowPlaying() {
    const npStatus = document.getElementById("npStatus");
    const npStation = document.getElementById("npStation");
    const npEnsemble = document.getElementById("npEnsemble");

    if (state.isPlaying && state.currentStation) {
        npStatus.textContent = "Läuft";
        npStatus.className = "np-status playing";
        npStation.textContent = state.currentStation.name || "Unbekannt";
        npEnsemble.textContent = state.currentStation.ensemble_label || "";
    } else {
        npStatus.textContent = "Kein Sender";
        npStatus.className = "np-status";
        npStation.textContent = "—";
        npEnsemble.textContent = "";
    }
}

// ─── Stop ───────────────────────────────────────────

document.getElementById("btnStop").addEventListener("click", async () => {
    await api("/stop", "POST");
    state.isPlaying = false;
    state.currentStation = null;
    updateNowPlaying();
    renderStations();
    renderFavorites();
    toast("Wiedergabe gestoppt");
});

// ─── Station abspielen ──────────────────────────────

async function playStation(station) {
    toast(`Tune ${station.name}...`);
    const res = await api("/play", "POST", station);
    if (res && !res.error) {
        state.isPlaying = true;
        state.currentStation = station;
        updateNowPlaying();
        renderStations();
        renderFavorites();
        toast(`▶ ${station.name}`, "success");
    } else {
        toast(res?.error || "Fehler beim Abspielen", "error");
    }
}

// ─── Stationen laden/rendern ────────────────────────

async function loadStations() {
    const res = await api("/stations");
    if (res) {
        state.stations = res.stations || [];
        document.getElementById("stationCount").textContent =
            state.stations.length ? `${state.stations.length} Sender` : "";
        renderStations();
    }
}

function renderStations() {
    const list = document.getElementById("stationsList");
    if (!state.stations.length) {
        list.innerHTML = `
            <div class="empty-state">
                <p>Keine Sender gefunden</p>
                <p class="hint">Starte einen Sendersuchlauf</p>
            </div>`;
        return;
    }

    list.innerHTML = state.stations.map((s, i) => {
        const isPlaying = state.currentStation &&
            state.currentStation.service_id === s.service_id &&
            state.currentStation.ensemble_id === s.ensemble_id;
        const isFav = state.favorites.some(f =>
            f.service_id === s.service_id && f.ensemble_id === s.ensemble_id);
        const initial = (s.name || "?")[0].toUpperCase();

        return `
            <div class="station-item ${isPlaying ? 'playing' : ''}" data-idx="${i}">
                <div class="station-icon">${isPlaying ? '▶' : initial}</div>
                <div class="station-info" onclick="playStation(window._stations[${i}])">
                    <div class="station-name">${esc(s.name)}</div>
                    <div class="station-meta">${esc(s.ensemble_label || '')}</div>
                </div>
                <button class="btn-fav ${isFav ? 'is-fav' : ''}" onclick="toggleFavorite(${i})">
                    ${isFav ? '★' : '☆'}
                </button>
            </div>`;
    }).join("");

    window._stations = state.stations;
}

// ─── DAB Scan ───────────────────────────────────────

document.getElementById("btnScan").addEventListener("click", async () => {
    if (state.dabScanning) return;
    state.dabScanning = true;

    const btn = document.getElementById("btnScan");
    const progress = document.getElementById("scanProgress");
    btn.classList.add("scanning");
    progress.style.display = "flex";

    await api("/scan", "POST");

    // Polling für Scan-Ergebnis
    const poll = setInterval(async () => {
        const res = await api("/scan/status");
        if (res && res.count > 0) {
            state.stations = res.stations;
            state.dabScanning = false;
            clearInterval(poll);

            btn.classList.remove("scanning");
            progress.style.display = "none";
            document.getElementById("stationCount").textContent = `${res.count} Sender`;
            renderStations();
            toast(`${res.count} Sender gefunden`, "success");
        }
    }, 3000);

    // Timeout nach 3 Minuten
    setTimeout(() => {
        if (state.dabScanning) {
            state.dabScanning = false;
            clearInterval(poll);
            btn.classList.remove("scanning");
            progress.style.display = "none";
            loadStations();
            toast("Suchlauf beendet");
        }
    }, 180000);
});

// ─── Favoriten ──────────────────────────────────────

async function loadFavorites() {
    const res = await api("/favorites");
    if (res) {
        state.favorites = res.favorites || [];
        renderFavorites();
    }
}

function renderFavorites() {
    const list = document.getElementById("favoritesList");
    if (!state.favorites.length) {
        list.innerHTML = `
            <div class="empty-state">
                <p>Noch keine Favoriten</p>
                <p class="hint">Tippe ★ bei einem Sender um ihn zu speichern</p>
            </div>`;
        return;
    }

    list.innerHTML = state.favorites.map((s, i) => {
        const isPlaying = state.currentStation &&
            state.currentStation.service_id === s.service_id &&
            state.currentStation.ensemble_id === s.ensemble_id;
        const initial = (s.name || "?")[0].toUpperCase();

        return `
            <div class="station-item ${isPlaying ? 'playing' : ''}">
                <div class="station-icon" style="color:var(--accent)">${isPlaying ? '▶' : initial}</div>
                <div class="station-info" onclick="playStation(window._favorites[${i}])">
                    <div class="station-name">${esc(s.name)}</div>
                    <div class="station-meta">${esc(s.ensemble_label || '')}</div>
                </div>
                <button class="btn-fav-remove" onclick="removeFavorite(${i})" title="Entfernen">✕</button>
            </div>`;
    }).join("");

    window._favorites = state.favorites;
}

async function toggleFavorite(stationIdx) {
    const station = state.stations[stationIdx];
    const isFav = state.favorites.some(f =>
        f.service_id === station.service_id && f.ensemble_id === station.ensemble_id);

    if (isFav) {
        const favIdx = state.favorites.findIndex(f =>
            f.service_id === station.service_id && f.ensemble_id === station.ensemble_id);
        await removeFavorite(favIdx);
    } else {
        const res = await api("/favorites", "POST", station);
        if (res) {
            state.favorites = res.favorites || [];
            renderStations();
            renderFavorites();
            toast(`★ ${station.name} gespeichert`, "success");
        }
    }
}

async function removeFavorite(idx) {
    const name = state.favorites[idx]?.name || "";
    const res = await api(`/favorites/${idx}`, "DELETE");
    if (res) {
        state.favorites = res.favorites || [];
        renderStations();
        renderFavorites();
        toast(`${name} entfernt`);
    }
}

// ─── Bluetooth ──────────────────────────────────────

async function loadBtDevices() {
    const res = await api("/bt/devices");
    if (!res) return;

    const devices = res.devices || [];
    renderBtDevices(devices);
}

function renderBtDevices(devices) {
    const list = document.getElementById("btDeviceList");

    if (!devices.length) {
        list.innerHTML = `
            <div class="empty-state">
                <p>Keine Geräte gefunden</p>
                <p class="hint">Schalte deinen Bluetooth-Lautsprecher ein und starte die Suche</p>
            </div>`;
        return;
    }

    list.innerHTML = devices.map(d => {
        const isConnected = d.connected;
        return `
            <div class="bt-device-item">
                <div class="bt-device-icon">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M6.5 6.5l11 11L12 23V1l5.5 5.5-11 11"/>
                    </svg>
                </div>
                <div class="bt-device-info">
                    <div class="bt-device-name-text">${esc(d.name)}</div>
                    <div class="bt-device-mac">${d.mac}</div>
                </div>
                ${d.paired ? '<span class="bt-device-badge">Gepaart</span>' : ''}
                ${isConnected
                    ? '<span class="bt-device-badge" style="background:var(--green-glow);color:var(--green)">Verbunden</span>'
                    : `<button class="btn-bt-connect" onclick="connectBt('${d.mac}', '${esc(d.name)}', this)">Verbinden</button>`
                }
            </div>`;
    }).join("");
}

// BT Scan
document.getElementById("btnBtScan").addEventListener("click", async () => {
    if (state.btScanning) return;
    state.btScanning = true;

    const progress = document.getElementById("btScanProgress");
    progress.style.display = "flex";

    await api("/bt/scan", "POST");

    // Poll für neue Geräte während Scan läuft
    const poll = setInterval(async () => {
        const scanRes = await api("/bt/scan/status");
        loadBtDevices();

        if (scanRes && !scanRes.scanning) {
            state.btScanning = false;
            clearInterval(poll);
            progress.style.display = "none";
            toast("Bluetooth-Suche beendet");
        }
    }, 3000);

    // Timeout
    setTimeout(() => {
        if (state.btScanning) {
            state.btScanning = false;
            clearInterval(poll);
            progress.style.display = "none";
        }
    }, 20000);
});

// BT Connect
async function connectBt(mac, name, btn) {
    if (btn) {
        btn.classList.add("connecting");
        btn.textContent = "...";
    }
    toast(`Verbinde mit ${name}...`);

    const res = await api("/bt/connect", "POST", { mac });

    if (res && res.connected) {
        state.btConnected = true;
        state.btConnectedMac = mac;
        updateBtStatus(name, mac);
        loadBtDevices();
        toast(`🔵 ${name} verbunden`, "success");
    } else {
        toast("Verbindung fehlgeschlagen", "error");
        if (btn) {
            btn.classList.remove("connecting");
            btn.textContent = "Verbinden";
        }
    }
}

// BT Disconnect
document.getElementById("btnBtDisconnect").addEventListener("click", async () => {
    await api("/bt/disconnect", "POST");
    state.btConnected = false;
    state.btConnectedMac = null;
    updateBtStatus(null);
    loadBtDevices();
    toast("Bluetooth getrennt");
});

function updateBtStatus(name, mac) {
    const indicator = document.getElementById("btIndicator");
    const info = document.getElementById("btConnectedInfo");
    const nameEl = document.getElementById("btDeviceName");

    if (name) {
        indicator.classList.add("connected");
        info.style.display = "flex";
        nameEl.textContent = `🔵 ${name}`;
    } else {
        indicator.classList.remove("connected");
        info.style.display = "none";
    }
}

// ─── Status Polling ─────────────────────────────────

async function pollStatus() {
    const res = await api("/status");
    if (!res) return;

    // Radio Status
    if (res.radio) {
        state.isPlaying = res.radio.is_playing;
        state.currentStation = res.radio.current_station;
        state.volume = res.radio.volume;
        volumeSlider.value = state.volume;
        volValue.textContent = state.volume;
        updateNowPlaying();
    }

    // BT Status
    if (res.bluetooth) {
        state.btConnected = res.bluetooth.connected;
        state.btConnectedMac = res.bluetooth.connected_mac;

        if (state.btConnected) {
            // Hole Gerätename
            const devRes = await api("/bt/devices");
            if (devRes) {
                const dev = (devRes.devices || []).find(d => d.mac === state.btConnectedMac);
                updateBtStatus(dev?.name || state.btConnectedMac, state.btConnectedMac);
            }
        } else {
            updateBtStatus(null);
        }
    }
}

// ─── Helpers ────────────────────────────────────────

function esc(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// ─── Init ───────────────────────────────────────────

async function init() {
    await pollStatus();
    await loadStations();
    await loadFavorites();

    // Status alle 10 Sekunden aktualisieren
    setInterval(pollStatus, 10000);
}

init();
