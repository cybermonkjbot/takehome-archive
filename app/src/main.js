import './style.css';

const status = document.querySelector('#data-status');
const lastRun = document.querySelector('#last-run');
const sampleMeta = document.querySelector('.sample-meta');
const message = document.querySelector('#sample-message');
const grid = document.querySelector('#snapshot-grid');
const overviewStatus = document.querySelector('#overview-status');
const collectorSampleEmpty = document.querySelector('#collector-sample-empty');
const logsDialog = document.querySelector('#logs-dialog');
const logsTitle = document.querySelector('#logs-title');
const logsMeta = document.querySelector('#logs-meta');
const logsStatus = document.querySelector('#logs-status');
const logsOutput = document.querySelector('#logs-output');
const logsAuth = document.querySelector('#logs-auth');
const logsTokenInput = document.querySelector('#logs-token');
const logsTokenFile = document.querySelector('#logs-token-file');
const logsAuthError = document.querySelector('#logs-auth-error');
const logsLock = document.querySelector('#logs-lock');

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
let logAccessToken = '';
let selectedLogTarget;

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
  container.replaceChildren();

  const copy = document.createElement('div');
  copy.className = 'state-copy';
  const heading = document.createElement('h3');
  heading.textContent = title;
  const detail = document.createElement('p');
  detail.textContent = description;
  copy.append(heading, detail);

  container.append(createStateIcon(type), copy);

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
  lastRun.textContent = Number.isNaN(sampledAt.valueOf())
    ? 'Timestamp unavailable'
    : timestamp.format(sampledAt);
  lastRun.dateTime = Number.isNaN(sampledAt.valueOf()) ? '' : sampledAt.toISOString();

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

  grid.replaceChildren(...metrics.map(([label, value]) => {
    const item = document.createElement('div');
    item.className = 'metric';
    const term = document.createElement('dt');
    term.textContent = label;
    const detail = document.createElement('dd');
    detail.textContent = value == null || value === '' ? 'Not reported' : String(value);
    item.append(term, detail);
    return item;
  }));
  grid.hidden = false;
  sampleMeta.hidden = false;
  collectorSampleEmpty.hidden = true;
  message.hidden = true;
  status.textContent = 'Updated';
}

function renderOverviewUnavailable() {
  overviewStatus.textContent = 'Unavailable · no current observation';
  document.querySelectorAll('[data-overview-id]').forEach((node) => {
    const state = node.querySelector('[data-state]');
    const count = node.querySelector('[data-count]');
    state.textContent = 'Unknown';
    state.className = 'node-state node-state-unknown';
    count.textContent = 'No current observation';
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
    count.textContent = `${integer.format(component.ready_pods)} / ${integer.format(component.observed_pods)} pods ready`;
  });

  const checkedAt = new Date(overview.checked_at);
  overviewStatus.textContent = Number.isNaN(checkedAt.valueOf())
    ? 'Live · checked just now'
    : `Live · checked ${timestamp.format(checkedAt)}`;
}

async function refreshOverview() {
  if (overviewRefreshInFlight) return;
  overviewRefreshInFlight = true;
  try {
    const response = await fetch('/api/v1/overview', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'error',
    });
    if (!response.ok) throw new Error('overview unavailable');
    displayOverview(await response.json());
  } catch {
    renderOverviewUnavailable();
  } finally {
    overviewRefreshInFlight = false;
    window.clearTimeout(overviewRefreshTimer);
    overviewRefreshTimer = window.setTimeout(refreshOverview, 30_000);
  }
}

async function refreshSnapshot() {
  if (snapshotRefreshInFlight) return;
  snapshotRefreshInFlight = true;

  if (grid.hidden) {
    status.textContent = 'Connecting';
    message.hidden = false;
    setStateMessage(message, {
      type: 'loading',
      title: 'Loading system snapshot',
      description: 'Checking for the latest collector report.',
    });
  }

  try {
    const response = await fetch('/api/v1/snapshot', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      credentials: 'same-origin',
    });
    if (!response.ok) throw new Error('snapshot unavailable');
    const result = await response.json();
    if (result.snapshot) {
      displaySnapshot(result.snapshot);
    } else {
      lastRun.textContent = '—';
      lastRun.dateTime = '';
      grid.hidden = true;
      sampleMeta.hidden = true;
      collectorSampleEmpty.hidden = false;
      message.hidden = false;
      setStateMessage(message, {
        type: 'empty',
        title: 'No snapshot yet',
        description: 'The collector has not saved a system report.',
        actionLabel: 'Check again',
        onAction: refreshSnapshot,
      });
      status.textContent = 'No sample';
    }
  } catch {
    status.textContent = 'Unavailable';
    if (grid.hidden) {
      message.hidden = false;
      setStateMessage(message, {
        type: 'error',
        title: 'Snapshot unavailable',
        description: 'The latest system snapshot could not be loaded.',
        actionLabel: 'Try again',
        onAction: refreshSnapshot,
      });
    } else {
      status.textContent = 'Update failed';
    }
  } finally {
    snapshotRefreshInFlight = false;
    window.clearTimeout(snapshotRefreshTimer);
    snapshotRefreshTimer = window.setTimeout(refreshSnapshot, 60_000);
  }
}


function appendPodLogs(pod) {
  const section = document.createElement('section');
  section.className = 'pod-logs';
  const heading = document.createElement('h3');
  heading.textContent = `${pod.name} · ${pod.container}`;
  section.append(heading);

  if (pod.error) {
    const output = document.createElement('pre');
    output.textContent = typeof pod.error === 'string' ? pod.error : 'Log unavailable.';
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

async function openLogs(component, label) {
  selectedLogTarget = { component, label };
  activeLogsRequest?.abort();
  logsTitle.textContent = `${label} logs`;
  logsMeta.textContent = 'Last 100 lines';
  logsOutput.replaceChildren();
  logsOutput.hidden = true;
  logsStatus.hidden = true;
  if (!logsDialog.open) logsDialog.showModal();

  if (!logAccessToken) {
    showLogAccessForm();
    return;
  }

  activeLogsRequest = new AbortController();
  logsAuth.hidden = true;
  logsLock.hidden = false;
  logsStatus.hidden = false;
  setStateMessage(logsStatus, {
    type: 'loading',
    title: 'Loading logs',
    description: 'Fetching the latest 100 lines.',
  });

  try {
    const response = await fetch(`/api/v1/logs/${encodeURIComponent(component)}?tail=100`, {
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${logAccessToken}`,
      },
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'error',
      signal: activeLogsRequest.signal,
    });
    if (response.status === 503) {
      let errorCode;
      try {
        errorCode = (await response.json()).error;
      } catch {
        errorCode = undefined;
      }
      setStateMessage(logsStatus, {
        type: 'error',
        title: errorCode === 'log_authentication_unavailable' ? 'Log access is not configured' : 'Logs unavailable',
        description: errorCode === 'log_authentication_unavailable'
          ? 'The API does not have its log access Secret. Reinstall the local stack to restore it.'
          : 'The log service is unavailable or cannot reach the Kubernetes API.',
      });
      return;
    }
    if (response.status === 401) {
      logAccessToken = '';
      showLogAccessForm('The shared secret was rejected. Check it and try again.');
      return;
    }
    if (!response.ok) throw new Error('logs unavailable');
    const result = await response.json();
    const pods = Array.isArray(result.pods) ? result.pods : [];
    if (!pods.length) {
      setStateMessage(logsStatus, {
        type: 'empty',
        title: 'No component pods found',
        description: 'No pods are currently reporting for this component.',
        actionLabel: 'Check again',
        onAction: () => openLogs(component, label),
      });
      return;
    }

    logsOutput.replaceChildren(...pods.map(appendPodLogs));
    logsOutput.hidden = false;
    logsStatus.hidden = true;
    if (pods.some((pod) => pod.error)) {
      logsStatus.hidden = false;
      setStateMessage(logsStatus, {
        type: 'warning',
        title: 'Some logs unavailable',
        description: 'One or more containers returned an error.',
        actionLabel: 'Try again',
        onAction: () => openLogs(component, label),
      });
    }
    if (result.truncated) logsMeta.textContent = 'Last 100 lines · output truncated';
  } catch (error) {
    if (error.name === 'AbortError') return;
    setStateMessage(logsStatus, {
      type: 'error',
      title: 'Logs unavailable',
      description: 'The latest component logs could not be loaded.',
      actionLabel: 'Try again',
      onAction: () => openLogs(component, label),
    });
  }
}

function showLogAccessForm(errorMessage = '') {
  activeLogsRequest?.abort();
  activeLogsRequest = undefined;
  logsAuth.hidden = false;
  logsLock.hidden = true;
  logsStatus.hidden = true;
  logsOutput.replaceChildren();
  logsOutput.hidden = true;
  logsTokenInput.value = '';
  logsTokenFile.value = '';
  logsAuthError.textContent = errorMessage;
  logsAuthError.hidden = !errorMessage;
  window.requestAnimationFrame(() => logsTokenInput.focus());
}

function reportAuthInputError(messageText) {
  logsAuthError.textContent = messageText;
  logsAuthError.hidden = false;
}

async function acceptLogAccessToken(candidate) {
  const token = candidate.trim();
  if (!/^[0-9a-f]{64}$/.test(token)) {
    reportAuthInputError('Enter the 64-character token from the configured secret.');
    return;
  }
  logAccessToken = token;
  logsTokenInput.value = '';
  logsTokenFile.value = '';
  logsAuthError.hidden = true;
  if (selectedLogTarget) {
    await openLogs(selectedLogTarget.component, selectedLogTarget.label);
  }
}

document.querySelectorAll('[data-component]').forEach((button) => {
  button.addEventListener('click', () => {
    const label = button.closest('.topology-node').querySelector('h2').textContent;
    openLogs(button.dataset.component, label);
  });
});

logsAuth?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const candidate = logsTokenInput.value;
  logsTokenInput.value = '';
  await acceptLogAccessToken(candidate);
});

logsTokenFile?.addEventListener('change', async () => {
  const file = logsTokenFile.files?.[0];
  if (!file) return;
  try {
    const candidate = await file.text();
    await acceptLogAccessToken(candidate);
  } catch {
    reportAuthInputError('The selected token file could not be read.');
  } finally {
    logsTokenFile.value = '';
  }
});

logsLock?.addEventListener('click', () => {
  logAccessToken = '';
  showLogAccessForm();
});

document.querySelector('#logs-close')?.addEventListener('click', () => logsDialog.close());
logsDialog?.addEventListener('close', () => {
  activeLogsRequest?.abort();
  activeLogsRequest = undefined;
  logsOutput.replaceChildren();
  logsOutput.hidden = true;
});

window.addEventListener('pagehide', () => {
  activeLogsRequest?.abort();
  logAccessToken = '';
  logsTokenInput.value = '';
  logsOutput.replaceChildren();
});

// I completed the assignment.
refreshSnapshot();
refreshOverview();
