/* Tests for the parts of the interface that can be broken silently.
 *
 * The code under test is taken out of app.js as it is - no mock, no
 * re-implementation - and run in a vm with just enough of a DOM to
 * work with.
 *
 * Run with:  node scripts/check-gui-js.mjs
 */

import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const assets = path.join(here, '..', 'src', 'winsleep', 'gui', 'assets');
const source = fs.readFileSync(path.join(assets, 'app.js'), 'utf8');

/* The parts of app.js that are tested here.  They are copied out of
 * the file as they are, so a change in app.js changes the test. */
function extract(name) {
  const marker = `function ${name}(`;
  const found = source.indexOf(marker);
  if (found < 0) throw new Error(`${name} was not found in app.js`);
  // An async function has to keep its keyword, or the await inside
  // it is a syntax error.
  const before = source.slice(Math.max(0, found - 6), found);
  const start = before.endsWith('async ') ? found - 6 : found;
  // Walk the braces to find the end of the function.
  let depth = 0;
  let index = source.indexOf('{', start);
  const from = index;
  for (; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    else if (source[index] === '}') {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  return source.slice(start, index + 1);
}

/* The units table is a constant, not a function, so it is cut out by
 * hand.  It has to be the same one the interface uses. */
const units = source.match(/const CONDITION_UNITS = \{[\s\S]*?\};/);
if (!units) throw new Error('CONDITION_UNITS was not found in app.js');

const code = [
  units[0],
  extract('setIfIdle'),
  extract('conditionValue'),
  extract('formatIdle'),
  extract('fillPlanSelect'),
  extract('renderRules'),
  extract('renderGames'),
  extract('renderService'),
  extract('serviceButton'),
  extract('renderOwnPlans'),
  extract('renderGpu'),
  extract('refresh'),
].join('\n\n');

/* A field is what the function works on: it has a value and a dataset.
 * The dataset is the marker the interface sets while the user types. */
function field(value, editing) {
  return {
    value,
    dataset: editing ? { editing } : {},
  };
}

const context = vm.createContext({});
vm.runInContext(`${code}\nthis.__out = { setIfIdle, conditionValue, formatIdle };`, context);
const { setIfIdle, conditionValue, formatIdle } = context.__out;

let passed = 0;
let failed = 0;

/* A test can be async, so the checks are collected first and run
 * afterwards.  That way a rejected promise counts as a failure and
 * not as a crash. */
const checks = [];

function test(name, fn) {
  checks.push({ name, fn });
}

async function run() {
  for (const { name, fn } of checks) {
    try {
      await fn();
      passed += 1;
      console.log(`ok    ${name}`);
    } catch (err) {
      failed += 1;
      console.log(`FAIL  ${name}`);
      console.log(`      ${err && err.message ? err.message : err}`);
    }
  }
}

/* ------------------------------------------------ the value being typed */

test('a field that is not being edited gets the new value', () => {
  const box = field('10');
  const changed = setIfIdle(box, 20, null);
  assert.strictEqual(box.value, 20);
  assert.strictEqual(changed, true);
});

test('a field the user is typing in is left alone', () => {
  const box = field('35');
  const changed = setIfIdle(box, 10, box);
  assert.strictEqual(box.value, '35');
  assert.strictEqual(changed, false);
});

test('a field that was edited keeps its value even when it lost the focus', () => {
  // The interface marks a field as edited while the user types.  The
  // mark is only cleared when the change is saved, so a refresh in
  // between does not throw the value away.
  const box = field('35', '1');
  const changed = setIfIdle(box, 10, null);
  assert.strictEqual(box.value, '35');
  assert.strictEqual(changed, false);
});

test('a field that is not there is not an error', () => {
  assert.strictEqual(setIfIdle(null, 20, null), false);
});

/* ------------------------------------------------------ the units */

/* The condition value is read through the ``$`` helper, so the test
 * swaps that helper out for one that returns the fields it cares
 * about. */
function conditionValueOf(kind, value) {
  const fields = {
    ruleKind: { value: kind },
    ruleValue: { value },
  };
  const scope = vm.createContext({
    ...context,
    $: (id) => fields[id],
  });
  vm.runInContext(`${code}`, scope);
  return vm.runInContext('conditionValue()', scope);
}

test('the idle time of a rule is written in seconds', () => {
  // The interface works in minutes, the rule keeps seconds.
  assert.strictEqual(conditionValueOf('idle_at_least', '10'), '600');
  assert.strictEqual(conditionValueOf('idle_below', '5'), '300');
});

test('a cpu threshold is written as it is', () => {
  assert.strictEqual(conditionValueOf('cpu_below', '15'), '15');
  assert.strictEqual(conditionValueOf('cpu_above', '80'), '80');
});

test('an empty value is no condition', () => {
  assert.strictEqual(conditionValueOf('idle_at_least', ''), '');
});

/* A field is what the interface works on.  It has a value, a text, a
 * dataset and a class - everything the code touches. */
function element(extra) {
  return Object.assign(
    {
      value: '',
      textContent: '',
      className: '',
      dataset: {},
      checked: false,
      options: [],
      innerHTML: '',
      appendChild() {},
    },
    extra || {},
  );
}

/* The refresh reads a lot of fields.  This hands out one for every
 * name, so the test does not have to know them all. */
function elementSet() {
  const fields = {};
  return {
    fields,
    get(id) {
      if (!fields[id]) fields[id] = element();
      return fields[id];
    },
  };
}

/* Run the real refresh out of app.js. */
async function runRefresh(getState) {
  const set = elementSet();
  const scope = vm.createContext({
    ...context,
    $: (id) => set.get(id),
    api: () => ({
      get_state: getState,
      service_status: () => Promise.resolve(null),
      own_plans: () => Promise.resolve({ ok: true, plans: [] }),
      gpu_state: () => Promise.resolve({ supported: false, message: 'not supported' }),
    }),
    document: {
      activeElement: null,
      createElement: () => element(),
    },
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(`${code}\nthis.__refresh = refresh;`, scope);
  try {
    await vm.runInContext('this.__refresh()', scope);
  } catch (err) {
    // The refresh has to handle a backend that is not there.  If it
    // does not, this is the failure the test is looking for.
    return set.fields;
  }
  return set.fields;
}

test('a refresh that cannot reach the backend says so', async () => {
  // The window refreshes every five seconds.  When the backend is not
  // there, the window has to say so - otherwise it sits on
  // "checking" forever and looks broken.
  const fields = await runRefresh(() => Promise.reject(new Error('no backend')));
  assert.match(fields.serviceText.textContent, /连不上后台/);
  assert.strictEqual(fields.daemonState.textContent, '连不上后台');
  assert.strictEqual(fields.statusDot.className, 'dot off');
});

test('a refresh that works shows the state', async () => {
  const fields = await runRefresh(() =>
    Promise.resolve({
      config: {
        idle_seconds: 600,
        idle_cpu_percent: 15,
        idle_plan: '节能',
        active_plan: '平衡',
        server_mode: false,
        keep_awake: false,
        rules: [],
        game_processes: [],
      },
      plans: ['节能', '平衡'],
      plan_guids: ['a', 'b'],
      active_plan: '平衡',
      active_plan_guid: 'b',
      daemon_running: true,
      idle_seconds: 42,
    }),
  );
  assert.strictEqual(fields.currentPlan.textContent, '平衡');
  assert.strictEqual(fields.daemonState.textContent, '后台服务运行中');
  assert.strictEqual(fields.statusDot.className, 'dot on');
  assert.strictEqual(fields.idleMinutes.value, 10);
  assert.strictEqual(fields.idleCpu.value, 15);
});

test('a service button that fails says what went wrong', async () => {
  // The action can fail in two ways: it can answer with a failure, or
  // it can throw.  Both have to end up on the screen, otherwise the
  // button says "working on it" and nothing else happens.
  const set = elementSet();
  const scope = vm.createContext({
    ...context,
    $: (id) => set.get(id),
    api: () => ({
      get_state: () => Promise.resolve({ config: {}, plans: [], plan_guids: [] }),
      own_plans: () => Promise.resolve({ ok: true, plans: [] }),
      gpu_state: () => Promise.resolve({ supported: false, message: 'not supported' }),
    }),
    document: { activeElement: null, createElement: () => element() },
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(`${code}\nthis.__serviceButton = serviceButton;`, scope);

  // A button that remembers its click handler.
  const created = [];
  scope.document.createElement = () => {
    const node = element({
      addEventListener(type, fn) {
        if (type === 'click') this.__onClick = fn;
      },
    });
    created.push(node);
    return node;
  };

  const button = vm.runInContext(
    'this.__serviceButton("安装并启动", () => { throw new Error("the task could not be created"); }, true)',
    scope,
  );
  await button.__onClick();

  assert.match(set.get('serviceHint').textContent, /the task could not be created/);
  assert.strictEqual(set.get('serviceHint').className, 'hint bad');
});

test('a service button that fails with a result says what went wrong', async () => {
  const set = elementSet();
  const scope = vm.createContext({
    ...context,
    $: (id) => set.get(id),
    api: () => ({
      get_state: () => Promise.resolve({ config: {}, plans: [], plan_guids: [] }),
      own_plans: () => Promise.resolve({ ok: true, plans: [] }),
      gpu_state: () => Promise.resolve({ supported: false, message: 'not supported' }),
    }),
    document: { activeElement: null, createElement: () => element() },
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(`${code}\nthis.__serviceButton = serviceButton;`, scope);
  scope.document.createElement = () =>
    element({
      addEventListener(type, fn) {
        if (type === 'click') this.__onClick = fn;
      },
    });

  const button = vm.runInContext(
    'this.__serviceButton("安装并启动", () => Promise.resolve({ ok: false, message: "schtasks failed" }), true)',
    scope,
  );
  await button.__onClick();

  assert.strictEqual(set.get('serviceHint').textContent, 'schtasks failed');
  assert.strictEqual(set.get('serviceHint').className, 'hint bad');
});

test('the idle time is shown in a readable way', () => {
  assert.strictEqual(formatIdle(30), '30 秒');
  assert.strictEqual(formatIdle(90), '1 分 30 秒');
  assert.strictEqual(formatIdle(3600), '1 小时 0 分');
  assert.strictEqual(formatIdle(-1), '--');
});

/* ------------------------------------------------------------- report */

await run();

console.log('');
console.log(`${passed}/${passed + failed} checks passed`);
if (failed > 0) process.exit(1);
