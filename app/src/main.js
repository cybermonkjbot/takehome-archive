import './style.css';

const status = document.querySelector('#data-status');
const lastRun = document.querySelector('#last-run');
const message = document.querySelector('#sample-message');
const grid = document.querySelector('#snapshot-grid');
const logsDialog = document.querySelector('#logs-dialog');
const logsTitle = document.querySelector('#logs-title');
const logsMeta = document.querySelector('#logs-meta');
const logsStatus = document.querySelector('#logs-status');
const logsOutput = document.querySelector('#logs-output');
const integer = new Intl.NumberFormat();
const timestamp = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
});

let activeLogsRequest;

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
  message.hidden = true;
  status.textContent = 'Updated';
}

async function refreshSnapshot() {
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
      message.hidden = false;
      message.textContent = 'No sample stored.';
      status.textContent = 'No sample';
    }
  } catch {
    status.textContent = 'Unavailable';
    if (grid.hidden) {
      message.hidden = false;
      message.textContent = 'Snapshot unavailable.';
    }
  } finally {
    window.setTimeout(refreshSnapshot, 60_000);
  }
}

function appendPodLogs(pod) {
  const section = document.createElement('section');
  section.className = 'pod-logs';
  const heading = document.createElement('h3');
  heading.textContent = `${pod.name} · ${pod.container}`;
  const output = document.createElement('pre');
  output.textContent = typeof pod.error === 'string'
    ? pod.error
    : pod.error
      ? 'Log unavailable.'
      : pod.logs || 'No log entries.';
  section.append(heading, output);
  return section;
}

async function openLogs(component, label) {
  activeLogsRequest?.abort();
  activeLogsRequest = new AbortController();
  logsTitle.textContent = `${label} logs`;
  logsMeta.textContent = 'Last 100 lines';
  logsStatus.textContent = 'Loading…';
  logsStatus.hidden = false;
  logsOutput.replaceChildren();
  logsOutput.hidden = true;
  if (!logsDialog.open) logsDialog.showModal();

  try {
    const response = await fetch(`/api/v1/logs/${encodeURIComponent(component)}?tail=100`, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      credentials: 'same-origin',
      signal: activeLogsRequest.signal,
    });
    if (!response.ok) throw new Error('logs unavailable');
    const result = await response.json();
    const pods = Array.isArray(result.pods) ? result.pods : [];
    if (!pods.length) {
      logsStatus.textContent = 'No pods found.';
      return;
    }

    logsOutput.replaceChildren(...pods.map(appendPodLogs));
    logsOutput.hidden = false;
    logsStatus.hidden = true;
    if (pods.some((pod) => pod.error)) {
      logsStatus.textContent = 'Some logs unavailable.';
      logsStatus.hidden = false;
    }
    if (result.truncated) logsMeta.textContent = 'Last 100 lines · output truncated';
  } catch (error) {
    if (error.name === 'AbortError') return;
    logsStatus.textContent = 'Logs unavailable.';
  }
}

document.querySelectorAll('[data-component]').forEach((button) => {
  button.addEventListener('click', () => {
    const label = button.closest('.component-row').querySelector('h2').textContent;
    openLogs(button.dataset.component, label);
  });
});

document.querySelector('#logs-close').addEventListener('click', () => logsDialog.close());
logsDialog.addEventListener('close', () => activeLogsRequest?.abort());

refreshSnapshot();
