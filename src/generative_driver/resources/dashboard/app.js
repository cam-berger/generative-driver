'use strict';
const byId = id => document.getElementById(id);
const names = ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'];
let lastLogKey = '', lastActivityKey = '', hasSnapshot = false;
function duration(seconds) {
  const n = Math.max(0, Math.floor(seconds));
  if (n < 60) return `${n}s`;
  if (n < 3600) return `${Math.floor(n / 60)}m ${String(n % 60).padStart(2, '0')}s`;
  return `${Math.floor(n / 3600)}h ${Math.floor(n % 3600 / 60)}m`;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}
function renderStages(stages) {
  byId('stages').replaceChildren(...stages.map((stage, i) => {
    const card = element('li', '', `stage ${stage.status}`);
    card.append(element('span', String(i + 1).padStart(2, '0'), 'number'),
                element('span', stage.name, 'name'), element('span', stage.status, 'state'));
    return card;
  }));
}
function unavailable(reason) {
  byId('status').textContent = 'Disconnected';
  byId('status').className = 'badge disconnected';
  byId('notice').textContent = reason + (hasSnapshot ? ' Showing the last received progress.' : '');
  byId('notice').hidden = false;
  byId('updated').textContent = 'Reconnecting…';
}
function render(data) {
  hasSnapshot = true;
  byId('status').textContent = data.stopping ? `${data.status} · stopping` : data.status;
  byId('status').className = `badge ${data.status}`;
  byId('identity').textContent = [data.run_id, data.case, data.agent.model, data.reasoning_effort].filter(Boolean).join(' · ');
  byId('elapsed').textContent = duration(data.elapsed_seconds);
  byId('accepted').textContent = `${data.accepted_count} / 6`;
  byId('repairs').textContent = String(data.repairs);
  byId('updated').textContent = 'Updated ' + new Date(data.observed_at * 1000).toLocaleTimeString();
  const warning = [data.reason, data.uncertain_effect ? 'Device effect is unresolved.' : ''].filter(Boolean).join(' ');
  byId('notice').textContent = warning;
  byId('notice').hidden = !warning;
  renderStages(data.stages);
  const logKey = data.logs.map(entry => entry.id).join(',');
  if (logKey !== lastLogKey) {
    byId('logs').replaceChildren(...data.logs.map(entry => {
      const row = element('div', '', 'log-row');
      row.append(element('span', new Date(entry.time * 1000).toLocaleTimeString(), 'log-time'),
                 element('span', entry.message, 'log-message'));
      return row;
    }));
    if (byId('follow').checked) byId('logs').scrollTop = byId('logs').scrollHeight;
    lastLogKey = logKey;
  }
  const activity = data.activity.entries;
  const activityKey = JSON.stringify(activity);
  if (activityKey !== lastActivityKey) {
    byId('activity').replaceChildren(...(activity.length ? activity.map(text => element('p', text))
      : [element('p', 'No current worker activity.', 'empty')]));
    lastActivityKey = activityKey;
  }
  byId('activity-age').textContent = data.activity.age_seconds === null ? 'Waiting'
    : duration(data.activity.age_seconds) + ' since activity';
  document.title = `${data.status} · Generative Driver`;
}
async function refresh() {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch('api/run', {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw new Error('Dashboard connection lost.');
    const data = await response.json();
    if (data.ok) render(data); else unavailable(data.reason);
  } catch (_) {
    unavailable('Dashboard connection lost. Waiting to reconnect.');
  } finally {
    clearTimeout(deadline);
    setTimeout(refresh, 1000);
  }
}
renderStages(names.map(name => ({name, status: 'pending'})));
refresh();
