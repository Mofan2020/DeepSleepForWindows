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

/* Tables are constants, not functions, so they are cut out by hand.
 * They have to be the same ones the interface uses - a copy would pass
 * this test while the real window used something else. */
function constant(pattern, name) {
  const found = source.match(pattern);
  if (!found) throw new Error(`${name} was not found in app.js`);
  return found[0];
}

const units = constant(/const CONDITION_UNITS = \{[\s\S]*?\};/, 'CONDITION_UNITS');
const groupLabels = constant(/const GROUP_LABELS = \{[\s\S]*?\};/, 'GROUP_LABELS');
const dayNames = constant(/const DAYS = \[[\s\S]*?\];/, 'DAYS');
const stateDecl = constant(/let state = \{[^\n]*\};/, 'state');

const code = [
  units,
  groupLabels,
  dayNames,
  stateDecl,
  extract('setIfIdle'),
  extract('makeOption'),
  extract('syncActionPlanOptions'),
  extract('conditionValueIn'),
  extract('conditionValue'),
  extract('formatIdle'),
  extract('reasonText'),
  extract('overviewChip'),
  extract('renderOverview'),
  extract('renderGuardCards'),
  extract('fillPlanSelect'),
  extract('fillPlanSelectByGuid'),
  extract('renderRules'),
  extract('renderGames'),
  extract('renderService'),
  extract('serviceButton'),
  extract('renderOwnPlans'),
  extract('renderGpu'),
  extract('refreshTelemetry'),
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
vm.runInContext(
  `${code}\nthis.__out = { setIfIdle, conditionValue, formatIdle, reasonText };`,
  context,
);
const { setIfIdle, conditionValue, formatIdle, reasonText } = context.__out;

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

/* ------------------------------------------------------- the "why" line */

/* The daemon writes its reasons in English because that is what its log
 * is written in.  The window is Chinese, so the known ones are
 * translated - but an unknown one has to come through as it is.  A
 * generic "后台决定" would hide a reason nobody has taught the window
 * about yet, and that is exactly the case worth seeing. */
test('a known reason is said in Chinese', () => {
  assert.strictEqual(reasonText('the user is active'), '你在用电脑，按活跃计划处理');
  assert.strictEqual(reasonText('a game is running'), '有游戏在跑，按活跃计划处理');
  assert.strictEqual(reasonText('rule: 夜间省电'), '规则「夜间省电」命中');
});

test('a reason with a number in it keeps the number', () => {
  assert.strictEqual(reasonText('timed override, 30 min left'), '你要求保持唤醒，还有 30 分钟');
  assert.strictEqual(reasonText('cpu is busy (48%)'), 'CPU 有 48%，不算空闲');
  assert.match(reasonText('idle for 900s'), /15 分/);
});

test('server mode countdown is counted in minutes', () => {
  assert.strictEqual(reasonText('server mode: locking in 120s'), '服务器模式：2 分钟后锁屏');
});

test('an unknown reason is passed through, not swallowed', () => {
  assert.strictEqual(reasonText('something new happened'), 'something new happened');
});

test('an empty reason is not an error', () => {
  assert.strictEqual(reasonText(''), '');
  assert.strictEqual(reasonText(null), '');
});

/* ---------------------------------------------------- filling in plans */

/* The action widget is built before the first refresh, so on a cold
 * start the "switch to which plan" dropdown was empty.  Filling it in
 * must not throw away a choice the user already made. */
test('the plan list is filled in when it arrives', () => {
  const select = element({ options: [], value: '' });
  elementSet();
  const fields = { ruleAction: { value: 'set_power_plan' }, ruleActionValue: select };
  const scope = vm.createContext({
    ...context,
    $: (id) => fields[id],
    state: { plans: ['平衡', '节能'], plan_guids: [], config: {} },
    document: { createElement: (tag) => element({ tag, textContent: '' }) },
  });
  // appendChild records what was added, so the options can be read back.
  select.appendChild = function (option) { this.options.push(option); };
  // ``state`` is declared with let, so it is not a property of the
  // global object - it has to be set from inside the sandbox.
  vm.runInContext(
    `${code}\nthis.__sync = syncActionPlanOptions;\nstate.plans = ['平衡', '节能'];`,
    scope,
  );

  assert.strictEqual(vm.runInContext('this.__sync()', scope), true);
  assert.deepStrictEqual(
    select.options.map((option) => option.value),
    ['平衡', '节能'],
  );
});

test('a plan list that is already right is left alone', () => {
  const select = element({ options: [{ value: '平衡' }, { value: '节能' }], value: '节能' });
  const fields = { ruleAction: { value: 'set_power_plan' }, ruleActionValue: select };
  const scope = vm.createContext({
    ...context,
    $: (id) => fields[id],
    document: { createElement: (tag) => element({ tag, textContent: '' }) },
  });
  vm.runInContext(
    `${code}\nthis.__sync = syncActionPlanOptions;\nstate.plans = ['平衡', '节能'];`,
    scope,
  );

  assert.strictEqual(vm.runInContext('this.__sync()', scope), false);
  assert.strictEqual(select.value, '节能');
});

test('nothing is filled in when the action is not a plan switch', () => {
  const select = element({ options: [], value: '' });
  const fields = { ruleAction: { value: 'lock_screen' }, ruleActionValue: select };
  const scope = vm.createContext({
    ...context,
    $: (id) => fields[id],
    document: { createElement: (tag) => element({ tag, textContent: '' }) },
  });
  vm.runInContext(
    `${code}\nthis.__sync = syncActionPlanOptions;\nstate.plans = ['平衡'];`,
    scope,
  );

  assert.strictEqual(vm.runInContext('this.__sync()', scope), false);
});

/* ------------------------------------------- the plan editor's dropdown */

/* A select that behaves like the real one for this purpose: writing
 * innerHTML clears the options, appendChild adds one. */
function editorSelect() {
  return {
    options: [],
    value: '',
    set innerHTML(value) {
      if (value === '') this.options = [];
    },
    get innerHTML() {
      return '';
    },
    appendChild(option) {
      this.options.push(option);
    },
  };
}

/* Windows lets two plans carry the same name (duplicating "平衡" does
 * it).  The editor used to put the **name** in the option value, so two
 * such plans were indistinguishable: picking the second one looked like
 * nothing happened.  The identity of a plan is its guid. */
test('two plans with the same name are told apart by their guid', () => {
  const select = editorSelect();
  const scope = vm.createContext({
    ...context,
    document: { createElement: (tag) => ({ tag, value: '', textContent: '' }) },
  });
  vm.runInContext(`${code}\nthis.__fill = fillPlanSelectByGuid;`, scope);
  Object.assign(scope, {
    __sel: select,
    __plans: ['平衡', '平衡'],
    __guids: ['guid-a', 'guid-b'],
    __want: 'guid-b',
  });
  vm.runInContext('this.__fill(__sel, __plans, __guids, __want)', scope);

  assert.deepStrictEqual(
    select.options.map((option) => option.value),
    ['guid-a', 'guid-b'],
    '选项的 value 必须是 GUID，不是名字',
  );
  assert.deepStrictEqual(
    select.options.map((option) => option.textContent),
    ['平衡', '平衡'],
    '给用户看的仍然是名字',
  );
  assert.strictEqual(select.value, 'guid-b', '选中的那个要真的被选中');
});

test('the editor dropdown is not rebuilt while the list is unchanged', () => {
  const select = editorSelect();
  select.options = [
    { value: 'guid-a', textContent: '平衡' },
    { value: 'guid-b', textContent: '节能' },
  ];
  select.value = 'guid-b';
  const scope = vm.createContext({
    ...context,
    document: { createElement: (tag) => ({ tag, value: '', textContent: '' }) },
  });
  vm.runInContext(`${code}\nthis.__fill = fillPlanSelectByGuid;`, scope);
  Object.assign(scope, {
    __sel: select,
    __plans: ['平衡', '节能'],
    __guids: ['guid-a', 'guid-b'],
    __want: 'guid-b',
  });
  vm.runInContext('this.__fill(__sel, __plans, __guids, __want)', scope);

  // refresh() runs every 5 seconds; rebuilding the list would close a
  // dropdown the user has open and move the selection.
  assert.strictEqual(select.options.length, 2);
  assert.strictEqual(select.options[0].textContent, '平衡');
  assert.strictEqual(select.value, 'guid-b');
});

/* ------------------------------------------------------------- report */


/* ---------------------------------------------- the plan editor (v0.5.4) */

/* 这一组测的是「换一个计划，面板跟着换」。
 *
 * 用户报过三次同一个毛病：下拉框里明明换成了「高性能」，上面那行还写着
 * 「正在编辑：平衡」，下面的数值也不动。根因有两条，都在这里钉住：
 *
 * 1. 换计划时先 `await save_config(...)` 再渲染——存设置一旦失败，后面
 *    那句永远不执行，面板就停在原地。
 * 2. 标题里的名字是后端给的（读不到就是空），空的时候退回「下拉框当前
 *    选中项」。一旦下拉框和面板不同步，名字就写到别的计划头上。
 */

const planChangesDecl = constant(/let planChanges = \{\};/, 'planChanges');
const lastDrawnDecl = constant(/let lastDrawnPlanGuid = '';/, 'lastDrawnPlanGuid');

const planCode = [
  groupLabels,
  stateDecl,
  planChangesDecl,
  lastDrawnDecl,
  extract('makeOption'),
  extract('planNameOfGuid'),
  extract('selectedPlanName'),
  extract('renderPlanSettings'),
  extract('selectPlanToEdit'),
].join('\n');

/* 一个够用的元素：记得住孩子、属性和文字。 */
function richElement(tag = 'div') {
  const node = {
    tagName: tag,
    className: '',
    textContent: '',
    hidden: false,
    value: '',
    dataset: {},
    children: [],
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    appendChild(child) {
      node.children.push(child);
      return child;
    },
    append(...kids) {
      kids.forEach((kid) => node.children.push(kid));
    },
    addEventListener() {},
    removeAttribute() {},
    setAttribute() {},
    querySelector: () => null,
  };
  // **innerHTML 赋值要清孩子。** 真 DOM 里 `box.innerHTML = ''` 就是把子节点
  // 全删掉；假 DOM 只存一个字符串的话，重画之后 children 里还堆着上一次的
  // 节点，断言读到的永远是第一次画的那行——测试会「通过」，读到的是旧东西。
  let html = '';
  Object.defineProperty(node, 'innerHTML', {
    get: () => html,
    set: (value) => {
      html = value;
      if (!value) node.children.length = 0;
    },
  });
  Object.defineProperty(node, 'options', {
    get() { return node.children; },
    set() {},
  });
  return node;
}

/* 下拉框：带 options 和 selectedIndex，跟真的 select 一样「设 value 就
 * 挪选中项」。假 DOM 不模拟这个的话，planNameOfGuid 永远查不到东西。 */
function planSelect(entries) {
  const select = richElement('select');
  entries.forEach(([value, name]) => {
    const option = richElement('option');
    option.value = value;
    option.textContent = name;
    select.children.push(option);
  });
  let current = entries.length ? entries[0][0] : '';
  Object.defineProperty(select, 'value', {
    get: () => current,
    set: (next) => {
      if (select.children.some((option) => option.value === next)) current = next;
    },
  });
  Object.defineProperty(select, 'selectedIndex', {
    get: () => select.children.findIndex((option) => option.value === current),
  });
  return select;
}

const PLAN_ROWS = (sleep) => ([
  { name: '关闭显示器', guid: 'g-display', group: 'video', value: sleep, unit: '分钟',
    choices: [] },
  { name: '睡眠', guid: 'g-sleep', group: 'sleep', value: sleep + 1, unit: '分钟',
    choices: [] },
]);

/* 把真正的 renderPlanSettings / selectPlanToEdit 跑起来。 */
function planScope(options) {
  const opts = options || {};
  const fields = {};
  const get = (id) => {
    if (!fields[id]) fields[id] = richElement('div');
    return fields[id];
  };
  fields.editPlanSelect = planSelect([
    ['g-balanced', '平衡'],
    ['g-high', '高性能'],
    ['g-saver', '节能'],
  ]);
  const settings = {
    'g-balanced': { ok: true, plan_name: opts.balancedName ?? '平衡', settings: PLAN_ROWS(10) },
    'g-high': { ok: true, plan_name: opts.highName ?? '高性能', settings: PLAN_ROWS(20) },
    'g-saver': { ok: true, plan_name: '', settings: PLAN_ROWS(30) },
  };
  const calls = { saved: [] };
  const scope = vm.createContext({
    $: get,
    api: () => ({
      plan_settings: (guid) => Promise.resolve(settings[guid] || { ok: false, settings: [] }),
      save_config: (payload) => {
        calls.saved.push(payload);
        if (opts.saveFails) return Promise.reject(new Error('写盘失败'));
        return Promise.resolve({ ok: true });
      },
    }),
    document: { createElement: (tag) => richElement(tag), activeElement: null },
    console,
    Array,
    Object,
    String,
    Number,
    Math,
  });
  vm.runInContext(
    `${planCode}\nthis.__render = renderPlanSettings; this.__select = selectPlanToEdit;`,
    scope,
  );
  return { scope, fields, get, calls };
}

/** 面板顶上那行「正在编辑：…」。 */
function headingOf(fields) {
  const box = fields.planSettings;
  const found = (box.children || []).find(
    (child) => child.dataset && child.dataset.planGuid,
  );
  return found ? String(found.textContent || '') : '';
}

test('换一个计划，标题就写那个计划', async () => {
  const { scope, fields } = planScope();
  await vm.runInContext('this.__render("g-high")', scope);
  assert.match(headingOf(fields), /高性能/);
  assert.doesNotMatch(headingOf(fields), /平衡/);
});

test('标题里的名字按 guid 查，不看下拉框此刻停在哪', async () => {
  // 下拉框停在「平衡」，面板画的却是「高性能」——名字不能跟着下拉框走。
  const { scope, fields, get } = planScope();
  get('editPlanSelect').value = 'g-balanced';
  await vm.runInContext('this.__render("g-high")', scope);
  assert.match(headingOf(fields), /高性能/);
});

test('后端没给名字的时候用下拉框里那个', async () => {
  const { scope, fields } = planScope({ highName: '' });
  await vm.runInContext('this.__render("g-high")', scope);
  assert.match(headingOf(fields), /高性能/);
});

test('标题带上条数和 guid 尾段，刷没刷一眼能分清', async () => {
  const { scope, fields } = planScope();
  await vm.runInContext('this.__render("g-high")', scope);
  const heading = headingOf(fields);
  assert.match(heading, /2 项/);
  assert.match(heading, /g-high/);
  await vm.runInContext('this.__render("g-saver")', scope);
  // 尾段取的是 guid 最后 6 位：g-saver 是 7 个字符，所以只剩下 -saver。
  assert.match(headingOf(fields), /saver/);
});

test('存不住「上次编辑的计划」也要把面板画出来', async () => {
  // 这是用户报的那个毛病：先存后画，存失败就把画也吞了。
  const { scope, fields, calls } = planScope({ saveFails: true });
  await vm.runInContext('this.__select("g-high")', scope);
  assert.match(headingOf(fields), /高性能/);
  // 数组是在 vm 那个 realm 里造的，原型跟这里不是同一个，比结构不比身份。
  assert.strictEqual(JSON.stringify(calls.saved), JSON.stringify([{ last_plan_guid: 'g-high' }]));
});

test('换计划时两份内容确实不一样', async () => {
  const { scope, fields } = planScope();
  await vm.runInContext('this.__render("g-high")', scope);
  const high = headingOf(fields);
  await vm.runInContext('this.__render("g-saver")', scope);
  assert.notStrictEqual(headingOf(fields), high);
});

await run();

console.log('');
console.log(`${passed}/${passed + failed} checks passed`);
if (failed > 0) process.exit(1);
