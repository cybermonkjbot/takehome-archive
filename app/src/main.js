import './style.css';

const overviewStatus = document.querySelector('#overview-status');
const overviewMessage = document.querySelector('#overview-message');
const snapshotStatus = document.querySelector('#snapshot-status');
const snapshotMessage = document.querySelector('#sample-message');
const snapshotGrid = document.querySelector('#snapshot-grid');
const collectorSampleState = document.querySelector('#collector-sample-state');
const collectorSampleDetail = document.querySelector('#collector-sample-detail');
const overviewTab = document.querySelector('#overview-tab');
const snapshotTab = document.querySelector('#snapshot-tab');
const logsTab = document.querySelector('#logs-tab');
const overviewPanel = document.querySelector('#overview-panel');
const snapshotPanel = document.querySelector('#snapshot-panel');
const logsPanel = document.querySelector('#logs-panel');
const logsForm = document.querySelector('#logs-auth');
const logsControls = document.querySelector('#logs-controls');
const logsTokenInput = document.querySelector('#logs-token');
const logsTokenFile = document.querySelector('#logs-token-file');
const logsAuthError = document.querySelector('#logs-auth-error');
const logsComponent = document.querySelector('#log-component');
const logsLock = document.querySelector('#logs-lock');
const logsRefresh = document.querySelector('#logs-refresh');
const logsStatus = document.querySelector('#logs-status');
const logsMeta = document.querySelector('#logs-meta');
const logsOutput = document.querySelector('#logs-output');

const integer = new Intl.NumberFormat();
const timestamp = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
});

let activeLogsRequest;
let snapshotRefreshTimer;
let snapshotRefreshInFlight = false;
let overviewRefreshTimer;
let overviewRefreshInFlight = false;
let hasSnapshot = false;
let hasOverview = false;
let logAccessToken = '';
let logAccessVerified = false;

const stateIconPaths = {
  loading: ['M20 12a8 8 0 1 1-2.34-5.66'],
  empty: ['M5.5 4.75h8l5 5v9.5h-13z', 'M13.5 4.75v5h5', 'M9 13h6', 'M9 16h4'],
  error: ['M12 3.5 21 19H3z', 'M12 9v4.5', 'M12 16.5h.01'],
  warning: ['M12 3.5 21 19H3z', 'M12 9v4.5', 'M12 16.5h.01'],
};

function createStateIcon(type) {
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.classList.add('state-icon');
  icon.setAttribute('viewBox', '0 0 24 24');
  icon.setAttribute('aria-hidden', 'true');
  icon.setAttribute('focusable', 'false');

  stateIconPaths[type].forEach((pathData) => {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', pathData);
    icon.append(path);
  });

  return icon;
}

function setStateMessage(container, { type, title, description, actionLabel, onAction }) {
  container.className = `state-message state-message-${type}`;
  container.setAttribute('aria-busy', String(type === 'loading'));
  container.hidden = false;
  container.replaceChildren();

  const copy = document.createElement('div');
  copy.className = 'state-copy';
  const heading = document.createElement('h3');
  heading.textContent = title;
  const detail = document.createElement('p');
  detail.textContent = description;
  copy.append(heading, detail);

  const content = document.createElement('div');
  content.className = 'state-content';
  content.append(createStateIcon(type), copy);
  container.append(content);

  if (actionLabel && onAction) {
    const action = document.createElement('button');
    action.className = 'state-action';
    action.type = 'button';
    action.textContent = actionLabel;
    action.addEventListener('click', () => {
      action.disabled = true;
      onAction();
    });
    container.append(action);
  }
}

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return 'Not reported';
  if (value < 1024 ** 2) return `${integer.format(value)} bytes`;
  const units = ['MiB', 'GiB', 'TiB'];
  let amount = value / 1024 ** 2;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toFixed(1)} ${units[unit]}`;
}

function displaySnapshot(snapshot) {
  const sampledAt = new Date(snapshot.sampled_at);
  const validSampleTime = !Number.isNaN(sampledAt.valueOf());
  const sampleLabel = validSampleTime ? timestamp.format(sampledAt) : 'time unavailable';
  snapshotStatus.textContent = `Collected ${sampleLabel}`;
  collectorSampleState.textContent = 'Sample saved';
  collectorSampleState.className = 'collector-sample-state collector-sample-state-ready';
  collectorSampleDetail.textContent = validSampleTime ? `Last report · ${sampleLabel}` : 'Latest report saved';

  const memory = snapshot.memory_limit_bytes == null
    ? `${formatBytes(snapshot.memory_current_bytes)} used · no cgroup limit reported`
    : `${formatBytes(snapshot.memory_current_bytes)} used of ${formatBytes(snapshot.memory_limit_bytes)}`;
  const metrics = [
    ['Collector pod', snapshot.pod_name],
    ['Kernel release', snapshot.kernel_release],
    ['Architecture', snapshot.architecture],
    ['Python runtime', snapshot.python_version],
    ['Logical CPUs visible', integer.format(snapshot.logical_cpu_count)],
    ['CPU quota', snapshot.cpu_quota_millicores == null ? 'No quota reported' : `${integer.format(snapshot.cpu_quota_millicores)} millicores`],
    ['Container memory', memory],
    ['1-minute load average', snapshot.load_average_1m == null ? 'Not reported' : Number(snapshot.load_average_1m).toFixed(2)],
  ];

  snapshotGrid.replaceChildren(...metrics.map(([label, value]) => {
    const item = document.createElement('div');
    item.className = 'metric';
    const term = document.createElement('dt');
    term.textContent = label;
    const detail = document.createElement('dd');
    detail.textContent = value == null || value === '' ? 'Not reported' : String(value);
    item.append(term, detail);
    return item;
  }));

  hasSnapshot = true;
  snapshotGrid.hidden = false;
  snapshotStatus.hidden = false;
  snapshotMessage.hidden = true;
}

function setCollectorEmpty() {
  hasSnapshot = false;
  snapshotStatus.hidden = true;
  collectorSampleState.textContent = 'No sample';
  collectorSampleState.className = 'collector-sample-state collector-sample-state-unknown';
  collectorSampleDetail.textContent = 'No snapshot stored yet';
  snapshotGrid.hidden = true;
}

function renderOverviewUnavailable() {
  overviewStatus.hidden = true;
  document.querySelectorAll('[data-overview-id]').forEach((node) => {
    const state = node.querySelector('[data-state]');
    const count = node.querySelector('[data-count]');
    state.textContent = 'Unknown';
    state.className = 'node-state node-state-unknown';
    count.textContent = 'No current observation';
  });
  setStateMessage(overviewMessage, {
    type: 'error',
    title: 'Live status could not be loaded',
    description: 'The last saved snapshot is still available below. Check the API connection and try again.',
    actionLabel: 'Try again',
    onAction: refreshOverview,
  });
}

function displayOverview(overview) {
  const components = Array.isArray(overview.components) ? overview.components : [];
  const byId = new Map(components.map((component) => [component.id, component]));
  document.querySelectorAll('[data-overview-id]').forEach((node) => {
    const component = byId.get(node.dataset.overviewId);
    const state = node.querySelector('[data-state]');
    const count = node.querySelector('[data-count]');
    if (!component || !['ready', 'degraded', 'missing'].includes(component.state)) {
      state.textContent = 'Unknown';
      state.className = 'node-state node-state-unknown';
      count.textContent = 'No current observation';
      return;
    }

    const stateLabels = { ready: 'Ready', degraded: 'Degraded', missing: 'Not found' };
    state.textContent = stateLabels[component.state];
    state.className = `node-state node-state-${component.state}`;
    const ready = integer.format(component.ready_pods);
    const observed = integer.format(component.observed_pods);
    count.textContent = `${ready} of ${observed} pods ready`;
  });

  const checkedAt = new Date(overview.checked_at);
  overviewStatus.textContent = Number.isNaN(checkedAt.valueOf())
    ? 'Live status checked'
    : `Checked ${timestamp.format(checkedAt)}`;
  overviewStatus.hidden = false;
  hasOverview = components.length > 0;
  overviewMessage.hidden = true;
}

async function refreshOverview() {
  if (overviewRefreshInFlight) return;
  overviewRefreshInFlight = true;
  if (!hasOverview) {
    overviewStatus.hidden = true;
    setStateMessage(overviewMessage, {
      type: 'loading',
      title: 'Checking the stack',
      description: 'Reading current pod readiness from Kubernetes.',
    });
  } else {
    overviewMessage.hidden = true;
    overviewStatus.hidden = false;
    overviewStatus.textContent = 'Refreshing…';
  }

  try {
    const response = await fetch('/api/v1/overview', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'error',
    });
    if (!response.ok) throw new Error('overview unavailable');
    const overview = await response.json();
    if (!Array.isArray(overview.components) || overview.components.length === 0) {
      throw new Error('overview has no component data');
    }
    displayOverview(overview);
  } catch {
    if (hasOverview) {
      overviewStatus.hidden = true;
      setStateMessage(overviewMessage, {
        type: 'warning',
        title: 'Could not refresh live status',
        description: 'The previous observation remains visible. Check the API connection and try again.',
        actionLabel: 'Try again',
        onAction: refreshOverview,
      });
    } else {
      renderOverviewUnavailable();
    }
  } finally {
    overviewRefreshInFlight = false;
    window.clearTimeout(overviewRefreshTimer);
    overviewRefreshTimer = window.setTimeout(refreshOverview, 30_000);
  }
}

async function refreshSnapshot() {
  if (snapshotRefreshInFlight) return;
  snapshotRefreshInFlight = true;

  if (!hasSnapshot) {
    snapshotStatus.hidden = true;
    setStateMessage(snapshotMessage, {
      type: 'loading',
      title: 'Loading the latest snapshot',
      description: 'Checking for a completed collector report.',
    });
  } else {
    snapshotMessage.hidden = true;
    snapshotStatus.hidden = false;
    snapshotStatus.textContent = 'Refreshing…';
  }

  try {
    const response = await fetch('/api/v1/snapshot', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'error',
    });
    if (!response.ok) throw new Error('snapshot unavailable');
    const result = await response.json();
    if (result.snapshot) {
      displaySnapshot(result.snapshot);
    } else {
      setCollectorEmpty();
      setStateMessage(snapshotMessage, {
        type: 'empty',
        title: 'No snapshot has been saved',
        description: 'The first completed collector run will add its measurements here. Start the local stack to run one now.',
        actionLabel: 'Check again',
        onAction: refreshSnapshot,
      });
    }
  } catch {
    snapshotStatus.hidden = true;
    setStateMessage(snapshotMessage, {
      type: hasSnapshot ? 'warning' : 'error',
      title: hasSnapshot ? 'Could not refresh the snapshot' : 'Snapshot could not be loaded',
      description: hasSnapshot
        ? 'The saved measurements remain visible. Check the API connection and try again.'
        : 'Check that the API is running, then try again.',
      actionLabel: 'Try again',
      onAction: refreshSnapshot,
    });
  } finally {
    snapshotRefreshInFlight = false;
    window.clearTimeout(snapshotRefreshTimer);
    snapshotRefreshTimer = window.setTimeout(refreshSnapshot, 60_000);
  }
}

function setActiveTab(selectedTab, shouldFocus = false) {
  const tabs = [overviewTab, snapshotTab, logsTab];
  const panels = [overviewPanel, snapshotPanel, logsPanel];
  tabs.forEach((tab, index) => {
    const selected = tab === selectedTab;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    panels[index].hidden = !selected;
  });
  if (selectedTab === snapshotTab) refreshSnapshot();
  if (shouldFocus) selectedTab.focus();
}

function handleTabKeydown(event) {
  const tabs = [overviewTab, snapshotTab, logsTab];
  const currentIndex = tabs.indexOf(event.currentTarget);
  let nextIndex;
  if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
  else if (event.key === 'ArrowLeft') nextIndex = (currentIndex + tabs.length - 1) % tabs.length;
  else if (event.key === 'Home') nextIndex = 0;
  else if (event.key === 'End') nextIndex = tabs.length - 1;
  else return;
  event.preventDefault();
  setActiveTab(tabs[nextIndex], true);
}

overviewTab.addEventListener('click', () => setActiveTab(overviewTab));
snapshotTab.addEventListener('click', () => setActiveTab(snapshotTab));
logsTab.addEventListener('click', () => setActiveTab(logsTab));
overviewTab.addEventListener('keydown', handleTabKeydown);
snapshotTab.addEventListener('keydown', handleTabKeydown);
logsTab.addEventListener('keydown', handleTabKeydown);

function appendPodLogs(pod) {
  const section = document.createElement('section');
  section.className = 'pod-logs';
  const heading = document.createElement('h3');
  heading.textContent = `${pod.name} · ${pod.container}`;
  section.append(heading);

  if (pod.error) {
    const output = document.createElement('pre');
    output.textContent = typeof pod.error === 'string' ? pod.error : 'Log unavailable.';
    output.className = 'pod-error';
    section.append(output);
    return section;
  }

  if (!pod.logs) {
    const empty = document.createElement('p');
    empty.className = 'pod-empty-state';
    empty.textContent = 'No log entries for this container.';
    section.append(empty);
    return section;
  }

  const output = document.createElement('pre');
  output.textContent = pod.logs;
  section.append(output);
  return section;
}

async function loadLogs() {
  if (!logAccessToken) {
    logsTokenInput.focus();
    return;
  }

  if (!logAccessVerified) logsControls.hidden = true;

  activeLogsRequest?.abort();
  const requestController = new AbortController();
  activeLogsRequest = requestController;
  logsOutput.replaceChildren();
  logsOutput.hidden = true;
  logsMeta.hidden = true;
  logsMeta.textContent = '';
  logsStatus.hidden = false;
  logsRefresh.hidden = true;
  logsRefresh.disabled = true;
  setStateMessage(logsStatus, {
    type: 'loading',
    title: 'Fetching output',
    description: 'Waiting for the selected component’s containers.',
  });
  try {
    const response = await fetch(`/api/v1/logs/${encodeURIComponent(logsComponent.value)}?tail=100`, {
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${logAccessToken}`,
      },
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'error',
      signal: requestController.signal,
    });
    if (activeLogsRequest !== requestController) return;

    if (response.status === 503) {
      let errorCode;
      try {
        errorCode = (await response.json()).error;
      } catch {
        errorCode = undefined;
      }
      if (activeLogsRequest !== requestController) return;
      setStateMessage(logsStatus, {
        type: 'error',
        title: errorCode === 'log_authentication_unavailable' ? 'Log access is not configured' : 'Logs are unavailable',
        description: errorCode === 'log_authentication_unavailable'
          ? 'The API does not have its log access Secret. Run the local startup script to restore it.'
          : 'The API could not read logs from Kubernetes. Check the API and cluster, then retry.',
        actionLabel: 'Try again',
        onAction: loadLogs,
      });
      return;
    }
    if (response.status === 401) {
      showLogAccessForm('That secret was rejected. Check the token file and try again.');
      return;
    }
    if (!response.ok) throw new Error('logs unavailable');

    logAccessVerified = true;
    logsControls.hidden = false;
    const result = await response.json();
    if (activeLogsRequest !== requestController) return;
    const pods = Array.isArray(result.pods) ? result.pods : [];
    if (!pods.length) {
      setStateMessage(logsStatus, {
        type: 'empty',
        title: 'No component containers found',
        description: 'There are no current pods for this component. Once it is running, load logs again.',
        actionLabel: 'Try again',
        onAction: loadLogs,
      });
      return;
    }

    logsOutput.replaceChildren(...pods.map(appendPodLogs));
    logsOutput.hidden = false;
    const failedPods = pods.filter((pod) => pod.error).length;
    logsMeta.textContent = `${integer.format(pods.length)} ${pods.length === 1 ? 'container' : 'containers'} · last 100 lines${result.truncated ? ' · output truncated' : ''}`;
    logsMeta.hidden = false;
    if (failedPods > 0) {
      setStateMessage(logsStatus, {
        type: 'warning',
        title: 'Some container logs could not be read',
        description: `${integer.format(failedPods)} of ${integer.format(pods.length)} containers returned an error. Available output is shown below.`,
        actionLabel: 'Try again',
        onAction: loadLogs,
      });
    } else {
      logsRefresh.hidden = false;
      logsStatus.hidden = true;
    }
  } catch (error) {
    if (error.name === 'AbortError' || activeLogsRequest !== requestController) return;
    setStateMessage(logsStatus, {
      type: 'error',
      title: 'Could not load container logs',
      description: 'Check the API connection and cluster access, then try again.',
      actionLabel: 'Try again',
      onAction: loadLogs,
    });
  } finally {
    if (activeLogsRequest === requestController) {
      activeLogsRequest = undefined;
      logsRefresh.disabled = false;
    }
  }
}

function showLogAccessForm(errorMessage = '') {
  activeLogsRequest?.abort();
  activeLogsRequest = undefined;
  logAccessToken = '';
  logAccessVerified = false;
  logsControls.hidden = true;
  logsForm.hidden = false;
  logsLock.hidden = true;
  logsRefresh.hidden = true;
  logsRefresh.disabled = false;
  logsOutput.replaceChildren();
  logsOutput.hidden = true;
  logsMeta.hidden = true;
  logsTokenInput.value = '';
  logsTokenFile.value = '';
  logsAuthError.textContent = errorMessage;
  logsAuthError.hidden = !errorMessage;
  logsStatus.hidden = true;
}

function reportAuthInputError(messageText) {
  logsAuthError.textContent = messageText;
  logsAuthError.hidden = false;
}

async function acceptLogAccessToken(candidate) {
  const token = candidate.trim();
  if (!/^[0-9a-f]{64}$/.test(token)) {
    reportAuthInputError('Enter the 64-character token from the local setup.');
    return;
  }
  logAccessToken = token;
  logsTokenInput.value = '';
  logsTokenFile.value = '';
  logsAuthError.hidden = true;
  logsForm.hidden = true;
  logsLock.hidden = false;
  logsRefresh.hidden = true;
  await loadLogs();
}

logsForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const candidate = logsTokenInput.value;
  logsTokenInput.value = '';
  await acceptLogAccessToken(candidate);
});

logsTokenFile.addEventListener('change', async () => {
  const file = logsTokenFile.files?.[0];
  if (!file) return;
  try {
    await acceptLogAccessToken(await file.text());
  } catch {
    reportAuthInputError('The selected token file could not be read.');
  } finally {
    logsTokenFile.value = '';
  }
});

logsComponent.addEventListener('change', () => {
  if (logAccessVerified && logAccessToken) loadLogs();
});
logsRefresh.addEventListener('click', loadLogs);
logsLock.addEventListener('click', () => {
  showLogAccessForm();
});

window.addEventListener('pagehide', () => {
  activeLogsRequest?.abort();
  logAccessToken = '';
  logsTokenInput.value = '';
  logsOutput.replaceChildren();
});

refreshSnapshot();
refreshOverview();
