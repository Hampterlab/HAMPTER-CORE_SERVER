let projectionConfig = {};
let portsData = { outports: [], inports: [] };
let routingData = { matrix: {}, connection_count: 0 };
let connections = [];
let currentDevices = [];
let virtualToolsData = {};
let portDebugData = { router_stats: {}, snapshot: { latest_outports: [], latest_inports: [], recent_events: [] } };
let vtBindingCounter = 0;
let activeTab = 'connections';
let projectionDirty = false;
let realtimeSource = null;
let realtimeReconnectTimer = null;
let lastRevisions = null;

const inFlight = {
    tools: false,
    matrix: false,
    connections: false,
    virtual: false,
    portDebug: false,
};

function escapeHtml(value) {
    const str = String(value ?? '');
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function encArg(value) {
    return encodeURIComponent(String(value ?? ''));
}

async function fetchJson(url, options = undefined) {
    const res = await fetch(url, options);
    let data = {};
    try {
        data = await res.json();
    } catch (_e) {
        data = {};
    }
    if (!res.ok) {
        throw new Error(data.error || data.message || `${res.status} ${res.statusText}`);
    }
    return data;
}

function setDirty(enabled) {
    projectionDirty = enabled;
    const el = document.getElementById('dirty-indicator');
    el.style.display = enabled ? 'inline-flex' : 'none';
}

function addActivity(message, level = 'info') {
    const list = document.getElementById('activity-log');
    const li = document.createElement('li');
    li.className = `activity-${level}`;
    const time = new Date().toLocaleTimeString();
    li.textContent = `${time} - ${message}`;
    list.prepend(li);

    while (list.children.length > 8) {
        list.removeChild(list.lastChild);
    }
}

function showAlert(msg, type = 'success') {
    const el = document.getElementById('alert');
    el.textContent = msg;
    el.className = `alert alert-${type}`;
    el.style.display = 'block';
    addActivity(msg, type === 'error' ? 'error' : 'info');
    setTimeout(() => {
        el.style.display = 'none';
    }, 4000);
}

function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

    document.querySelector(`.tab[onclick="switchTab('${tabName}')"]`).classList.add('active');
    document.getElementById(`tab-${tabName}`).classList.add('active');
    activeTab = tabName;

    if (tabName === 'tools') loadToolsData();
    else if (tabName === 'matrix') loadMatrixData();
    else if (tabName === 'connections') loadConnectionsData();
    else if (tabName === 'ports') loadPortDebugData();
    else if (tabName === 'virtual') loadVirtualToolsData();
}

function setBridgeStatus(online) {
    const el = document.getElementById('bridge-status');
    el.className = `status-chip ${online ? 'online' : 'offline'}`;
    el.innerHTML = `<span class="led"></span>Bridge ${online ? 'online' : 'offline'}`;
}

function setDockerStatus(running) {
    const el = document.getElementById('docker-status');
    el.className = `status-chip ${running ? 'online' : 'offline'}`;
    el.innerHTML = `<span class="led"></span>Docker ${running ? 'running' : 'stopped'}`;
}

function setStreamStatus(connected) {
    const el = document.getElementById('stream-status');
    el.className = `stream-chip ${connected ? 'online' : 'offline'}`;
    el.textContent = `Realtime: ${connected ? 'connected' : 'disconnected'}`;
}

function updateLiveStats(counts) {
    document.getElementById('live-devices').textContent = counts.devices_total ?? 0;
    document.getElementById('live-online').textContent = counts.devices_online ?? 0;
    document.getElementById('live-connections').textContent = counts.connections ?? 0;
    document.getElementById('live-vtools').textContent = counts.virtual_tools ?? 0;
}

function refreshByRevisions(revisions) {
    if (!lastRevisions) {
        lastRevisions = revisions;
        return;
    }

    if (revisions.devices !== lastRevisions.devices) {
        if (activeTab === 'tools' || activeTab === 'virtual') {
            if (activeTab === 'tools') loadToolsData({ silent: true });
            if (activeTab === 'virtual') loadVirtualToolsData({ silent: true });
        }
    }

    if (revisions.connections !== lastRevisions.connections) {
        if (activeTab === 'connections') loadConnectionsData({ silent: true });
    }

    if (revisions.routing !== lastRevisions.routing) {
        if (activeTab === 'matrix') loadMatrixData({ silent: true });
    }

    if (revisions.virtual_tools !== lastRevisions.virtual_tools) {
        if (activeTab === 'virtual') loadVirtualToolsData({ silent: true });
    }

    if (revisions.status !== lastRevisions.status && activeTab === 'tools') {
        loadToolsData({ silent: true });
    }

    lastRevisions = revisions;
}

function connectRealtime() {
    if (realtimeSource) realtimeSource.close();

    realtimeSource = new EventSource('/api/stream');

    realtimeSource.addEventListener('snapshot', (ev) => {
        try {
            const snap = JSON.parse(ev.data);
            setBridgeStatus(!!snap.bridge_healthy);
            setDockerStatus(!!snap.docker_running);
            updateLiveStats(snap.counts || {});
            setStreamStatus(true);
            refreshByRevisions(snap.revisions || {});
            if (activeTab === 'ports') loadPortDebugData({ silent: true });
        } catch (e) {
            console.error('snapshot parse error', e);
        }
    });

    realtimeSource.addEventListener('ping', () => {
        setStreamStatus(true);
        if (activeTab === 'ports') loadPortDebugData({ silent: true });
    });

    realtimeSource.onerror = () => {
        setStreamStatus(false);
        if (realtimeSource) {
            realtimeSource.close();
            realtimeSource = null;
        }
        if (!realtimeReconnectTimer) {
            realtimeReconnectTimer = setTimeout(() => {
                realtimeReconnectTimer = null;
                connectRealtime();
            }, 2000);
        }
    };
}

async function loadToolsData(options = {}) {
    if (inFlight.tools) return;
    inFlight.tools = true;
    try {
        projectionConfig = await fetchJson('/api/projection/config');
        currentDevices = await fetchJson('/api/devices');
        renderDevices();
        renderGlobalSettings();
        if (!options.silent) addActivity('Tool projection data refreshed');
    } catch (e) {
        showAlert('Failed to load tools data: ' + e.message, 'error');
    } finally {
        inFlight.tools = false;
    }
}

function renderDevices() {
    const container = document.getElementById('devices-container');
    const showOffline = document.getElementById('show-offline-devices')?.checked;
    const query = (document.getElementById('device-search')?.value || '').toLowerCase().trim();

    if (!currentDevices || currentDevices.length === 0) {
        container.innerHTML = '<p>No registered devices.</p>';
        return;
    }

    let filtered = currentDevices.filter(d => d.online || showOffline);
    if (query) {
        filtered = filtered.filter(d => {
            const tools = (d.tools || []).map(t => `${t.name} ${t.description || ''}`).join(' ').toLowerCase();
            const label = `${d.name || ''} ${d.device_id || ''}`.toLowerCase();
            return label.includes(query) || tools.includes(query);
        });
    }

    if (filtered.length === 0) {
        container.innerHTML = '<p>No matching devices.</p>';
        return;
    }

    container.innerHTML = filtered.map(device => {
        const deviceId = device.device_id;
        const deviceIdEnc = encArg(deviceId);
        const projection = projectionConfig.devices?.[deviceId] || {};
        const tools = device.tools || [];
        const isOffline = !device.online;
        const disabled = isOffline ? 'disabled' : '';

        return `
            <div class="device-card ${isOffline ? 'device-offline' : ''}">
                <div class="device-header">
                    <div class="device-info">
                        <h3>${escapeHtml(device.name || deviceId)}</h3>
                        <div class="device-id">${escapeHtml(deviceId)}</div>
                    </div>
                    <div class="device-controls">
                        <span class="status-chip ${device.online ? 'online' : 'offline'}"><span class="led"></span>${device.online ? 'Online' : 'Offline'}</span>
                        <label class="checkbox-inline"><input type="checkbox" ${projection.enabled !== false ? 'checked' : ''}
                            onchange="updateDeviceEnabled(decodeURIComponent('${deviceIdEnc}'), this.checked)" ${disabled}> Enabled</label>
                    </div>
                </div>
                <div class="device-tools">
                    <div class="tool-row">
                        <label>Device Alias</label>
                        <input type="text" value="${escapeHtml(projection.device_alias || '')}" onchange="updateDeviceAlias(decodeURIComponent('${deviceIdEnc}'), this.value)" placeholder="Display name" ${disabled}>
                    </div>
                    <div class="tool-list">
                        ${tools.map(tool => renderTool(deviceId, tool, projection.tools?.[tool.name] || {}, isOffline)).join('')}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function renderTool(deviceId, tool, toolProjection, isOffline) {
    const disabled = isOffline ? 'disabled' : '';
    const deviceIdEnc = encArg(deviceId);
    const toolNameEnc = encArg(tool.name);
    return `
        <div class="tool-item">
            <div class="tool-head">
                <strong>${escapeHtml(tool.name)}</strong>
                <span>${escapeHtml(tool.description || 'No description')}</span>
            </div>
            <div class="tool-controls">
                <label class="checkbox-inline"><input type="checkbox" ${toolProjection.enabled !== false ? 'checked' : ''}
                    onchange="updateToolEnabled(decodeURIComponent('${deviceIdEnc}'), decodeURIComponent('${toolNameEnc}'), this.checked)" ${disabled}> Enabled</label>
                <input type="text" value="${escapeHtml(toolProjection.alias || '')}" onchange="updateToolAlias(decodeURIComponent('${deviceIdEnc}'), decodeURIComponent('${toolNameEnc}'), this.value)" placeholder="Alias" ${disabled}>
                <input type="text" value="${escapeHtml(toolProjection.description || '')}" onchange="updateToolDescription(decodeURIComponent('${deviceIdEnc}'), decodeURIComponent('${toolNameEnc}'), this.value)" placeholder="Custom description" ${disabled}>
            </div>
        </div>
    `;
}

function renderGlobalSettings() {
    document.getElementById('auto-enable-devices').checked = projectionConfig.global?.auto_enable_new_devices !== false;
    document.getElementById('auto-enable-tools').checked = projectionConfig.global?.auto_enable_new_tools !== false;
}

function ensureDeviceConfig(deviceId) {
    if (!projectionConfig.devices) projectionConfig.devices = {};
    if (!projectionConfig.devices[deviceId]) {
        projectionConfig.devices[deviceId] = { enabled: true, device_alias: null, tools: {} };
    }
}

function ensureToolConfig(deviceId, toolName) {
    ensureDeviceConfig(deviceId);
    if (!projectionConfig.devices[deviceId].tools) projectionConfig.devices[deviceId].tools = {};
    if (!projectionConfig.devices[deviceId].tools[toolName]) {
        projectionConfig.devices[deviceId].tools[toolName] = { enabled: true, alias: null, description: null };
    }
}

function updateDeviceEnabled(deviceId, enabled) { ensureDeviceConfig(deviceId); projectionConfig.devices[deviceId].enabled = enabled; setDirty(true); }
function updateDeviceAlias(deviceId, alias) { ensureDeviceConfig(deviceId); projectionConfig.devices[deviceId].device_alias = alias || null; setDirty(true); }
function updateToolEnabled(deviceId, toolName, enabled) { ensureToolConfig(deviceId, toolName); projectionConfig.devices[deviceId].tools[toolName].enabled = enabled; setDirty(true); }
function updateToolAlias(deviceId, toolName, alias) { ensureToolConfig(deviceId, toolName); projectionConfig.devices[deviceId].tools[toolName].alias = alias || null; setDirty(true); }
function updateToolDescription(deviceId, toolName, desc) { ensureToolConfig(deviceId, toolName); projectionConfig.devices[deviceId].tools[toolName].description = desc || null; setDirty(true); }

async function saveProjectionConfig() {
    try {
        projectionConfig.global = {
            auto_enable_new_devices: document.getElementById('auto-enable-devices').checked,
            auto_enable_new_tools: document.getElementById('auto-enable-tools').checked
        };
        await fetchJson('/api/projection/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(projectionConfig)
        });
        setDirty(false);
        showAlert('Projection configuration saved', 'success');
    } catch (e) {
        showAlert('Save failed: ' + e.message, 'error');
    }
}

async function reloadBridgeConfig() {
    try {
        const result = await fetchJson('/api/bridge/reload', { method: 'POST' });
        if (result.ok) {
            showAlert('Bridge configuration reloaded', 'success');
            await loadToolsData({ silent: true });
        } else {
            showAlert('Reload failed: ' + (result.error || 'unknown error'), 'error');
        }
    } catch (e) {
        showAlert('Reload failed: ' + e.message, 'error');
    }
}

async function restartBridge() {
    if (!confirm('Restart bridge container now? Active requests may fail briefly.')) return;
    try {
        const result = await fetchJson('/api/docker/restart', { method: 'POST' });
        if (result.ok) showAlert('Bridge container restarted', 'success');
        else showAlert('Restart failed: ' + (result.error || 'unknown error'), 'error');
    } catch (e) {
        showAlert('Restart failed: ' + e.message, 'error');
    }
}

async function loadMatrixData(options = {}) {
    if (inFlight.matrix) return;
    inFlight.matrix = true;
    try {
        portsData = await fetchJson('/api/ports');
        routingData = await fetchJson('/api/routing');
        renderMatrix();
        if (!options.silent) addActivity('Routing matrix refreshed');
    } catch (e) {
        showAlert('Failed to load matrix data: ' + e.message, 'error');
    } finally {
        inFlight.matrix = false;
    }
}

function renderMatrix() {
    const outports = portsData.outports || [];
    const inports = portsData.inports || [];
    const matrix = routingData.matrix || {};

    document.getElementById('stat-outports').textContent = outports.length;
    document.getElementById('stat-inports').textContent = inports.length;
    document.getElementById('stat-connections').textContent = routingData.connection_count || 0;

    if (outports.length === 0 || inports.length === 0) {
        document.getElementById('routing-matrix').innerHTML = '<tr><td colspan="99">No ports available yet.</td></tr>';
        return;
    }

    let html = '<thead><tr><th>OutPort / InPort</th>';
    inports.forEach(inp => {
        html += `<th><span class="port-badge in">${escapeHtml(inp.port_id)}</span></th>`;
    });
    html += '</tr></thead><tbody>';

    outports.forEach(outp => {
        html += `<tr><td><span class="port-badge out">${escapeHtml(outp.port_id)}</span></td>`;
        inports.forEach(inp => {
            const cell = matrix[outp.port_id]?.[inp.port_id] || { connected: false };
            const connected = cell.connected;
            const enabled = cell.enabled !== false;

            let cellClass = 'matrix-cell';
            if (connected && enabled) cellClass += ' connected';
            else if (connected && !enabled) cellClass += ' disabled';

            html += `<td class="${cellClass}" onclick="toggleConnection(decodeURIComponent('${encArg(outp.port_id)}'), decodeURIComponent('${encArg(inp.port_id)}'), ${connected})">
                <div class="connection-dot ${connected ? 'dot-connected' : 'dot-empty'}"></div>
            </td>`;
        });
        html += '</tr>';
    });

    html += '</tbody>';
    document.getElementById('routing-matrix').innerHTML = html;
}

async function toggleConnection(source, target, isConnected) {
    try {
        if (isConnected) {
            await fetchJson('/api/routing/disconnect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source, target })
            });
            showAlert(`Disconnected ${source} -> ${target}`, 'success');
        } else {
            await fetchJson('/api/routing/connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source, target, transform: {}, description: '' })
            });
            showAlert(`Connected ${source} -> ${target}`, 'success');
        }
        await loadMatrixData({ silent: true });
        await loadConnectionsData({ silent: true });
    } catch (e) {
        showAlert('Operation failed: ' + e.message, 'error');
    }
}

async function loadConnectionsData(options = {}) {
    if (inFlight.connections) return;
    inFlight.connections = true;
    try {
        portsData = await fetchJson('/api/ports');
        connections = await fetchJson('/api/routing/connections');
        renderConnections();
        if (!options.silent) addActivity('Connections refreshed');
    } catch (e) {
        showAlert('Failed to load connections: ' + e.message, 'error');
    } finally {
        inFlight.connections = false;
    }
}

async function loadPortDebugData(options = {}) {
    if (inFlight.portDebug) return;
    inFlight.portDebug = true;
    try {
        const limit = parseInt(document.getElementById('port-debug-limit')?.value || '50', 10);
        portDebugData = await fetchJson(`/api/port-debug?limit=${Number.isFinite(limit) ? limit : 50}`);
        renderPortDebug();
        if (!options.silent) addActivity('Port debug refreshed');
    } catch (e) {
        showAlert('Failed to load port debug: ' + e.message, 'error');
    } finally {
        inFlight.portDebug = false;
    }
}

function renderPortDebug() {
    const stats = portDebugData.router_stats || {};
    const snapshot = portDebugData.snapshot || {};
    const latestOutports = snapshot.latest_outports || [];
    const latestInports = snapshot.latest_inports || [];
    const recentEvents = snapshot.recent_events || [];

    document.getElementById('port-queued').textContent = stats.queued ?? 0;
    document.getElementById('port-processed').textContent = stats.processed ?? 0;
    document.getElementById('port-routed').textContent = stats.total_routed ?? 0;
    document.getElementById('port-dropped').textContent = (stats.total_dropped ?? 0) + (stats.enqueue_dropped ?? 0);

    document.getElementById('port-debug-out-count').textContent = latestOutports.length;
    document.getElementById('port-debug-in-count').textContent = latestInports.length;
    document.getElementById('port-debug-event-count').textContent = recentEvents.length;

    const outContainer = document.getElementById('port-debug-outports');
    if (latestOutports.length === 0) {
        outContainer.innerHTML = '<p>No outport values observed yet.</p>';
    } else {
        outContainer.innerHTML = latestOutports.map(item => `
            <div class="debug-item">
                <div class="debug-item-head">
                    <span class="port-badge out">${escapeHtml(item.port_id)}</span>
                    <span class="debug-time">${escapeHtml(item.last_seen || '-')}</span>
                </div>
                <div class="debug-item-meta mono">value=${escapeHtml(item.value)} protocol=${escapeHtml(item.protocol || '-')}</div>
            </div>
        `).join('');
    }

    const inContainer = document.getElementById('port-debug-inports');
    if (latestInports.length === 0) {
        inContainer.innerHTML = '<p>No inport dispatch recorded yet.</p>';
    } else {
        inContainer.innerHTML = latestInports.map(item => `
            <div class="debug-item">
                <div class="debug-item-head">
                    <span class="port-badge in">${escapeHtml(item.port_id)}</span>
                    <span class="state-pill ${item.last_bridge_success ? 'on' : 'off'}">${item.last_bridge_success ? 'PUBLISHED' : 'FAILED'}</span>
                </div>
                <div class="debug-item-meta mono">value=${escapeHtml(item.last_bridge_value ?? '-')} transport=${escapeHtml(item.last_bridge_transport || '-')}</div>
                <div class="debug-item-sub">${escapeHtml(item.last_bridge_at || '-')}</div>
            </div>
        `).join('');
    }

    const eventsContainer = document.getElementById('port-debug-events');
    if (recentEvents.length === 0) {
        eventsContainer.innerHTML = '<p>No recent port events.</p>';
    } else {
        eventsContainer.innerHTML = recentEvents.slice().reverse().map(event => `
            <div class="event-row">
                <div class="event-top">
                    <span class="event-type">${escapeHtml(event.type)}</span>
                    <span class="debug-time">${escapeHtml(event.timestamp || '-')}</span>
                </div>
                <div class="event-body mono">${escapeHtml(formatPortEvent(event))}</div>
            </div>
        `).join('');
    }
}

function formatPortEvent(event) {
    if (!event || !event.type) return '';
    if (event.type === 'outport_value') return `${event.port_id} value=${event.value} via ${event.protocol}`;
    if (event.type === 'inport_dispatch') return `${event.port_id} value=${event.value} transport=${event.transport} success=${event.success}`;
    if (event.type === 'route_result') return `${event.source_port_id} -> ${event.target_port_id} input=${event.input_value} output=${event.output_value} success=${event.success}`;
    if (event.type === 'route_queue') return `${event.source_port_id} value=${event.value} enqueued=${event.enqueued} queue=${event.queue_size}`;
    if (event.type === 'ports_announce') return `${event.device_id} outports=${event.outports} inports=${event.inports}`;
    return JSON.stringify(event);
}

function renderConnections() {
    const container = document.getElementById('connections-list');
    const query = (document.getElementById('connections-search')?.value || '').toLowerCase().trim();

    if (!connections || connections.length === 0) {
        container.innerHTML = '<p>No connections configured.</p>';
        return;
    }

    let list = connections;
    if (query) {
        list = connections.filter(conn => {
            const haystack = `${conn.source || ''} ${conn.target || ''} ${conn.description || ''}`.toLowerCase();
            return haystack.includes(query);
        });
    }

    if (list.length === 0) {
        container.innerHTML = '<p>No matching connections.</p>';
        return;
    }

    container.innerHTML = list.map(conn => {
        const transformStr = Object.keys(conn.transform || {}).length > 0 ? JSON.stringify(conn.transform) : 'none';
        const connIdEnc = encArg(conn.id);
        const sourceEnc = encArg(conn.source);
        const targetEnc = encArg(conn.target);
        return `
            <div class="connection-item">
                <div class="connection-main">
                    <span class="state-pill ${conn.enabled ? 'on' : 'off'}">${conn.enabled ? 'ON' : 'OFF'}</span>
                    <span class="port-badge out">${escapeHtml(conn.source)}</span>
                    <span class="arrow">-&gt;</span>
                    <span class="port-badge in">${escapeHtml(conn.target)}</span>
                </div>
                <div class="connection-meta">
                    <span>Transform: ${escapeHtml(transformStr)}</span>
                    <span>${escapeHtml(conn.description || '')}</span>
                </div>
                <div class="connection-actions">
                    <button class="btn" onclick="editConnection(decodeURIComponent('${connIdEnc}'))">Edit</button>
                    <button class="btn btn-danger" onclick="quickDelete(decodeURIComponent('${connIdEnc}'), decodeURIComponent('${sourceEnc}'), decodeURIComponent('${targetEnc}'))">Delete</button>
                </div>
            </div>
        `;
    }).join('');
}

async function quickDelete(connectionId, source, target) {
    if (!confirm(`Delete connection ${source} -> ${target}?`)) return;
    try {
        await fetchJson('/api/routing/disconnect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ connection_id: connectionId })
        });
        showAlert('Connection deleted', 'success');
        loadConnectionsData({ silent: true });
    } catch (e) {
        showAlert('Delete failed: ' + e.message, 'error');
    }
}

function showAddConnectionModal() {
    document.getElementById('modal-title').textContent = 'Add Connection';
    document.getElementById('modal-connection-id').value = '';
    document.getElementById('modal-source').innerHTML = (portsData.outports || []).map(p => `<option value="${escapeHtml(p.port_id)}">${escapeHtml(p.port_id)}</option>`).join('');
    document.getElementById('modal-target').innerHTML = (portsData.inports || []).map(p => `<option value="${escapeHtml(p.port_id)}">${escapeHtml(p.port_id)}</option>`).join('');
    document.getElementById('modal-scale').value = '';
    document.getElementById('modal-offset').value = '';
    document.getElementById('modal-threshold').value = '';
    document.getElementById('modal-enabled').value = 'true';
    document.getElementById('modal-description').value = '';
    document.getElementById('modal-delete-btn').style.display = 'none';
    document.getElementById('connection-modal').classList.add('show');
}

function editConnection(connectionId) {
    const conn = connections.find(c => c.id === connectionId);
    if (!conn) return;

    document.getElementById('modal-title').textContent = 'Edit Connection';
    document.getElementById('modal-connection-id').value = connectionId;
    document.getElementById('modal-source').innerHTML = (portsData.outports || []).map(p => `<option value="${escapeHtml(p.port_id)}" ${p.port_id === conn.source ? 'selected' : ''}>${escapeHtml(p.port_id)}</option>`).join('');
    document.getElementById('modal-target').innerHTML = (portsData.inports || []).map(p => `<option value="${escapeHtml(p.port_id)}" ${p.port_id === conn.target ? 'selected' : ''}>${escapeHtml(p.port_id)}</option>`).join('');
    document.getElementById('modal-scale').value = conn.transform?.scale ?? '';
    document.getElementById('modal-offset').value = conn.transform?.offset ?? '';
    document.getElementById('modal-threshold').value = conn.transform?.threshold ?? '';
    document.getElementById('modal-enabled').value = conn.enabled !== false ? 'true' : 'false';
    document.getElementById('modal-description').value = conn.description || '';
    document.getElementById('modal-delete-btn').style.display = 'inline-flex';
    document.getElementById('connection-modal').classList.add('show');
}

function closeModal() {
    document.getElementById('connection-modal').classList.remove('show');
}

async function saveConnection() {
    const id = document.getElementById('modal-connection-id').value;
    const source = document.getElementById('modal-source').value;
    const target = document.getElementById('modal-target').value;
    const scale = document.getElementById('modal-scale').value;
    const offset = document.getElementById('modal-offset').value;
    const threshold = document.getElementById('modal-threshold').value;
    const enabled = document.getElementById('modal-enabled').value === 'true';
    const description = document.getElementById('modal-description').value;

    const transform = {};
    if (scale) transform.scale = parseFloat(scale);
    if (offset) transform.offset = parseFloat(offset);
    if (threshold) {
        transform.threshold = parseFloat(threshold);
        transform.threshold_mode = 'above';
    }

    try {
        if (id) {
            await fetchJson(`/api/routing/connection/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source, target, transform, enabled, description })
            });
            showAlert('Connection updated', 'success');
        } else {
            await fetchJson('/api/routing/connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source, target, transform, enabled, description })
            });
            showAlert('Connection created', 'success');
        }
        closeModal();
        loadConnectionsData({ silent: true });
        loadMatrixData({ silent: true });
    } catch (e) {
        showAlert('Save failed: ' + e.message, 'error');
    }
}

async function deleteConnection() {
    const id = document.getElementById('modal-connection-id').value;
    if (!id || !confirm('Delete this connection?')) return;

    try {
        await fetchJson('/api/routing/disconnect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ connection_id: id })
        });
        showAlert('Connection deleted', 'success');
        closeModal();
        loadConnectionsData({ silent: true });
    } catch (e) {
        showAlert('Delete failed: ' + e.message, 'error');
    }
}

async function loadVirtualToolsData(options = {}) {
    if (inFlight.virtual) return;
    inFlight.virtual = true;
    try {
        virtualToolsData = await fetchJson('/api/virtual-tools');
        currentDevices = await fetchJson('/api/devices');
        renderVirtualTools();
        if (!options.silent) addActivity('Virtual tools refreshed');
    } catch (e) {
        showAlert('Failed to load virtual tools: ' + e.message, 'error');
    } finally {
        inFlight.virtual = false;
    }
}

function renderVirtualTools() {
    const container = document.getElementById('virtual-tools-list');
    const tools = Object.entries(virtualToolsData || {});

    if (tools.length === 0) {
        container.innerHTML = '<p>No virtual tools configured.</p>';
        return;
    }

    container.innerHTML = tools.map(([name, def]) => {
        const bindings = def.bindings || [];
        const bindingsSummary = bindings.map(b => `${b.device_id}/${b.tool}`).join(', ') || 'No bindings';
        const nameEnc = encArg(name);

        return `
            <div class="device-card">
                <div class="device-header">
                    <div class="device-info">
                        <h3>${escapeHtml(name)}</h3>
                        <div class="device-id">${escapeHtml(def.description || 'No description')}</div>
                    </div>
                    <div class="connection-actions">
                        <button class="btn" onclick="editVirtualTool(decodeURIComponent('${nameEnc}'))">Edit</button>
                        <button class="btn btn-danger" onclick="quickDeleteVirtualTool(decodeURIComponent('${nameEnc}'))">Delete</button>
                    </div>
                </div>
                <div class="device-tools">
                    <strong>Bindings (${bindings.length})</strong>
                    <div class="connection-meta">${escapeHtml(bindingsSummary)}</div>
                </div>
            </div>
        `;
    }).join('');
}

function showAddVirtualToolModal() {
    document.getElementById('vt-modal-title').textContent = 'Add Virtual Tool';
    document.getElementById('vt-modal-name').value = '';
    document.getElementById('vt-modal-name').disabled = false;
    document.getElementById('vt-modal-description').value = '';
    document.getElementById('vt-modal-original-name').value = '';
    document.getElementById('vt-bindings-list').innerHTML = '';
    document.getElementById('vt-modal-delete-btn').style.display = 'none';
    vtBindingCounter = 0;
    document.getElementById('virtual-tool-modal').classList.add('show');
}

function editVirtualTool(name) {
    const vt = virtualToolsData[name];
    if (!vt) return;

    document.getElementById('vt-modal-title').textContent = 'Edit Virtual Tool';
    document.getElementById('vt-modal-name').value = name;
    document.getElementById('vt-modal-name').disabled = true;
    document.getElementById('vt-modal-description').value = vt.description || '';
    document.getElementById('vt-modal-original-name').value = name;
    document.getElementById('vt-modal-delete-btn').style.display = 'inline-flex';

    const bindingsContainer = document.getElementById('vt-bindings-list');
    bindingsContainer.innerHTML = '';
    vtBindingCounter = 0;
    (vt.bindings || []).forEach(binding => addVirtualToolBinding(binding.device_id, binding.tool));

    document.getElementById('virtual-tool-modal').classList.add('show');
}

function closeVirtualToolModal() {
    document.getElementById('virtual-tool-modal').classList.remove('show');
}

function addVirtualToolBinding(deviceId = '', toolName = '') {
    const container = document.getElementById('vt-bindings-list');
    const id = vtBindingCounter++;

    const deviceOptions = currentDevices
        .filter(d => d.online)
        .map(d => `<option value="${escapeHtml(d.device_id)}" ${d.device_id === deviceId ? 'selected' : ''}>${escapeHtml(d.name || d.device_id)}</option>`)
        .join('');

    const toolOptions = deviceId ? buildToolOptions(deviceId, toolName) : '<option value="">Select device first</option>';

    const bindingHtml = `
        <div class="form-row" id="vt-binding-${id}" style="margin-bottom: 10px; align-items: center;">
            <div class="form-group" style="flex: 1; margin-bottom: 0;">
                <select id="vt-binding-device-${id}" onchange="updateToolOptions(${id})">
                    <option value="">Select Device</option>
                    ${deviceOptions}
                </select>
            </div>
            <div class="form-group" style="flex: 1; margin-bottom: 0;">
                <select id="vt-binding-tool-${id}">${toolOptions}</select>
            </div>
            <button class="btn btn-danger" onclick="removeVirtualToolBinding(${id})" type="button">Delete</button>
        </div>
    `;
    container.insertAdjacentHTML('beforeend', bindingHtml);
}

function buildToolOptions(deviceId, selectedTool = '') {
    const device = currentDevices.find(d => d.device_id === deviceId);
    if (!device || !device.tools) return '<option value="">No tools</option>';

    return device.tools.map(t => `<option value="${escapeHtml(t.name)}" ${t.name === selectedTool ? 'selected' : ''}>${escapeHtml(t.name)}</option>`).join('');
}

function updateToolOptions(id) {
    const deviceId = document.getElementById(`vt-binding-device-${id}`).value;
    const toolSelect = document.getElementById(`vt-binding-tool-${id}`);
    toolSelect.innerHTML = deviceId ? buildToolOptions(deviceId) : '<option value="">Select device first</option>';
}

function removeVirtualToolBinding(id) {
    const el = document.getElementById(`vt-binding-${id}`);
    if (el) el.remove();
}

function collectBindings() {
    const bindings = [];
    document.querySelectorAll('[id^="vt-binding-"]').forEach(row => {
        const id = row.id.replace('vt-binding-', '');
        const deviceId = document.getElementById(`vt-binding-device-${id}`)?.value;
        const toolName = document.getElementById(`vt-binding-tool-${id}`)?.value;
        if (deviceId && toolName) bindings.push({ device_id: deviceId, tool: toolName });
    });
    return bindings;
}

async function saveVirtualTool() {
    const originalName = document.getElementById('vt-modal-original-name').value;
    const name = document.getElementById('vt-modal-name').value.trim();
    const description = document.getElementById('vt-modal-description').value.trim();
    const bindings = collectBindings();

    if (!name) {
        showAlert('Name is required', 'error');
        return;
    }

    try {
        const data = { name, description, bindings };
        if (originalName) {
            await fetchJson(`/api/virtual-tools/${originalName}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            showAlert('Virtual tool updated', 'success');
        } else {
            await fetchJson('/api/virtual-tools', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            showAlert('Virtual tool created', 'success');
        }
        closeVirtualToolModal();
        loadVirtualToolsData({ silent: true });
    } catch (e) {
        showAlert('Save failed: ' + e.message, 'error');
    }
}

async function deleteVirtualTool() {
    const name = document.getElementById('vt-modal-original-name').value;
    if (!name || !confirm(`Delete virtual tool "${name}"?`)) return;

    try {
        await fetchJson(`/api/virtual-tools/${name}`, { method: 'DELETE' });
        showAlert('Virtual tool deleted', 'success');
        closeVirtualToolModal();
        loadVirtualToolsData({ silent: true });
    } catch (e) {
        showAlert('Delete failed: ' + e.message, 'error');
    }
}

async function quickDeleteVirtualTool(name) {
    if (!confirm(`Delete virtual tool "${name}"?`)) return;
    try {
        await fetchJson(`/api/virtual-tools/${name}`, { method: 'DELETE' });
        showAlert('Virtual tool deleted', 'success');
        loadVirtualToolsData({ silent: true });
    } catch (e) {
        showAlert('Delete failed: ' + e.message, 'error');
    }
}

window.onbeforeunload = function (e) {
    if (projectionDirty) {
        e.preventDefault();
        e.returnValue = '';
    }
};

window.onload = async function () {
    setDirty(false);
    connectRealtime();
    await loadConnectionsData();
    addActivity('HAMPTER Manager ready');
};
