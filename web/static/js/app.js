/**
 * Tmux Control Panel v4 - Main Application
 * 
 * Fixed Display Configuration:
 *   GUI Panel 1 -> Display :100
 *   GUI Panel 2 -> Display :101
 *   GUI Panel 3 -> Display :102
 */

const state = {
    socket: null,
    terminal: null,
    fitAddon: null,
    config: { tmuxSocket: 'control-panel', sessionPrefix: 'cp-' },
    sessions: [],
    currentSession: null,
    customCommands: {},
    inCopyMode: false,
    displays: [],
    
    // Fixed display mapping
    fixedDisplays: { 0: 100, 1: 101, 2: 102 },
    
    layout: 'terminals-only',
    guiPanels: [
        { displayNum: null, rfb: null, visible: false, detached: false, fullscreen: false },
        { displayNum: null, rfb: null, visible: false, detached: false, fullscreen: false },
        { displayNum: null, rfb: null, visible: false, detached: false, fullscreen: false }
    ],
    panelSizes: { guiContainer: 50, terminal: 50, guiRows: [33.33, 33.33, 33.33] },
    resizing: { active: false, type: null, startPos: 0, startSize: 0, index: 0 },
    dragging: { active: false, panel: null, offsetX: 0, offsetY: 0 }
};

const dom = {};

function cacheDomElements() {
    dom.statusDot = document.getElementById('statusDot');
    dom.statusText = document.getElementById('statusText');
    dom.socketInput = document.getElementById('socketInput');
    dom.prefixInput = document.getElementById('prefixInput');
    dom.sessionList = document.getElementById('sessionList');
    dom.displayList = document.getElementById('displayList');
    dom.workspace = document.getElementById('workspace');
    dom.workspaceContent = document.getElementById('workspaceContent');
    dom.guiContainer = document.getElementById('guiContainer');
    dom.mainResizer = document.getElementById('mainResizer');
    dom.terminalPanel = document.getElementById('terminalPanel');
    dom.terminalTitle = document.getElementById('terminalTitle');
    dom.terminalContainer = document.getElementById('terminalContainer');
    dom.terminalPlaceholder = document.getElementById('terminalPlaceholder');
    dom.terminalBody = document.getElementById('terminalBody');
    dom.quickCommands = document.getElementById('quickCommands');
    dom.fullscreenOverlay = document.getElementById('fullscreenOverlay');
    dom.guiPanels = [
        document.getElementById('guiPanel1'),
        document.getElementById('guiPanel2'),
        document.getElementById('guiPanel3')
    ];
    dom.guiResizers = document.querySelectorAll('.gui-resizer');
}

async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        const config = await response.json();
        state.config.tmuxSocket = config.tmux_socket;
        state.config.sessionPrefix = config.session_prefix;
        dom.socketInput.value = config.tmux_socket;
        dom.prefixInput.value = config.session_prefix;
    } catch (error) {
        console.error('Failed to load config:', error);
    }
}

async function saveConfig() {
    const tmuxSocket = dom.socketInput.value.trim();
    const sessionPrefix = dom.prefixInput.value.trim();
    if (!tmuxSocket || !sessionPrefix) return;
    try {
        await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tmux_socket: tmuxSocket, session_prefix: sessionPrefix })
        });
        state.config.tmuxSocket = tmuxSocket;
        state.config.sessionPrefix = sessionPrefix;
        await refreshSessions();
    } catch (error) {
        console.error('Failed to save config:', error);
    }
}

function connectWebSocket() {
    updateStatus('connecting');
    state.socket = io({ transports: ['websocket'], reconnection: true, reconnectionDelay: 1000, reconnectionAttempts: 10 });
    
    state.socket.on('connect', () => { updateStatus('connected'); refreshSessions(); refreshDisplays(); });
    state.socket.on('disconnect', () => updateStatus('disconnected'));
    state.socket.on('connect_error', () => updateStatus('error'));
    state.socket.on('subscribed', (data) => {
        if (state.terminal) { state.terminal.clear(); state.terminal.reset(); }
        setTimeout(() => {
            if (state.socket?.connected && state.terminal) {
                state.socket.emit('resize', { session: data.session, cols: state.terminal.cols, rows: state.terminal.rows });
            }
        }, 100);
    });
    state.socket.on('output', (data) => {
        if (state.terminal && data.session === state.currentSession) state.terminal.write(data.data);
    });
    state.socket.on('error', (data) => console.error('Server error:', data.message));
}

function updateStatus(status) {
    dom.statusDot.className = 'status-dot';
    const statusMap = {
        'connected': ['status-dot--connected', 'Connected'],
        'connecting': ['status-dot--connecting', 'Connecting...'],
        'disconnected': [null, 'Disconnected'],
        'error': [null, 'Error']
    };
    const [cls, text] = statusMap[status] || [null, status];
    if (cls) dom.statusDot.classList.add(cls);
    dom.statusText.textContent = text;
}

function initTerminal() {
    state.terminal = new Terminal({
        cursorBlink: true, cursorStyle: 'block', fontSize: 14,
        fontFamily: '"JetBrains Mono", "Fira Code", Consolas, monospace',
        scrollback: 50000, allowProposedApi: true,
        theme: {
            background: '#080a0d', foreground: '#d4dae4', cursor: '#4ecca3',
            black: '#1a1e25', red: '#ff6b6b', green: '#3dd68c', yellow: '#ff9f43',
            blue: '#4a9eff', magenta: '#a78bfa', cyan: '#22d3ee', white: '#d4dae4'
        }
    });
    
    state.fitAddon = new FitAddon.FitAddon();
    state.terminal.loadAddon(state.fitAddon);
    state.terminal.loadAddon(new WebLinksAddon.WebLinksAddon());
    
    state.terminal.attachCustomKeyEventHandler((event) => {
        if (event.ctrlKey && event.shiftKey && (event.key === 'C' || event.key === 'V')) return false;
        if (event.ctrlKey && event.key === 'c' && state.terminal.hasSelection()) return false;
        if (event.ctrlKey && event.key === 'v') return false;
        return true;
    });
    
    state.terminal.onData((data) => {
        if (state.currentSession && state.socket?.connected) {
            if (state.inCopyMode) {
                state.inCopyMode = false;
                state.socket.emit('input', { session: state.currentSession, keys: '\x1b' });
                setTimeout(() => state.socket.emit('input', { session: state.currentSession, keys: data }), 10);
            } else {
                state.socket.emit('input', { session: state.currentSession, keys: data });
            }
        }
    });
    
    state.terminal.onResize(({ cols, rows }) => {
        if (state.currentSession && state.socket?.connected) {
            state.socket.emit('resize', { session: state.currentSession, cols, rows });
        }
    });
}

function attachTerminal() {
    dom.terminalContainer.innerHTML = '';
    state.terminal.open(dom.terminalContainer);
    state.terminal.clear();
    
    dom.terminalContainer.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (!state.currentSession || !state.socket?.connected) return;
        const scrollUp = e.deltaY < 0;
        const lines = Math.max(1, Math.abs(Math.round(e.deltaY / 30)));
        if (scrollUp) {
            if (!state.inCopyMode) {
                state.socket.emit('scroll', { session: state.currentSession, command: 'enter' });
                state.inCopyMode = true;
                setTimeout(() => state.socket.emit('scroll', { session: state.currentSession, command: 'up', lines }), 50);
            } else {
                state.socket.emit('scroll', { session: state.currentSession, command: 'up', lines });
            }
        } else if (state.inCopyMode) {
            state.socket.emit('scroll', { session: state.currentSession, command: 'down', lines });
        }
    }, { passive: false, capture: true });
    
    dom.terminalContainer.addEventListener('click', () => state.terminal.focus());
    setTimeout(() => { state.fitAddon.fit(); state.terminal.focus(); }, 100);
}

async function refreshSessions() {
    try {
        const response = await fetch('/api/sessions');
        state.sessions = (await response.json()).sessions;
        renderSessionList();
        const cmdResponse = await fetch('/api/commands');
        state.customCommands = await cmdResponse.json();
        if (state.currentSession) renderQuickCommands();
    } catch (error) {
        console.error('Failed to refresh sessions:', error);
    }
}

function renderSessionList() {
    if (state.sessions.length === 0) {
        dom.sessionList.innerHTML = '<div class="empty-message">No sessions</div>';
        return;
    }
    dom.sessionList.innerHTML = state.sessions.map(session => `
        <div class="session-item ${session === state.currentSession ? 'session-item--active' : ''}" onclick="selectSession('${session}')">
            <span class="session-item__name">${session}</span>
            <div class="session-item__actions">
                <button class="btn btn--danger btn--icon" onclick="event.stopPropagation(); deleteSession('${session}')" title="Delete">×</button>
            </div>
        </div>
    `).join('');
}

async function selectSession(session) {
    if (state.currentSession && state.socket?.connected) {
        state.socket.emit('unsubscribe', { session: state.currentSession });
    }
    state.currentSession = session;
    state.inCopyMode = false;
    dom.terminalPlaceholder.style.display = 'none';
    dom.terminalContainer.style.display = 'block';
    dom.terminalTitle.textContent = session;
    attachTerminal();
    renderSessionList();
    renderQuickCommands();
    setTimeout(() => {
        state.fitAddon.fit();
        if (state.socket?.connected) {
            state.socket.emit('subscribe', { session, cols: state.terminal.cols, rows: state.terminal.rows });
        }
    }, 150);
}

async function createSession() {
    const name = document.getElementById('sessionName').value.trim();
    const cwd = document.getElementById('sessionCwd').value.trim();
    const cmd = document.getElementById('sessionCmd').value.trim();
    if (!name) { alert('Please enter a session name'); return; }
    try {
        const response = await fetch('/api/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, cwd: cwd || undefined, command: cmd || undefined })
        });
        const data = await response.json();
        if (data.status === 'ok') {
            hideModal('newSessionModal');
            document.getElementById('sessionName').value = '';
            document.getElementById('sessionCwd').value = '';
            document.getElementById('sessionCmd').value = '';
            await refreshSessions();
            selectSession(data.session);
        } else {
            alert('Failed: ' + data.message);
        }
    } catch (error) {
        console.error('Failed to create session:', error);
    }
}

async function deleteSession(session) {
    if (!confirm('Delete session "' + session + '"?')) return;
    try {
        await fetch('/api/sessions/' + session, { method: 'DELETE' });
        if (state.currentSession === session) {
            state.currentSession = null;
            dom.terminalPlaceholder.style.display = 'flex';
            dom.terminalContainer.style.display = 'none';
            dom.terminalTitle.textContent = 'No session selected';
        }
        await refreshSessions();
    } catch (error) {
        console.error('Failed to delete session:', error);
    }
}

function sendSignal(sig) {
    if (state.currentSession && state.socket?.connected) {
        state.socket.emit('signal', { session: state.currentSession, signal: sig });
    }
}

function clearTerminal() {
    if (state.currentSession && state.socket?.connected) {
        state.socket.emit('input', { session: state.currentSession, keys: '\x0c' });
    }
}

function renderQuickCommands() {
    const commands = state.customCommands[state.currentSession] || [];
    dom.quickCommands.innerHTML = '<span class="quick-commands__label">Quick:</span>' +
        commands.map((cmd, i) => 
            '<button class="btn btn--secondary btn--sm" onclick="runCommand(\'' + escapeHtml(cmd.command) + '\')">' + escapeHtml(cmd.label) + '</button>' +
            '<button class="btn btn--danger btn--sm" onclick="deleteCommand(' + i + ')">×</button>'
        ).join('') +
        '<button class="btn btn--secondary btn--sm" onclick="showModal(\'addCommandModal\')">+ Add</button>';
}

async function runCommand(command) {
    if (!state.currentSession) return;
    try {
        await fetch('/api/sessions/' + state.currentSession + '/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command })
        });
    } catch (error) {
        console.error('Failed to run command:', error);
    }
}

async function addCommand() {
    const label = document.getElementById('cmdLabel').value.trim();
    const command = document.getElementById('cmdCommand').value.trim();
    if (!label || !command || !state.currentSession) { alert('Fill all fields'); return; }
    try {
        const response = await fetch('/api/commands/' + state.currentSession, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label, command })
        });
        const data = await response.json();
        if (data.status === 'ok') {
            state.customCommands[state.currentSession] = data.commands;
            renderQuickCommands();
            hideModal('addCommandModal');
            document.getElementById('cmdLabel').value = '';
            document.getElementById('cmdCommand').value = '';
        }
    } catch (error) {
        console.error('Failed to add command:', error);
    }
}

async function deleteCommand(index) {
    if (!state.currentSession) return;
    try {
        const response = await fetch('/api/commands/' + state.currentSession + '/' + index, { method: 'DELETE' });
        const data = await response.json();
        if (data.status === 'ok') {
            state.customCommands[state.currentSession] = data.commands;
            renderQuickCommands();
        }
    } catch (error) {
        console.error('Failed to delete command:', error);
    }
}

async function refreshDisplays() {
    try {
        const response = await fetch('/api/x11/displays');
        state.displays = (await response.json()).displays || [];
        renderDisplayList();
    } catch (error) {
        console.error('Failed to refresh displays:', error);
    }
}

function renderDisplayList() {
    if (state.displays.length === 0) {
        dom.displayList.innerHTML = '<div class="empty-message">No displays running</div>';
        return;
    }
    dom.displayList.innerHTML = state.displays.map(d => 
        '<div class="display-item">' +
            '<div class="display-item__info">' +
                '<span class="display-item__label">' + d.display + ' (Panel ' + (d.panel_index + 1) + ')</span>' +
                '<span class="display-item__size">' + d.width + '×' + d.height + '</span>' +
            '</div>' +
            '<button class="btn btn--danger btn--icon" onclick="deleteDisplay(' + d.display_num + ')" title="Stop">×</button>' +
        '</div>'
    ).join('');
}

async function deleteDisplay(displayNum) {
    if (!confirm('Stop display :' + displayNum + '?')) return;
    try {
        await fetch('/api/x11/displays/' + displayNum, { method: 'DELETE' });
        state.guiPanels.forEach((panel, i) => {
            if (panel.displayNum === displayNum) disconnectGuiPanel(i);
        });
        await refreshDisplays();
    } catch (error) {
        console.error('Failed to delete display:', error);
    }
}

function setLayout(layout) {
    state.layout = layout;
    document.querySelectorAll('.layout-btn').forEach(btn => {
        btn.classList.toggle('layout-btn--active', btn.dataset.layout === layout);
    });
    const guiCount = parseInt(layout.replace('split-', '')) || 0;
    
    if (layout === 'terminals-only') {
        dom.guiContainer.style.display = 'none';
        dom.mainResizer.style.display = 'none';
        dom.terminalPanel.style.flex = '1';
        state.guiPanels.forEach(p => p.visible = false);
    } else {
        dom.terminalPanel.style.flex = '0 0 ' + (100 - state.panelSizes.guiContainer) + '%';
        dom.mainResizer.style.display = 'block';
        dom.guiContainer.style.display = 'flex';
        dom.guiContainer.style.flex = '1';
        
        for (let i = 0; i < 3; i++) {
            if (i < guiCount) {
                dom.guiPanels[i].classList.remove('gui-panel--hidden');
                dom.guiPanels[i].style.flex = '1';
                state.guiPanels[i].visible = true;
            } else {
                dom.guiPanels[i].classList.add('gui-panel--hidden');
                state.guiPanels[i].visible = false;
            }
        }
        
        document.querySelectorAll('.gui-resizer').forEach((r, i) => {
            r.style.display = i < guiCount - 1 ? 'block' : 'none';
        });
    }
    setTimeout(() => { if (state.fitAddon) state.fitAddon.fit(); }, 100);
}

function setupResizers() {
    dom.mainResizer.addEventListener('mousedown', (e) => {
        e.preventDefault();
        state.resizing = { active: true, type: 'main', startPos: e.clientX, startSize: dom.terminalPanel.getBoundingClientRect().width };
        document.body.classList.add('resizing');
        dom.mainResizer.classList.add('resizer--active');
    });
    
    document.querySelectorAll('.gui-resizer').forEach((resizer, index) => {
        resizer.addEventListener('mousedown', (e) => {
            e.preventDefault();
            state.resizing = { active: true, type: 'gui', index, startPos: e.clientY, startSize: dom.guiPanels[index].getBoundingClientRect().height };
            document.body.classList.add('resizing-v');
            resizer.classList.add('resizer--active');
        });
    });
    
    document.addEventListener('mousemove', handleResize);
    document.addEventListener('mouseup', stopResize);
}

function handleResize(e) {
    if (!state.resizing.active) return;
    if (state.resizing.type === 'main') {
        const containerWidth = dom.workspaceContent.getBoundingClientRect().width;
        const delta = e.clientX - state.resizing.startPos;
        const newWidth = state.resizing.startSize + delta;
        if (newWidth >= 300 && newWidth <= containerWidth - 250) {
            const percent = (newWidth / containerWidth) * 100;
            dom.terminalPanel.style.flex = '0 0 ' + percent + '%';
            state.panelSizes.guiContainer = 100 - percent;
        }
    } else if (state.resizing.type === 'gui') {
        const delta = e.clientY - state.resizing.startPos;
        const newHeight = state.resizing.startSize + delta;
        if (newHeight >= 100) {
            dom.guiPanels[state.resizing.index].style.flex = '0 0 ' + newHeight + 'px';
        }
    }
    if (state.fitAddon) state.fitAddon.fit();
}

function stopResize() {
    if (!state.resizing.active) return;
    state.resizing.active = false;
    document.body.classList.remove('resizing', 'resizing-v');
    document.querySelectorAll('.resizer--active').forEach(r => r.classList.remove('resizer--active'));
    setTimeout(() => { if (state.fitAddon) state.fitAddon.fit(); }, 50);
}

function toggleFullscreen(panelIndex) {
    const panel = dom.guiPanels[panelIndex];
    const panelState = state.guiPanels[panelIndex];
    if (panelState.fullscreen) {
        panel.classList.remove('gui-panel--fullscreen');
        dom.fullscreenOverlay.classList.remove('fullscreen-overlay--visible');
        panelState.fullscreen = false;
    } else {
        panel.classList.add('gui-panel--fullscreen');
        dom.fullscreenOverlay.classList.add('fullscreen-overlay--visible');
        panelState.fullscreen = true;
    }
}

function toggleDetach(panelIndex) {
    const panel = dom.guiPanels[panelIndex];
    const panelState = state.guiPanels[panelIndex];
    if (panelState.fullscreen) toggleFullscreen(panelIndex);
    
    if (panelState.detached) {
        panel.classList.remove('gui-panel--detached');
        panel.style.position = '';
        panel.style.top = '';
        panel.style.left = '';
        panel.style.width = '';
        panel.style.height = '';
        panelState.detached = false;
    } else {
        const rect = panel.getBoundingClientRect();
        panel.classList.add('gui-panel--detached');
        panel.style.position = 'fixed';
        panel.style.top = rect.top + 'px';
        panel.style.left = rect.left + 'px';
        panel.style.width = rect.width + 'px';
        panel.style.height = rect.height + 'px';
        panelState.detached = true;
        panel.querySelector('.gui-panel__header').addEventListener('mousedown', (e) => startDrag(e, panelIndex));
    }
}

function startDrag(e, panelIndex) {
    if (!state.guiPanels[panelIndex].detached || e.target.tagName === 'BUTTON') return;
    const rect = dom.guiPanels[panelIndex].getBoundingClientRect();
    state.dragging = { active: true, panel: dom.guiPanels[panelIndex], offsetX: e.clientX - rect.left, offsetY: e.clientY - rect.top };
    document.addEventListener('mousemove', handleDrag);
    document.addEventListener('mouseup', stopDrag);
}

function handleDrag(e) {
    if (!state.dragging.active) return;
    state.dragging.panel.style.left = (e.clientX - state.dragging.offsetX) + 'px';
    state.dragging.panel.style.top = (e.clientY - state.dragging.offsetY) + 'px';
}

function stopDrag() {
    state.dragging.active = false;
    document.removeEventListener('mousemove', handleDrag);
    document.removeEventListener('mouseup', stopDrag);
}

/**
 * Connect GUI panel - creates display on demand
 * Panel 0 -> :100, Panel 1 -> :101, Panel 2 -> :102
 */
async function connectGuiPanel(panelIndex) {
    const displayNum = state.fixedDisplays[panelIndex];
    const panel = dom.guiPanels[panelIndex];
    const body = panel.querySelector('.gui-panel__body');
    const titleSpan = panel.querySelector('.gui-panel__display-num');
    
    titleSpan.textContent = ':' + displayNum + ' (connecting...)';
    body.innerHTML = '<div style="color: var(--text-muted);">Creating display...</div>';
    
    try {
        // This creates the display on-demand if it doesn't exist
        const response = await fetch('/api/x11/panel/' + panelIndex + '/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ width: 1280, height: 800 })
        });
        const data = await response.json();
        
        if (data.status === 'error') {
            if (data.message.includes('Missing dependencies')) {
                alert('Install X11 packages:\nsudo apt install xvfb x11vnc websockify');
            } else {
                alert('Failed: ' + data.message);
            }
            titleSpan.textContent = 'Not connected';
            body.innerHTML = '<div class="gui-panel__placeholder"><div class="gui-panel__placeholder-icon">🖼️</div><div class="gui-panel__placeholder-text">Click "Connect" for display :' + displayNum + '</div></div>';
            return;
        }
        
        if (state.guiPanels[panelIndex].rfb) {
            state.guiPanels[panelIndex].rfb.disconnect();
            state.guiPanels[panelIndex].rfb = null;
        }
        
        state.guiPanels[panelIndex].displayNum = displayNum;
        titleSpan.textContent = ':' + displayNum;
        body.innerHTML = '<div style="color: var(--text-muted);">Connecting VNC...</div>';
        
        const RFB = (await import('/static/js/novnc/core/rfb.js')).default;
        const wsUrl = 'ws://' + window.location.hostname + ':' + data.display.ws_port;
        
        console.log('Connecting to VNC at:', wsUrl);
        body.innerHTML = '';
        const rfb = new RFB(body, wsUrl, { scaleViewport: true, resizeSession: false });
        
        rfb.addEventListener('connect', () => {
            console.log('Panel ' + (panelIndex + 1) + ' connected to :' + displayNum);
            titleSpan.textContent = ':' + displayNum + ' ✓';
        });
        rfb.addEventListener('disconnect', (e) => {
            console.log('Panel ' + (panelIndex + 1) + ' disconnected:', e.detail);
            titleSpan.textContent = ':' + displayNum + ' (disconnected)';
        });
        rfb.addEventListener('securityfailure', (e) => {
            console.error('Security failure:', e.detail);
        });
        rfb.addEventListener('credentialsrequired', (e) => {
            console.log('Credentials required:', e.detail);
        });
        
        state.guiPanels[panelIndex].rfb = rfb;
        await refreshDisplays();
    } catch (error) {
        console.error('Connection failed:', error);
        body.innerHTML = '<div style="color: var(--accent-red);">Connection failed</div>';
        titleSpan.textContent = 'Not connected';
    }
}

function disconnectGuiPanel(panelIndex) {
    const panelState = state.guiPanels[panelIndex];
    if (panelState.rfb) {
        panelState.rfb.disconnect();
        panelState.rfb = null;
    }
    panelState.displayNum = null;
    
    const panel = dom.guiPanels[panelIndex];
    if (panel) {
        const body = panel.querySelector('.gui-panel__body');
        const titleSpan = panel.querySelector('.gui-panel__display-num');
        titleSpan.textContent = 'Not connected';
        body.innerHTML = '<div class="gui-panel__placeholder"><div class="gui-panel__placeholder-icon">🖼️</div><div class="gui-panel__placeholder-text">Click "Connect" for display :' + state.fixedDisplays[panelIndex] + '</div></div>';
    }
}

// Called when clicking "Connect" button - directly connects, no modal
function showConnectDisplayModal(panelIndex) {
    connectGuiPanel(panelIndex);
}

async function bindDisplayToSession(displayNum) {
    if (!state.currentSession) { alert('Select a session first'); return; }
    if (!displayNum) { alert('Connect this panel first'); return; }
    try {
        const response = await fetch('/api/sessions/' + state.currentSession + '/bind-display', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ display_num: displayNum })
        });
        const data = await response.json();
        if (data.status === 'ok') {
            alert('DISPLAY=:' + displayNum + ' set in ' + state.currentSession);
        } else {
            alert('Failed: ' + data.message);
        }
    } catch (error) {
        console.error('Failed to bind display:', error);
    }
}

function showModal(id) {
    document.getElementById(id).classList.add('modal-overlay--visible');
    const firstInput = document.querySelector('#' + id + ' input');
    if (firstInput) setTimeout(() => firstInput.focus(), 100);
}

function hideModal(id) {
    document.getElementById(id).classList.remove('modal-overlay--visible');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function setupEventListeners() {
    dom.socketInput.addEventListener('change', saveConfig);
    dom.prefixInput.addEventListener('change', saveConfig);
    
    let resizeTimeout;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => { if (state.fitAddon) state.fitAddon.fit(); }, 100);
    });
    
    document.getElementById('sessionName').addEventListener('keydown', e => { if (e.key === 'Enter') createSession(); });
    document.getElementById('cmdCommand').addEventListener('keydown', e => { if (e.key === 'Enter') addCommand(); });
    
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('modal-overlay--visible'); });
    });
    
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            const fullscreenPanel = state.guiPanels.findIndex(p => p.fullscreen);
            if (fullscreenPanel >= 0) { toggleFullscreen(fullscreenPanel); return; }
            document.querySelectorAll('.modal-overlay').forEach(o => o.classList.remove('modal-overlay--visible'));
        }
    });
    
    document.querySelectorAll('.layout-btn').forEach(btn => {
        btn.addEventListener('click', () => setLayout(btn.dataset.layout));
    });
    
    dom.fullscreenOverlay.addEventListener('click', () => {
        const fullscreenPanel = state.guiPanels.findIndex(p => p.fullscreen);
        if (fullscreenPanel >= 0) toggleFullscreen(fullscreenPanel);
    });
}

document.addEventListener('DOMContentLoaded', () => {
    cacheDomElements();
    initTerminal();
    loadConfig();
    connectWebSocket();
    setupEventListeners();
    setupResizers();
    setLayout('terminals-only');
    
    // Update placeholder text
    dom.guiPanels.forEach((panel, i) => {
        const placeholder = panel.querySelector('.gui-panel__placeholder-text');
        if (placeholder) placeholder.textContent = 'Click "Connect" for display :' + state.fixedDisplays[i];
    });
});