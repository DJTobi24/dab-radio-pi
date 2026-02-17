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
    albums: [],
    currentAlbum: null,
    playbackSettings: {},
    storageInfo: {}
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
        if (tab.dataset.tab === "music") loadAlbums();
        if (tab.dataset.tab === "settings") {
            loadSettings();
            loadStorage();
            loadPlaybackSettings();
        }
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

    if (!npStatus || !npStation || !npEnsemble) return;

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

const btnStop = document.getElementById("btnStop");
if (btnStop) {
    btnStop.addEventListener("click", async () => {
        await api("/stop", "POST");
        state.isPlaying = false;
        state.currentStation = null;
        updateNowPlaying();
        renderStations();
        renderFavorites();
        toast("Wiedergabe gestoppt");
    });
}

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
        const stationCount = document.getElementById("stationCount");
        if (stationCount) {
            stationCount.textContent = state.stations.length ? `${state.stations.length} Sender` : "";
        }
        renderStations();
    }
}

function renderStations() {
    const list = document.getElementById("stationsList");
    if (!list) return;
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

        const qualityBadge = s.quality ?
            `<span class="quality-badge quality-${getQualityLevel(s.quality)}">${s.quality}%</span>` : '';

        return `
            <div class="station-item ${isPlaying ? 'playing' : ''}" data-idx="${i}">
                <div class="station-icon">${isPlaying ? '▶' : initial}</div>
                <div class="station-info" onclick="playStation(window._stations[${i}])">
                    <div class="station-name">${esc(s.name)}</div>
                    <div class="station-meta">
                        ${esc(s.ensemble_label || '')}
                        ${qualityBadge}
                    </div>
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
    if (!list) return;
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
    if (!list) return;

    if (!devices.length) {
        list.innerHTML = `
            <div class="empty-state">
                <p>Keine Geräte gefunden</p>
                <p class="hint">Schalte deinen Bluetooth-Lautsprecher ein und starte die Suche</p>
            </div>`;
        return;
    }

    // Geräte global speichern für Event-Handler
    window._btDevices = devices;

    list.innerHTML = devices.map((d, i) => `
        <div class="bt-device-item">
            <div class="bt-device-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M6.5 6.5l11 11L12 23V1l5.5 5.5-11 11"/>
                </svg>
            </div>
            <div class="bt-device-info">
                <div class="bt-device-name-text">${esc(d.name)}</div>
                <div class="bt-device-mac">${d.mac}${d.paired ? ' · Gepaart' : ''}</div>
            </div>
            ${d.connected
                ? '<span class="bt-device-badge" style="background:var(--green-glow);color:var(--green)">Verbunden</span>'
                : `<button class="btn-bt-connect" onclick="connectBtDevice(${i})">Verbinden</button>`
            }
        </div>`
    ).join("");
}

// BT Gerät verbinden (über Index im globalen Array)
function connectBtDevice(idx) {
    const d = window._btDevices && window._btDevices[idx];
    if (!d) return;
    const buttons = document.querySelectorAll('.btn-bt-connect');
    // Finde den richtigen Button basierend auf der Position
    let btn = null;
    let btnIdx = 0;
    for (let i = 0; i <= idx; i++) {
        if (window._btDevices[i] && !window._btDevices[i].connected) {
            if (i === idx) btn = buttons[btnIdx];
            btnIdx++;
        }
    }
    connectBt(d.mac, d.name, btn);
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
        btn.disabled = true;
        btn.textContent = "Verbinde...";
        btn.classList.add("connecting");
    }
    toast(`Verbinde mit ${name}...`);

    const res = await api("/bt/connect", "POST", { mac });

    if (res && res.connected) {
        const deviceName = res.name || name;
        state.btConnected = true;
        state.btConnectedMac = mac;
        updateBtStatus(deviceName, mac);
        loadBtDevices();
        toast(`Verbunden mit ${deviceName}`, "success");
    } else {
        toast(res?.message || "Verbindung fehlgeschlagen", "error");
        if (btn) {
            btn.disabled = false;
            btn.textContent = "Verbinden";
            btn.classList.remove("connecting");
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
        nameEl.textContent = name;
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

    // BT Status (Name kommt direkt vom Server, kein extra API-Call nötig)
    if (res.bluetooth) {
        state.btConnected = res.bluetooth.connected;
        state.btConnectedMac = res.bluetooth.connected_mac;

        if (state.btConnected) {
            updateBtStatus(
                res.bluetooth.connected_name || state.btConnectedMac,
                state.btConnectedMac
            );
        } else {
            updateBtStatus(null);
        }
    }
}

// ─── Settings & Network ─────────────────────────────

async function loadSettings() {
    const res = await api("/settings");
    if (!res) return;

    // Load settings into form
    document.getElementById("apSsid").value = res.ap_ssid || "";
    document.getElementById("apPassword").value = res.ap_password || "";
    document.getElementById("defaultVolume").value = res.default_volume || 40;
    document.getElementById("defaultVolumeValue").textContent = res.default_volume || 40;
    document.getElementById("fallbackEnabled").checked = res.fallback_enabled !== false;

    // Load network status
    updateNetworkStatus();
}

async function updateNetworkStatus() {
    const res = await api("/network/status");
    if (!res) return;

    const status = document.getElementById("networkStatus");

    if (res.mode === "client") {
        if (res.connected) {
            status.innerHTML = `
                <div style="display: flex; align-items: center; gap: 0.75rem;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M5 12.55a11 11 0 0 1 14.08 0"/>
                        <path d="M1.42 9a16 16 0 0 1 21.16 0"/>
                        <path d="M8.53 16.11a6 6 0 0 1 6.95 0"/>
                        <line x1="12" y1="20" x2="12.01" y2="20"/>
                    </svg>
                    <div>
                        <div style="font-weight: 600;">✅ Verbunden mit: ${res.ssid}</div>
                        <div style="font-size: 0.8125rem; color: hsl(var(--muted-foreground)); margin-top: 0.125rem;">IP: ${res.ip_address}</div>
                    </div>
                </div>
            `;
        } else {
            status.innerHTML = `
                <div style="display: flex; align-items: center; gap: 0.75rem;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="1" y1="1" x2="23" y2="23"/>
                        <path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55"/>
                        <path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39"/>
                        <path d="M10.71 5.05A16 16 0 0 1 22.58 9"/>
                        <path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88"/>
                        <path d="M8.53 16.11a6 6 0 0 1 6.95 0"/>
                        <line x1="12" y1="20" x2="12.01" y2="20"/>
                    </svg>
                    <div style="font-weight: 600;">⚠️ Client-Modus (Nicht verbunden)</div>
                </div>
            `;
        }
    } else {
        status.innerHTML = `
            <div style="display: flex; align-items: center; gap: 0.75rem;">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>
                    <path d="M2 12h20"/>
                </svg>
                <div style="font-weight: 600;">📡 Access Point Modus (${res.ap_ssid})</div>
            </div>
        `;
    }
}

async function updateApSettings() {
    const ssid = document.getElementById("apSsid").value;
    const password = document.getElementById("apPassword").value;

    if (!ssid || !password) {
        toast("Bitte SSID und Passwort eingeben", "error");
        return;
    }

    if (password.length < 8) {
        toast("Passwort muss mindestens 8 Zeichen lang sein", "error");
        return;
    }

    const res = await api("/settings/ap", "POST", { ssid, password });

    if (res && !res.error) {
        toast("✅ AP-Einstellungen gespeichert. Bitte neustarten.", "success");
    } else {
        toast(res?.error || "Fehler beim Speichern", "error");
    }
}

async function scanWifi() {
    const progress = document.getElementById("wifiScanProgress");
    const list = document.getElementById("wifiNetworkList");

    progress.style.display = "flex";
    list.innerHTML = "";

    toast("Suche WLAN-Netzwerke...");

    const res = await api("/wifi/scan", "POST");

    progress.style.display = "none";

    if (!res || !res.networks || res.networks.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <p>Keine Netzwerke gefunden</p>
                <p class="hint">Stelle sicher, dass WLAN-Netzwerke in Reichweite sind</p>
            </div>
        `;
        return;
    }

    list.innerHTML = res.networks.map(n => `
        <div class="wifi-item" onclick='connectToWifi("${n.ssid.replace(/"/g, '\\"')}", ${n.encrypted})'>
            <div class="wifi-info">
                <div class="wifi-ssid">${esc(n.ssid)}</div>
                <div class="wifi-meta">
                    ${n.encrypted ? '🔒' : '🔓'} Signal: ${n.signal}%
                </div>
            </div>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 18 15 12 9 6"/>
            </svg>
        </div>
    `).join("");

    toast(`${res.networks.length} Netzwerke gefunden`, "success");
}

async function connectToWifi(ssid, encrypted) {
    let password = "";

    if (encrypted) {
        password = prompt(`Passwort für "${ssid}":`);
        if (!password) return;
    }

    toast(`Verbinde mit ${ssid}...`);

    const res = await api("/wifi/connect", "POST", { ssid, password });

    if (res && res.connected) {
        toast(`✅ Verbunden mit ${ssid}`, "success");
        // Wait a bit then reload status
        setTimeout(() => {
            updateNetworkStatus();
            // Update fallback checkbox
            updateFallbackSetting();
        }, 2000);
    } else {
        toast("❌ Verbindung fehlgeschlagen", "error");
    }
}

async function switchToApMode() {
    if (!confirm("Zurück zum Access Point Modus wechseln?")) return;

    toast("Wechsle zu AP-Modus...");

    await api("/wifi/disconnect", "POST");

    setTimeout(() => {
        toast("AP-Modus aktiviert", "success");
        updateNetworkStatus();
    }, 3000);
}

async function updateDefaultVolume() {
    const volume = parseInt(document.getElementById("defaultVolume").value);

    await api("/settings/volume", "POST", { volume });

    toast("✅ Standard-Lautstärke gespeichert", "success");
}

async function updateFallbackSetting() {
    const enabled = document.getElementById("fallbackEnabled").checked;
    await api("/settings/fallback", "POST", { enabled });
}

// Update default volume display on slider change
const defaultVolumeSlider = document.getElementById("defaultVolume");
if (defaultVolumeSlider) {
    defaultVolumeSlider.addEventListener("input", () => {
        document.getElementById("defaultVolumeValue").textContent = defaultVolumeSlider.value;
    });
}

// Update fallback setting on checkbox change
const fallbackCheckbox = document.getElementById("fallbackEnabled");
if (fallbackCheckbox) {
    fallbackCheckbox.addEventListener("change", updateFallbackSetting);
}

// ─── Music ──────────────────────────────────────────

async function loadAlbums() {
    const res = await api("/albums");
    if (res) {
        state.albums = res.albums || [];
        renderAlbums();
    }
}

function renderAlbums() {
    const list = document.getElementById("albumsList");
    if (!state.albums.length) {
        list.innerHTML = `<div class="empty-state"><p>Noch keine Alben</p><p class="hint">Erstelle ein Album und lade Musik hoch</p></div>`;
        return;
    }

    list.innerHTML = state.albums.map(a => `
        <div class="album-card" data-id="${a.id}">
            <div class="album-cover">${a.cover_art ? `<img src="/api/albums/${a.id}/cover" alt="Cover">` : '🎵'}</div>
            <div class="album-info">
                <div class="album-name">${esc(a.name)}</div>
                <div class="album-meta">${a.track_count} Titel · ${formatSize(a.total_size)}</div>
            </div>
            <div class="album-actions">
                <button onclick="playAlbum('${a.id}')" title="Abspielen">▶</button>
                <button onclick="showUploadModal('${a.id}')" title="Musik hinzufügen">+</button>
                <button onclick="deleteAlbum('${a.id}')" title="Löschen">×</button>
            </div>
        </div>
    `).join('');
}

async function createAlbum() {
    const nameInput = document.getElementById("newAlbumName");
    const descInput = document.getElementById("newAlbumDesc");

    const name = nameInput.value.trim();
    if (!name) {
        toast("Bitte Album-Name eingeben");
        return;
    }

    const description = descInput.value.trim();
    const res = await api("/albums", "POST", { name, description });

    if (res && res.album) {
        toast("Album erstellt!");
        nameInput.value = "";
        descInput.value = "";
        await loadAlbums();
    } else {
        toast("Fehler beim Erstellen", "error");
    }
}

function showUploadModal(albumId) {
    window._uploadAlbumId = albumId;
    const modal = document.getElementById("uploadModal");
    modal.style.display = "flex";
    document.getElementById("fileUpload").value = "";
}

function closeUploadModal() {
    document.getElementById("uploadModal").style.display = "none";
}

async function uploadTracks() {
    const files = document.getElementById("fileUpload").files;
    if (!files.length) {
        toast("Keine Dateien ausgewählt");
        return;
    }

    const formData = new FormData();
    for (let file of files) {
        formData.append("files", file);
    }

    toast("Lade hoch...", "info");

    try {
        const res = await fetch(`/api/albums/${window._uploadAlbumId}/upload`, {
            method: "POST",
            body: formData
        });
        const data = await res.json();

        if (data.success) {
            toast(`${data.uploaded} ${data.uploaded === 1 ? 'Datei' : 'Dateien'} hochgeladen`);
            await loadAlbums();
            closeUploadModal();
        } else {
            toast(data.error || "Upload fehlgeschlagen", "error");
            if (data.errors && data.errors.length > 0) {
                console.error("Upload errors:", data.errors);
            }
        }
    } catch (e) {
        toast("Upload fehlgeschlagen", "error");
        console.error("Upload error:", e);
    }
}

async function playAlbum(albumId) {
    const res = await api(`/albums/${albumId}/play`, "POST", {});
    if (res && res.status === "playing") {
        toast("Album wird abgespielt");
        await pollStatus();
    } else {
        toast(res?.error || "Fehler beim Abspielen", "error");
    }
}

async function deleteAlbum(albumId) {
    const album = state.albums.find(a => a.id === albumId);
    if (!album) return;

    if (!confirm(`Album "${album.name}" wirklich löschen?`)) return;

    const res = await api(`/albums/${albumId}`, "DELETE");
    if (res && res.status === "deleted") {
        toast("Album gelöscht");
        await loadAlbums();
    } else {
        toast("Fehler beim Löschen", "error");
    }
}

function formatSize(bytes) {
    if (!bytes || bytes === 0) return "0 KB";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

// ─── Storage ────────────────────────────────────────

async function loadStorage() {
    const res = await api("/storage");
    if (res) {
        state.storageInfo = res;
        updateStorageDisplay();
    }
}

function updateStorageDisplay() {
    const info = state.storageInfo;
    if (!info) return;

    const usedEl = document.getElementById("storageUsed");
    const availEl = document.getElementById("storageAvailable");
    const barEl = document.getElementById("storageUsedBar");

    if (usedEl) usedEl.textContent = `${info.used_gb} GB verwendet`;
    if (availEl) availEl.textContent = `${info.available_gb} GB frei`;
    if (barEl) barEl.style.width = `${info.percent_used}%`;
}

// ─── Playback Mode ──────────────────────────────────

async function loadPlaybackSettings() {
    const res = await api("/playback/settings");
    if (res) {
        state.playbackSettings = res;
        renderPlaybackSettings();
    }
}

function renderPlaybackSettings() {
    const settings = state.playbackSettings;
    const modeSelect = document.getElementById("playbackMode");
    const autoStartCheckbox = document.getElementById("autoStartPlayback");

    if (modeSelect) {
        modeSelect.value = settings.mode || "off";
    }

    if (autoStartCheckbox) {
        autoStartCheckbox.checked = settings.auto_start_on_boot || false;
    }

    updatePlaybackModeUI();

    // Populate preset selects
    if (settings.mode === "dab_preset") {
        populatePresetStationSelect();
    } else if (settings.mode === "album") {
        populatePresetAlbumSelect();
    }
}

function updatePlaybackMode() {
    const mode = document.getElementById("playbackMode").value;

    const stationDiv = document.getElementById("presetStationSelect");
    const albumDiv = document.getElementById("presetAlbumSelect");

    if (stationDiv) stationDiv.style.display = (mode === "dab_preset") ? "block" : "none";
    if (albumDiv) albumDiv.style.display = (mode === "album") ? "block" : "none";

    if (mode === "dab_preset") populatePresetStationSelect();
    if (mode === "album") populatePresetAlbumSelect();
}

function updatePlaybackModeUI() {
    updatePlaybackMode();
}

function populatePresetStationSelect() {
    const select = document.getElementById("presetStation");
    if (!select) return;

    select.innerHTML = state.favorites.map((s, i) =>
        `<option value="${i}">${esc(s.name)}</option>`
    ).join('');

    // Select current preset if exists
    if (state.playbackSettings.preset_station) {
        const preset = state.playbackSettings.preset_station;
        const idx = state.favorites.findIndex(f =>
            f.service_id === preset.service_id && f.ensemble_id === preset.ensemble_id
        );
        if (idx >= 0) select.value = idx;
    }
}

function populatePresetAlbumSelect() {
    const select = document.getElementById("presetAlbum");
    if (!select) return;

    select.innerHTML = state.albums.map(a =>
        `<option value="${a.id}">${esc(a.name)}</option>`
    ).join('');

    // Select current preset if exists
    if (state.playbackSettings.preset_album_id) {
        select.value = state.playbackSettings.preset_album_id;
    }
}

async function savePlaybackSettings() {
    const mode = document.getElementById("playbackMode").value;
    const autoStart = document.getElementById("autoStartPlayback").checked;

    let body = {
        mode: mode,
        auto_start_on_boot: autoStart
    };

    // Add mode-specific settings
    if (mode === "dab_preset") {
        const stationIdx = parseInt(document.getElementById("presetStation").value);
        if (!isNaN(stationIdx) && state.favorites[stationIdx]) {
            body.preset_station = state.favorites[stationIdx];
        }
    } else if (mode === "album") {
        const albumId = document.getElementById("presetAlbum").value;
        if (albumId) {
            body.preset_album_id = albumId;
        }
    }

    const res = await api("/playback/mode", "POST", body);
    if (res && res.status === "ok") {
        toast("Einstellungen gespeichert");
        state.playbackSettings = body;
    } else {
        toast("Fehler beim Speichern", "error");
    }
}

// ─── DAB Board Status ───────────────────────────────

async function checkDabBoard() {
    const statusDiv = document.getElementById("dabBoardStatus");
    if (!statusDiv) return;

    statusDiv.innerHTML = `<div class="spinner"></div><span>Prüfe DAB Board...</span>`;

    const res = await api("/dab/board/status");
    if (res) {
        const icon = res.detected ? "✅" : "❌";
        const statusClass = res.detected ? "status-ok" : "status-error";
        statusDiv.innerHTML = `
            <div class="${statusClass}">
                <span>${icon} ${esc(res.message)}</span>
            </div>
        `;
    } else {
        statusDiv.innerHTML = `<div class="status-error"><span>❌ Prüfung fehlgeschlagen</span></div>`;
    }
}

// ─── Quality Badges ─────────────────────────────────

function getQualityLevel(quality) {
    if (quality >= 61) return "good";
    if (quality >= 31) return "medium";
    return "low";
}

// ─── Helpers ────────────────────────────────────────

function esc(str) {
    if (!str) return "";
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
