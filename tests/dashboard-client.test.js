// Browser behavior contracts; no packages or network services required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/generative_driver/resources/dashboard/app.js'), 'utf8');

class Element {
  constructor() { this.children = []; this.textContent = ''; this.checked = true; this.scrollHeight = 1; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  set innerHTML(_) { throw new Error('Run data must be rendered as text'); }
}
function boot(fetch) {
  const nodes = new Map(), timers = new Map();
  let sequence = 0;
  const document = {title: '', createElement: () => new Element(), getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  }};
  vm.runInNewContext(source, {document, fetch, AbortController, Date, console,
    setTimeout(fn, delay) { const id = ++sequence; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); }});
  return {nodes, timers, trigger(predicate) {
    const entry = [...timers.entries()].find(([, timer]) => predicate(timer.delay));
    assert.ok(entry, 'Expected a bounded refresh timer');
    timers.delete(entry[0]); entry[1].fn();
  }};
}
const snapshot = (status = 'running') => ({ok: true, run_id: 'run-browser', status, stopping: false,
  case: 'tq9-v2', agent: {model: 'example-model'}, elapsed_seconds: 72, accepted_count: status === 'completed' ? 6 : 1,
  repairs: 0, observed_at: 100, reason: null, uncertain_effect: false,
  stages: ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'].map((name, i) => ({name,
    status: status === 'completed' || i === 0 ? 'accepted' : i === 1 ? 'running' : 'pending'})),
  logs: [{id: 1, time: 100, message: 'interpret: stage assigned'}], activity: {entries: [], age_seconds: null}});
const response = data => Promise.resolve({ok: true, json: () => Promise.resolve(data)});
async function flush() { for (let i = 0; i < 10; i++) await Promise.resolve(); }

(async () => {
  const text = '<script>untrusted run message</script>';
  const data = snapshot(); data.reason = text; data.logs[0].message = text;
  const view = boot(() => response(data));
  await flush();
  assert.equal(view.nodes.get('status').textContent, 'running');
  assert.equal(view.nodes.get('accepted').textContent, '1 / 6');
  assert.equal(view.nodes.get('notice').textContent, text);
  assert.equal(view.nodes.get('logs').children[0].children[1].textContent, text);

  let request = 0;
  const live = boot((_url, options) => {
    request++;
    if (request === 1) return response(snapshot());
    if (request === 2) return new Promise((_resolve, reject) => {
      if (options.signal) options.signal.addEventListener('abort', () => reject(new Error('aborted')));
    });
    return response(snapshot('completed'));
  });
  await flush();
  live.trigger(delay => delay === 1000);
  await flush();
  live.trigger(delay => delay > 1000 && delay <= 10000);
  await flush();
  assert.equal(live.nodes.get('status').textContent, 'Disconnected');
  assert.equal(live.nodes.get('accepted').textContent, '1 / 6');
  assert.match(live.nodes.get('notice').textContent, /last received progress/);
  live.trigger(delay => delay === 1000);
  await flush();
  assert.equal(live.nodes.get('status').textContent, 'completed');
  assert.equal(live.nodes.get('accepted').textContent, '6 / 6');
  console.log('Browser contracts passed: progress/text rendering and stalled-fetch recovery.');
})().catch(error => { console.error(error); process.exitCode = 1; });
