/* DeepSleep For Windows interface.
 *
 * Talks to the Python side through window.pywebview.api.  Every method
 * there returns plain JSON-friendly values, so nothing here has to know
 * anything about the daemon.
 */
'use strict';

const api = () => window.pywebview.api;

const $ = (id) => document.getElementById(id);

const CONDITION_LABELS = {
  idle_at_least: '空闲至少',
  idle_below: '空闲少于',
  cpu_below: 'CPU 低于',
  cpu_above: 'CPU 高于',
  process_running: '进程运行中',
  time_window: '时间窗口',
  day_of_week: '星期',
};

const ACTION_LABELS = {
  set_power_plan: '切换计划',
  keep_awake: '保持唤醒',
  lock_screen: '锁屏',
  run_command: '运行程序',
};

/* The subgroups Windows uses for power settings, in the words a person
 * would use.  A GUID that is not here is shown as it came back - better
 * than hiding a setting because its group was not on the list. */
/* 分组标题按 **组 GUID** 查。
 *
 * 这里以前写的是 SUB_VIDEO / SUB_SLEEP 这种符号名，而设置项带回来的
 * `row.group` 是 Windows 的组 GUID 字符串，两边永远对不上——界面上
 * 显示的就是一串裸 GUID，「莫名其妙的疑似 UUID 的内容」。
 *
 * 键必须跟 core/powersettings.py 里的 GROUP_* 常量逐字一致。 */
const GROUP_LABELS = {
  '7516b95f-f776-4464-8c53-06167f40cc99': '显示',
  '238c9fa8-0aad-41ed-83f4-97be242c8f20': '睡眠',
  '0012ee47-9041-4b5d-9b77-535fba8b1442': '硬盘',
  '54533251-82be-4824-96c1-47b60b740d00': '处理器',
  '12bbebe6-58d6-4636-95bb-3217ef867c1a': '网络',
  '2a737441-1930-4402-8d77-b2bebba308a3': 'USB',
  '19cbb8fa-5279-450e-9fac-8a3d5fede023': '无线适配器',
  '9596fb26-9850-41fd-ac3e-4c5f8b4e1a2d': '多媒体',
  '501a4d13-42af-4429-9fd1-a8218c268e20': 'PCI Express',
  'dab60367-53fe-4fbc-825e-521d069d2456': '显卡',
};

/* The unit belongs to the label.  A bare number in a rule is a number
 * of seconds, so the interface works in minutes and says so. */
const CONDITION_UNITS = {
  idle_at_least: '分钟',
  idle_below: '分钟',
  cpu_below: '%',
  cpu_above: '%',
};

let state = { plans: [], plan_guids: [], config: {} };

/* The live log view.  ``logOffset`` is the byte position in the log
 * file that was read last time, so the Python side only has to send the
 * lines that appeared since.  ``logLines`` holds what is on screen. */
let logOffset = 0;
let logLines = [];
let logTimer = null;
let logFollow = true;
const LOG_MAX_ON_SCREEN = 2000;

/* What the log page is currently showing: every line, only the warnings,
 * or only the errors.  The filter is applied to the lines already in
 * memory, so switching it does not re-read the file. */
let logLevel = 'all';
let logQuery = '';

const LOG_LEVELS = {
  warn: /\b(WARNING|WARN)\b/,
  error: /\b(ERROR|CRITICAL|Traceback|Exception)\b/,
};

function formatIdle(seconds) {
  if (seconds === null || seconds === undefined || seconds < 0) return '--';
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h} 小时 ${m} 分`;
  if (m > 0) return `${m} 分 ${s} 秒`;
  return `${s} 秒`;
}

function fillPlanSelect(select, plans, selected) {
  select.innerHTML = '';
  for (const plan of plans) {
    const option = document.createElement('option');
    option.value = plan;
    option.textContent = plan;
    if (plan === selected) option.selected = true;
    select.appendChild(option);
  }
}

function makeOption(value, label) {
  const option = document.createElement('option');
  option.value = String(value);
  option.textContent = label;
  return option;
}

/* 编辑器的计划下拉框：**value 是 GUID，显示的是名字。**
 *
 * 以前 value 直接放名字，于是：
 *
 * 1. Windows 允许两个计划重名（用户复制过「平衡」就会出现），重名的
 *    那两个在框里分不开，切过去看起来「数据没变」；
 * 2. 反过来用 `plans.indexOf(name)` 找回 GUID，重名时永远指到第一个。
 *
 * 计划的身份是 GUID，名字只是给人看的标签。 */
function fillPlanSelectByGuid(select, plans, guids, selectedGuid) {
  const list = plans || [];
  const ids = guids || [];
  const options = [];
  for (let index = 0; index < list.length; index += 1) {
    const guid = ids[index] || '';
    if (!guid) continue;
    options.push([guid, list[index]]);
  }

  // 列表没变就不重建：refresh() 每 5 秒跑一次，重建会把用户正打开着的
  // 下拉框关掉，选中项也会跳。
  const have = Array.from(select.options || []).map((option) => option.value);
  const same = have.length === options.length
    && options.every(([guid], index) => have[index] === guid);
  if (same) {
    if (selectedGuid) select.value = selectedGuid;
    return;
  }

  select.innerHTML = '';
  for (const [guid, name] of options) {
    const option = document.createElement('option');
    option.value = guid;
    option.textContent = name;
    if (guid === selectedGuid) option.selected = true;
    select.appendChild(option);
  }
  if (selectedGuid) select.value = selectedGuid;
}

/* ---------------------------------------------------------- rule form */

/* The kinds a condition can have.  One table, used both to build a new
 * row and to label a saved one, so the two cannot drift apart. */
const CONDITION_KINDS = [
  ['idle_at_least', '空闲至少'],
  ['idle_below', '空闲少于'],
  ['cpu_below', 'CPU 低于'],
  ['cpu_above', 'CPU 高于'],
  ['process_running', '进程运行中'],
  ['time_window', '时间窗口'],
  ['day_of_week', '星期'],
];

const DAYS = [
  ['mon', '一'], ['tue', '二'], ['wed', '三'], ['thu', '四'],
  ['fri', '五'], ['sat', '六'], ['sun', '日'],
];

/* The value the rule stores, from the value the user typed.
 *
 * The interface works in minutes and a rule keeps seconds, so a rule
 * written by hand stays readable and the number in the config always
 * means the same thing wherever it came from. */
function conditionValueIn(kind, raw) {
  const text = String(raw == null ? '' : raw).trim();
  if (!text) return '';
  if (CONDITION_UNITS[kind] === '分钟') {
    const minutes = parseFloat(text);
    if (!Number.isFinite(minutes)) return text;
    return String(Math.round(minutes * 60));
  }
  return text;
}

/* Read one row of the condition list.  Each row keeps its kind in
 * ``dataset`` and its widget inside itself, so several rows can be open
 * at once - the old form only ever had one. */
function readConditionRow(row) {
  const kind = row.dataset.kind || '';
  if (!kind) return null;

  if (kind === 'day_of_week') {
    const picked = [];
    for (const input of row.querySelectorAll('.day-input')) {
      if (input.checked) picked.push(input.value);
    }
    return picked.length ? { kind, value: picked.join(',') } : null;
  }

  const field = row.querySelector('.cond-input');
  if (!field) return null;
  const value = conditionValueIn(kind, field.value);
  return value ? { kind, value } : null;
}

function readConditions() {
  const out = [];
  for (const row of document.querySelectorAll('#ruleConditions .cond-row')) {
    const one = readConditionRow(row);
    if (one) out.push(one);
  }
  return out;
}

/* Build the value widget for one condition kind. */
function conditionWidget(kind, value) {
  const box = document.createElement('div');
  box.className = 'cond-value';

  if (kind === 'day_of_week') {
    const picked = new Set(String(value || '').split(',').map((s) => s.trim()));
    const row = document.createElement('div');
    row.className = 'day-row';
    for (const [key, label] of DAYS) {
      const wrap = document.createElement('label');
      wrap.className = 'day';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = key;
      input.className = 'day-input';
      input.checked = picked.has(key);
      const span = document.createElement('span');
      span.textContent = label;
      wrap.append(input, span);
      row.appendChild(wrap);
    }
    box.appendChild(row);
    return box;
  }

  const field = document.createElement('input');
  field.className = 'cond-input';

  if (CONDITION_UNITS[kind] === '分钟' || CONDITION_UNITS[kind] === '%') {
    field.className = 'cond-input num';
    field.type = 'number';
    field.min = '0';
    field.step = '1';
    if (CONDITION_UNITS[kind] === '%') field.max = '100';
    // The rule stores seconds, the field shows minutes.
    const shown = CONDITION_UNITS[kind] === '分钟' && value
      ? Math.round(parseFloat(value) / 60)
      : value;
    field.value = shown == null || shown === '' ? '' : String(shown);
    field.placeholder = CONDITION_UNITS[kind] === '%' ? '15' : '10';
  } else if (kind === 'process_running') {
    field.className = 'cond-input text';
    field.value = value || '';
    field.placeholder = 'steam.exe';
  } else {
    field.className = 'cond-input text';
    field.value = value || '';
    field.placeholder = '09:00-18:00';
  }

  box.appendChild(field);

  // A process name is picked, not typed: a typo there means the rule
  // silently never fires.
  if (kind === 'process_running') {
    const pick = document.createElement('button');
    pick.type = 'button';
    pick.className = 'btn small';
    pick.textContent = '选择';
    pick.addEventListener('click', async () => {
      const chosen = await pickProcess();
      if (chosen) field.value = chosen;
    });
    box.appendChild(pick);
  } else {
    const unit = document.createElement('span');
    unit.className = 'unit';
    unit.textContent = CONDITION_UNITS[kind] || (kind === 'time_window' ? '24 小时制' : '');
    box.appendChild(unit);
  }

  return box;
}

function addConditionRow(kind, value) {
  const list = $('ruleConditions');
  if (!list) return null;
  const row = document.createElement('div');
  row.className = 'cond-row';
  row.dataset.kind = kind || 'idle_at_least';

  const select = document.createElement('select');
  select.className = 'select cond-kind';
  for (const [key, label] of CONDITION_KINDS) {
    select.appendChild(makeOption(key, label));
  }
  select.value = row.dataset.kind;
  select.addEventListener('change', () => {
    row.dataset.kind = select.value;
    const old = row.querySelector('.cond-value');
    if (old) old.remove();
    row.insertBefore(conditionWidget(select.value, ''), remove);
  });

  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'cond-del';
  remove.textContent = '×';
  remove.title = '去掉这条条件';
  remove.addEventListener('click', () => {
    row.remove();
    if (list.children.length === 0) addConditionRow();
  });

  row.append(select, conditionWidget(row.dataset.kind, value || ''), remove);
  list.appendChild(row);
  return row;
}

/* The plan list is only known after the first refresh, but the action
 * widget is built when the window opens - so on a cold start the
 * "switch to which plan" dropdown was empty until something else made
 * the plan list arrive.  This fills in the plans without touching the
 * choice the user already made. */
function syncActionPlanOptions() {
  const select = $('ruleActionValue');
  if (!select) return false;
  const action = $('ruleAction');
  if (!action || action.value !== 'set_power_plan') return false;

  const wanted = state.plans || [];
  if (wanted.length === 0) return false;

  const have = [];
  for (const option of select.options || []) have.push(option.value);
  if (have.length === wanted.length && wanted.every((plan) => have.includes(plan))) {
    return false;
  }

  const chosen = select.value;
  select.innerHTML = '';
  for (const plan of wanted) {
    select.appendChild(makeOption(plan, plan));
  }
  if (chosen && wanted.includes(chosen)) select.value = chosen;
  return true;
}

/* What the old single-value form read.  The multi-row form uses
 * ``readConditionRow``; this stays because the same unit conversion has
 * to apply to both, and there is one test that pins the conversion. */
function conditionValue() {
  const kind = $('ruleKind').value;

  if (kind === 'day_of_week') {
    const picked = [];
    for (const input of document.querySelectorAll('.day-input')) {
      if (input.checked) picked.push(input.value);
    }
    return picked.join(',');
  }

  const box = $('ruleValue');
  if (!box) return '';
  return conditionValueIn(kind, box.value);
}

function buildActionValue() {
  const area = $('ruleActionArea');
  area.innerHTML = '';
  const kind = $('ruleAction').value;

  if (kind === 'set_power_plan') {
    const select = document.createElement('select');
    select.className = 'select';
    select.id = 'ruleActionValue';
    for (const plan of state.plans || []) {
      select.appendChild(makeOption(plan, plan));
    }
    area.appendChild(select);
    syncActionPlanOptions();
    return;
  }

  if (kind === 'keep_awake') {
    const select = document.createElement('select');
    select.className = 'select';
    select.id = 'ruleActionValue';
    select.appendChild(makeOption('on', '保持唤醒'));
    select.appendChild(makeOption('off', '允许休眠'));
    area.appendChild(select);
    return;
  }

  if (kind === 'lock_screen') {
    const note = document.createElement('span');
    note.className = 'unit';
    note.textContent = '不需要参数';
    area.appendChild(note);
    return;
  }

  const text = document.createElement('input');
  text.className = 'text';
  text.id = 'ruleActionValue';
  text.placeholder = 'C:\\Tools\\thing.exe';
  const pick = document.createElement('button');
  pick.type = 'button';
  pick.className = 'btn';
  pick.textContent = '选文件';
  pick.addEventListener('click', async () => {
    const result = await api().pick_file();
    if (result && result.ok) text.value = result.path;
  });
  area.append(text, pick);
}

function describeRule(rule) {
  const conditions = (rule.conditions || [])
    .map((c) => {
      const label = CONDITION_LABELS[c.kind] || c.kind;
      if (c.kind === 'idle_at_least' || c.kind === 'idle_below') {
        const seconds = parseFloat(c.value);
        const shown = Number.isFinite(seconds) ? `${seconds / 60} 分钟` : c.value;
        return `${label} ${shown}`;
      }
      if (c.kind === 'cpu_below' || c.kind === 'cpu_above') {
        return `${label} ${c.value}%`;
      }
      if (c.kind === 'day_of_week') {
        const names = String(c.value).split(',').map((key) => {
          const found = DAYS.find(([day]) => day === key.trim());
          return found ? '周' + found[1] : key.trim();
        });
        return names.join('、');
      }
      return `${label} ${c.value}`;
    })
    .join(' 且 ');
  const action = `${ACTION_LABELS[rule.action.kind] || rule.action.kind} ${rule.action.value || ''}`.trim();
  return { conditions: conditions || '无条件', action };
}

function renderRules(rules) {
  const list = $('ruleList');
  list.innerHTML = '';
  if (!rules || rules.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '还没有规则。';
    list.appendChild(empty);
    return;
  }

  for (const rule of rules) {
    const text = describeRule(rule);

    const row = document.createElement('div');
    row.className = 'rule' + (rule.enabled ? '' : ' disabled');

    const toggle = document.createElement('input');
    toggle.type = 'checkbox';
    toggle.checked = !!rule.enabled;
    toggle.title = '启用 / 停用';
    toggle.addEventListener('change', async () => {
      await api().toggle_rule(rule.name, toggle.checked);
    });

    const name = document.createElement('span');
    name.className = 'rule-name';
    name.textContent = rule.name;

    const cond = document.createElement('span');
    cond.className = 'rule-cond';
    cond.textContent = text.conditions;

    const action = document.createElement('span');
    action.className = 'rule-action';
    action.textContent = text.action;

    const remove = document.createElement('button');
    remove.className = 'btn ghost small';
    remove.textContent = '删除';
    remove.addEventListener('click', async () => {
      await api().remove_rule(rule.name);
      await refresh();
    });

    // The priority is what decides the order when two rules fire at the
    // same time.  Hiding it would make that order look arbitrary.
    if (rule.priority) {
      const pri = document.createElement('span');
      pri.className = 'rule-pri';
      pri.textContent = 'P' + rule.priority;
      pri.title = '优先级 ' + rule.priority + '，数字大的先执行';
      row.append(toggle, name, cond, action, pri, remove);
      list.appendChild(row);
      continue;
    }

    row.append(toggle, name, cond, action, remove);
    list.appendChild(row);
  }
}

/* ------------------------------------------------------- game processes */

function renderGames(games) {
  const list = $('gameList');
  list.innerHTML = '';
  if (!games || games.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '还没有添加游戏进程。';
    list.appendChild(empty);
    return;
  }

  for (const game of games) {
    const chip = document.createElement('span');
    chip.className = 'chip';

    const label = document.createElement('span');
    label.textContent = game;
    chip.appendChild(label);

    const remove = document.createElement('button');
    remove.className = 'chip-x';
    remove.textContent = '×';
    remove.title = '移除';
    remove.addEventListener('click', async () => {
      const next = (state.config.game_processes || []).filter((name) => name !== game);
      await api().save_config({ game_processes: next });
      await refresh();
    });

    chip.appendChild(remove);
    list.appendChild(chip);
  }
}

/* ------------------------------------------------------- the plan editor */

let planChanges = {};

async function renderPlanSettings(guid) {
  const box = $('planSettings');
  const bar = $('planSaveBar');
  box.innerHTML = '';
  planChanges = {};

  if (!guid) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = '选一个计划。';
    box.appendChild(empty);
    bar.hidden = true;
    return;
  }

  const result = await api().plan_settings(guid);
  const rows = (result && result.settings) || [];
  if (rows.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = (result && result.message) || '这个计划没有可改的设置。';
    box.appendChild(empty);
    bar.hidden = true;
    return;
  }

  // The settings come back in one flat list.  Eleven numbers in a row
  // give no clue which ones belong together, so they are grouped by the
  // subgroup Windows itself uses.
  let currentGroup = null;
  let groupBox = null;

  for (const row of rows) {
    if (row.group !== currentGroup) {
      currentGroup = row.group;
      groupBox = document.createElement('div');
      groupBox.className = 'setting-group';
      const head = document.createElement('div');
      head.className = 'setting-group-head';
      // **认不出的组别不要显示 GUID。** 用户看不懂，而且会让整个
      // 面板看起来像坏了——分组名缺失是程序的事，不是让用户去读 GUID。
      head.textContent = GROUP_LABELS[row.group] || '其他设置';
      groupBox.appendChild(head);
      box.appendChild(groupBox);
    }

    const line = document.createElement('div');
    line.className = 'setting';

    const label = document.createElement('label');
    label.className = 'setting-label';
    label.textContent = row.name;

    let picker;
    const hasChoices = row.choices && row.choices.length > 0;
    if (!hasChoices) {
      picker = document.createElement('input');
      picker.className = 'num';
      picker.type = 'number';
      picker.min = '0';
      picker.value = row.value;
    } else {
      picker = document.createElement('select');
      picker.className = 'select';
      for (const choice of row.choices) {
        picker.appendChild(makeOption(choice.value, choice.label));
      }
      picker.value = String(row.value);
    }

    picker.addEventListener('change', () => {
      const value = parseInt(picker.value, 10);
      if (!Number.isFinite(value)) return;
      planChanges[row.guid] = { group: row.group, guid: row.guid, value };
      $('planSaveHint').textContent = '有改动，还没保存';
      $('planSaveHint').className = 'hint';
    });

    // The dropdown labels already say "10 分钟", so a unit next to it
    // would read "10 分钟 分钟".  The number fields have no labels of
    // their own and do need it.
    const unit = document.createElement('span');
    unit.className = 'unit';
    unit.textContent = hasChoices ? '' : (row.unit || '');

    line.append(label, picker, unit);
    groupBox.appendChild(line);
  }

  bar.hidden = Object.keys(planChanges).length === 0;
}

/* ----------------------------------------------------- the process picker */

let processPickerResolve = null;

function pickProcess() {
  return new Promise((resolve) => {
    processPickerResolve = resolve;
    $('processSheet').style.display = 'flex';
    $('processSearch').value = '';
    $('processList').innerHTML = '<p class="empty">正在读进程列表…</p>';
    loadProcesses('');
  });
}

function closeProcessSheet(result) {
  $('processSheet').style.display = 'none';
  if (processPickerResolve) {
    processPickerResolve(result);
    processPickerResolve = null;
  }
}

async function loadProcesses(wanted) {
  const list = $('processList');
  const result = await api().list_processes(wanted || '');
  const rows = (result && result.processes) || [];
  list.innerHTML = '';

  if (rows.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = (result && result.message) || '没有找到进程。';
    list.appendChild(empty);
    return;
  }

  for (const row of rows) {
    const item = document.createElement('button');
    item.className = 'proc';
    item.type = 'button';

    const name = document.createElement('span');
    name.className = 'proc-name';
    name.textContent = row.name;

    const exe = document.createElement('span');
    exe.className = 'proc-exe';
    exe.textContent = row.exe || '';

    item.append(name, exe);
    item.addEventListener('click', (event) => {
      event.stopPropagation();
      closeProcessSheet(row.name);
    });
    list.appendChild(item);
  }
}

/* ---------------------------------------------------------- the service */

function serviceButton(label, action, primary) {
  const button = document.createElement('button');
  button.className = primary ? 'btn primary' : 'btn';
  button.textContent = label;
  button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = '处理中…';
    let result = null;
    try {
      result = await action();
    } catch (err) {
      // A failure that is not a result - the window would keep saying
      // "working on it" and never come back.
      result = { ok: false, message: '出错了：' + (err && err.message ? err.message : err) };
    }
    button.disabled = false;
    if (result && result.message) {
      $('serviceHint').textContent = result.message;
      $('serviceHint').className = result.ok ? 'hint ok' : 'hint bad';
    }
    await refresh();
  });
  return button;
}

/* The state decides what the buttons do.  A service that is not
 * running is not a dead end: there is always something to press.
 *
 * A working service gets one quiet line.  It used to get a full panel
 * with a heading, two paragraphs and a button, which is a lot of the
 * window spent on something that needs no attention. */
function renderService(service) {
  const actions = $('serviceActions');
  const text = $('serviceText');
  const hint = $('serviceHint');
  const dot = $('serviceDot');
  if (actions) actions.innerHTML = '';

  if (!service) {
    if (text) text.textContent = '正在检查后台服务…';
    if (dot) dot.className = 'dot small';
    return;
  }

  if (!service.installed) {
    if (dot) dot.className = 'dot small warn';
    text.textContent = '后台服务没有安装。装上之后开机自己起来。';
    hint.textContent = '';
    actions.appendChild(
      serviceButton('安装并启动', () => api().install_service(), true)
    );
    return;
  }

  /* The tasks hold the path of the program.  When the program was
   * moved, the task still points at the old place and every start
   * fails.  Say that instead of leaving the user pressing a button
   * that cannot work. */
  if (service.stale) {
    if (dot) dot.className = 'dot small warn';
    text.textContent = '后台服务的任务还指着程序原来的位置。';
    hint.textContent = '程序被移动过了，重装一次任务就好。';
    actions.appendChild(
      serviceButton('重新安装任务', () => api().install_service(), true)
    );
    return;
  }

  if (!service.running) {
    if (dot) dot.className = 'dot small warn';
    text.textContent = '后台服务已安装，但没有在运行。';
    hint.textContent = '现在没人看着空闲时间，规则不会触发。';
    actions.appendChild(
      serviceButton('启动', () => api().start_service(), true)
    );
    return;
  }

  if (dot) dot.className = 'dot small on';
  text.textContent = service.agent_running
    ? '后台服务运行中'
    : '后台服务运行中，但读空闲时间的那一半没在跑';

  /* The icon lives in the notification area, which Windows 11 hides
   * behind a small arrow by default.  Saying whether it is really
   * there is the only useful answer to "where is it?". */
  const tray = [];
  if (service.tray_installed) {
    tray.push(service.tray_running ? '托盘图标已显示' : '托盘图标没有起来');
  } else {
    tray.push('托盘图标没有装上');
  }
  if (!service.agent_running) {
    tray.push('读不到真实空闲时间，会一直按「你还在」处理');
  }
  hint.textContent = tray.join(' · ');

  actions.appendChild(
    serviceButton('停止', () => api().stop_service(), false)
  );
}

/* ------------------------------------------------------ 微软遥测 */

/* Read how much of the telemetry is turned off and draw it.
 *
 * The switch is set from what the machine says, not from what was
 * asked for: a switch that shows the wish instead of the result is
 * worse than no switch. */
async function refreshTelemetry() {
  let answer;
  try {
    answer = await api().telemetry_state();
  } catch (err) {
    $('telemetryValue').textContent = '读不到';
    $('telemetryHint').textContent = String(err);
    return;
  }

  if (!answer || !answer.ok) {
    $('telemetryValue').textContent = '读不到';
    $('telemetryHint').textContent = (answer && answer.message) || '';
    return;
  }

  const status = answer.status || {};
  const services = status.services || {};
  const names = Object.keys(services);

  // The telemetry counts as off when the policy says so and most of
  // the services are disabled.  Anything less is partial, and the
  // label says which.
  const policy = status.registry && status.registry.AllowTelemetry;
  const policyOff = policy === 0;
  const disabled = names.filter((name) => services[name].start === 'disabled');
  const tasksOff = (status.tasks_disabled || 0) > 0;
  const hostsOff = !!status.hosts_blocked;

  const fullyOff = policyOff && disabled.length >= Math.ceil(names.length / 2);

  $('telemetryToggle').checked = fullyOff;
  $('telemetryValue').textContent = fullyOff
    ? '已关闭'
    : policyOff || disabled.length || hostsOff || tasksOff
      ? '部分关闭'
      : '未关闭';

  const parts = [];
  parts.push(policyOff ? '策略已禁止' : `策略：${policy === undefined ? '未设置' : policy}`);
  parts.push(`服务已禁用 ${disabled.length}/${names.length}`);
  parts.push(hostsOff ? `已屏蔽 ${status.hosts_blocked_count || 0} 个域名` : '域名未屏蔽');
  parts.push(`计划任务已禁用 ${status.tasks_disabled || 0}/${status.tasks_present || 0}`);
  $('telemetryHint').textContent = parts.join(' · ');

  const detail = $('telemetryDetail');
  if (!detail) return;
  detail.innerHTML = '';
  for (const name of names) {
    const row = document.createElement('div');
    row.className = 'setting-row';
    const label = document.createElement('span');
    label.className = 'setting-label';
    label.textContent = name;
    const value = document.createElement('span');
    value.className = 'setting-value';
    value.textContent = `${services[name].state} / ${services[name].start}`;
    row.append(label, value);
    detail.appendChild(row);
  }
}

/* ---------------------------------------------------------- the overview */

/* Why the machine is in the state it is in.
 *
 * The daemon answers with a short English sentence because that is what
 * its log is written in.  The window is Chinese, so the known ones are
 * translated here.  An unknown reason is shown as it arrived - a new
 * reason in the daemon should appear in the window, not disappear
 * behind a generic "后台决定".
 */
function reasonText(reason) {
  const raw = String(reason || '').trim();
  if (!raw) return '';

  let match = /^timed override, (\d+) min left$/.exec(raw);
  if (match) return `你要求保持唤醒，还有 ${match[1]} 分钟`;

  match = /^server mode: locking in (\d+)s$/.exec(raw);
  if (match) {
    const left = parseInt(match[1], 10);
    return `服务器模式：${Math.max(0, Math.round(left / 60))} 分钟后锁屏`;
  }

  match = /^cpu is busy \((\d+)%\)$/.exec(raw);
  if (match) return `CPU 有 ${match[1]}%，不算空闲`;

  match = /^idle for (\d+)s$/.exec(raw);
  if (match) {
    const seconds = parseInt(match[1], 10);
    return `已经离开 ${formatIdle(seconds)}，按空闲计划处理`;
  }

  match = /^rule: (.+)$/.exec(raw);
  if (match) return `规则「${match[1]}」命中`;

  const known = {
    'a game is running': '有游戏在跑，按活跃计划处理',
    'the user is active': '你在用电脑，按活跃计划处理',
    'waiting for the agent': '读空闲时间的那一半没有回报，先按你还在处理',
    'server mode': '服务器模式开着，机器不会睡',
    'server mode: locking the screen': '服务器模式：正在锁屏',
    'server mode: the screen is locked': '服务器模式：屏幕已经锁了',
  };
  return known[raw] || raw;
}

function overviewChip(text, kind) {
  const chip = document.createElement('span');
  chip.className = 'pill' + (kind ? ' ' + kind : '');
  chip.textContent = text;
  return chip;
}

function renderOverview(data) {
  const config = data.config || {};
  const now = data.server_now || 0;

  const plan = $('heroPlan');
  if (plan) plan.textContent = data.active_plan || '未知';

  // --- why -------------------------------------------------------------
  const why = $('heroWhy');
  const intent = data.intent || null;
  if (why) {
    if (!data.daemon_running) {
      why.textContent = '后台服务没在运行，所以现在没人做决定。';
    } else if (!intent) {
      why.textContent = '后台刚起来，还没做过决定。';
    } else {
      why.textContent = reasonText(intent.reason) || '后台没有说明原因。';
    }
  }

  const chips = $('heroChips');
  if (chips) {
    chips.innerHTML = '';
    if (config.server_mode) chips.appendChild(overviewChip('服务器模式', 'accent'));
    if (data.override_remaining > 0) {
      chips.appendChild(overviewChip('临时保持唤醒 ' + formatIdle(data.override_remaining), 'accent'));
    } else if (config.keep_awake) {
      chips.appendChild(overviewChip('一直保持唤醒', 'on'));
    }
    if (data.override_remaining > 0 || config.keep_awake || config.server_mode) {
      chips.appendChild(overviewChip('不会自动睡', 'on'));
    }
    if (intent && intent.lock_in !== null && intent.lock_in !== undefined) {
      chips.appendChild(overviewChip('锁屏倒计时 ' + formatIdle(intent.lock_in), 'warn'));
    }
    if (intent && !intent.agent_fresh) {
      chips.appendChild(overviewChip('空闲时间不可信', 'bad'));
    }
  }

  // --- the idle track --------------------------------------------------
  const idleSeconds = data.idle_seconds;
  const threshold = config.idle_seconds ?? 600;
  const idle = $('heroIdle');
  if (idle) idle.textContent = formatIdle(idleSeconds);

  const thresholdLabel = $('heroThreshold');
  if (thresholdLabel) {
    thresholdLabel.textContent = `阈值 ${Math.round(threshold / 60)} 分钟`;
  }

  // The bar is only drawn when the threshold is a real number: a
  // percentage of zero would be a divide by zero dressed up as progress.
  const bar = $('idleBar');
  if (bar && bar.style && threshold > 0 && idleSeconds >= 0) {
    const pct = Math.min(100, (idleSeconds / threshold) * 100);
    bar.style.width = pct.toFixed(1) + '%';
    bar.style.background = pct >= 100
      ? 'linear-gradient(90deg, #4ade80, #5eead4)'
      : '';
  }

  // --- the override ----------------------------------------------------
  const state = $('overrideState');
  if (state) {
    const left = data.override_remaining || 0;
    state.textContent = left > 0
      ? `还有 ${formatIdle(left)}`
      : '未设置';
    state.className = left > 0 ? 'hint ok' : 'hint';
  }

  // --- rule count ------------------------------------------------------
  const rules = config.rules || [];
  const live = rules.filter((rule) => rule.enabled).length;
  const ovRules = $('ovRules');
  if (ovRules) ovRules.textContent = `${rules.length} 条`;
  const ovRulesHint = $('ovRulesHint');
  if (ovRulesHint) {
    ovRulesHint.textContent = rules.length === 0
      ? '还没有规则，自动化页可以加。'
      : `${live} 条在用，${rules.length - live} 条停用。`;
  }
}

/* The two small status cards on the overview.  They say the same thing
 * as the strip at the top, at a glance, because "is the thing that
 * watches my keyboard actually running" is the question a sleep tool
 * lives or dies by. */
function renderGuardCards(service) {
  const guard = $('ovGuard');
  const guardHint = $('ovGuardHint');
  const tray = $('ovTray');
  const trayHint = $('ovTrayHint');

  if (!service) {
    if (guard) guard.textContent = '未知';
    if (guardHint) guardHint.textContent = '还读不到后台服务的状态。';
    if (tray) tray.textContent = '未知';
    if (trayHint) trayHint.textContent = '';
    return;
  }

  if (!service.installed) {
    if (guard) guard.textContent = '未安装';
    if (guardHint) guardHint.textContent = '装上之后自动启动，不用每次手动开。';
  } else if (service.stale) {
    if (guard) guard.textContent = '要重装';
    if (guardHint) guardHint.textContent = '程序被移动过，任务还指着旧位置。';
  } else if (!service.running) {
    if (guard) guard.textContent = '没在运行';
    if (guardHint) guardHint.textContent = '现在没人看着空闲时间。';
  } else if (!service.agent_running) {
    if (guard) guard.textContent = '半开';
    if (guardHint) guardHint.textContent = '读不到真实空闲时间，会一直当作你在用。';
  } else {
    if (guard) guard.textContent = '正常';
    if (guardHint) guardHint.textContent = '正在读键盘和鼠标，空闲到点会自己切计划。';
  }

  if (!service.tray_installed) {
    if (tray) tray.textContent = '没有';
    if (trayHint) trayHint.textContent = '托盘图标没装上，只能从这个窗口操作。';
  } else if (!service.tray_running) {
    if (tray) tray.textContent = '没起来';
    if (trayHint) trayHint.textContent = '装上了但这次没运行，重启程序试试。';
  } else {
    if (tray) tray.textContent = '已显示';
    if (trayHint) trayHint.textContent = 'Windows 11 默认把它收在箭头里，拖出来就能一直看到。';
  }
}

/* ------------------------------------------------------------- refresh */

async function refresh() {
  let data;
  try {
    data = await api().get_state();
  } catch (err) {
    // The window has to say something when the bridge is not there.
    // Without this it would sit on "checking" forever.
    const text = $('serviceText');
    if (text) {
      text.textContent = '连不上后台：' + (err && err.message ? err.message : err);
      $('serviceHint').textContent = '';
    }
    $('daemonState').textContent = '连不上后台';
    const dot = $('statusDot');
    if (dot) dot.className = 'dot off';
    return;
  }
  state = data;
  const config = data.config || {};

  $('currentPlan').textContent = data.active_plan || '未知';
  $('activePlan').textContent = data.active_plan || '未知';

  $('idleInfo').textContent = '空闲 ' + formatIdle(data.idle_seconds);
  renderOverview(data);

  const dot = $('statusDot');
  if (!data.daemon_running) {
    dot.className = 'dot off';
    $('daemonState').textContent = '后台服务未运行';
  } else {
    dot.className = 'dot on';
    $('daemonState').textContent = '后台服务运行中';
  }

  const plans = data.plans || [];
  fillPlanSelect($('planSelect'), plans, data.active_plan);
  fillPlanSelect($('idlePlanSelect'), plans, config.idle_plan);
  fillPlanSelect($('activePlanSelect'), plans, config.active_plan);

  $('idlePlanValue').textContent = config.idle_plan || '未设置';
  $('activePlanValue').textContent = config.active_plan || '未设置';

  // The config keeps seconds, the interface works in minutes.  A field
  // the user is typing in is left alone: overwriting it every five
  // seconds would throw the value away before it is saved.
  const active = document.activeElement;
  setIfIdle($('idleMinutes'), Math.round((config.idle_seconds ?? 600) / 60), active);
  setIfIdle($('idleCpu'), config.idle_cpu_percent ?? 15, active);
  setIfIdle($('serverLockMinutes'), Math.round((config.server_lock_seconds ?? 300) / 60), active);
  setIfIdle($('logMaxLines'), config.log_max_lines ?? 0, active);
  setIfIdle($('logMaxDays'), config.log_max_age_days ?? 0, active);
  $('cpuValue').textContent = `低于 ${config.idle_cpu_percent ?? 15}%`;

  $('serverToggle').checked = !!config.server_mode;
  $('serverValue').textContent = config.server_mode ? '开启' : '关闭';
  $('keepAwakeToggle').checked = !!config.keep_awake;
  $('keepAwakeValue').textContent = config.keep_awake ? '开启' : '关闭';

  refreshTelemetry();

  renderOwnPlans();
  renderGpu();
  renderRules(config.rules || []);
  renderGames(config.game_processes || []);
  syncActionPlanOptions();

  const editSelect = $('editPlanSelect');
  // 编辑器选中的是 GUID：先保住用户当前选的那个，没有才退回「上次用的」
  // 和「正在生效的」。
  const wantedGuid = editSelect.value
    || config.last_plan_guid
    || data.active_plan_guid
    || '';
  fillPlanSelectByGuid(editSelect, plans, data.plan_guids || [], wantedGuid);

  let service = null;
  try {
    service = await api().service_status();
  } catch (err) {
    service = null;
  }
  renderService(service);
  renderGuardCards(service);
}

/* Save one of the number fields.  Each one is a different setting, so
 * they are saved separately. */
function saveNumberField(id) {
  const field = $(id);
  const value = parseFloat(field.value);
  if (!Number.isFinite(value) || value < 0) return;

  if (id === 'idleMinutes') {
    if (value <= 0) return;
    api().save_config({ idle_seconds: Math.round(value * 60) });
    return;
  }
  if (id === 'idleCpu') {
    api().save_config({ idle_cpu_percent: value });
    return;
  }
  if (id === 'serverLockMinutes') {
    if (value <= 0) return;
    api().save_config({ server_lock_seconds: Math.round(value * 60) });
  }
}

/* Write a value into a field, unless the user is working in it.

 * This is the rule that keeps a value the user typed from being
 * thrown away: the interface refreshes every five seconds, and an
 * unconditional write would put the old value back before the user
 * had finished typing.
 */
function setIfIdle(field, value, active) {
  if (!field) return false;
  if (field === active) return false;
  if (field.dataset && field.dataset.editing === '1') return false;
  field.value = value;
  return true;
}

/* --------------------------------------------------------- quick actions */

function switchTab(name) {
  const tab = document.querySelector('.tab[data-tab="' + name + '"]');
  if (tab) tab.click();
}

/* The timed "do not sleep" buttons.  Each one writes an end time into
 * the config, so the promise survives closing this window. */
function wireOverride() {
  const bar = $('overrideBar');
  if (!bar) return;

  for (const button of bar.querySelectorAll('button[data-minutes]')) {
    button.addEventListener('click', async () => {
      const minutes = parseInt(button.dataset.minutes, 10) || 0;
      const state = $('overrideState');
      if (state) {
        state.textContent = '设置中…';
        state.className = 'hint';
      }
      let result = null;
      try {
        result = await api().set_override(minutes);
      } catch (err) {
        result = { ok: false, message: String(err) };
      }
      if (state) {
        state.textContent = result && result.ok
          ? (result.message || (minutes ? '已设置' : '已取消'))
          : ((result && result.message) || '没设置成功');
        state.className = result && result.ok ? 'hint ok' : 'hint bad';
      }
      await refresh();
    });
  }
}

function wireLogTools() {
  const search = $('logSearch');
  if (search) {
    search.addEventListener('input', () => {
      logQuery = search.value.trim();
      applyLogFilter();
    });
  }

  const filter = $('logFilter');
  if (filter) {
    for (const item of filter.querySelectorAll('.seg-item')) {
      item.addEventListener('click', () => {
        logLevel = item.dataset.level || 'all';
        for (const other of filter.querySelectorAll('.seg-item')) {
          other.classList.toggle('active', other === item);
        }
        applyLogFilter();
      });
    }
  }

  const more = $('ovMoreLog');
  if (more) more.addEventListener('click', () => switchTab('logs'));
}

function wireShortcuts() {
  document.addEventListener('keydown', (event) => {
    // Escape closes the process picker.  It has a backdrop now, but a
    // dialog that cannot be dismissed from the keyboard is one people
    // get stuck in.
    if (event.key === 'Escape') {
      const sheet = $('processSheet');
      if (sheet && sheet.style && sheet.style.display !== 'none') {
        closeProcessSheet(null);
        event.preventDefault();
      }
      return;
    }

    if (!event.ctrlKey || event.altKey || event.metaKey) return;
    const order = ['overview', 'power', 'rules', 'system', 'logs'];
    const index = parseInt(event.key, 10) - 1;
    if (index >= 0 && index < order.length) {
      switchTab(order[index]);
      event.preventDefault();
    }
  });
}

/* ---------------------------------------------------------------- wire */

function wire() {
  for (const tab of document.querySelectorAll('.tab')) {
    tab.addEventListener('click', () => {
      for (const other of document.querySelectorAll('.tab')) {
        other.classList.toggle('active', other === tab);
      }
      for (const page of document.querySelectorAll('.page')) {
        page.hidden = page.id !== `page-${tab.dataset.tab}`;
      }
      // The log is only read while its page is open.  Nobody is
      // looking at it otherwise, so there is no reason to keep asking
      // for it.
      if (tab.dataset.tab === 'logs') {
        startLogWatch();
      } else {
        stopLogWatch();
      }
    });
  }

  $('refreshBtn').addEventListener('click', refresh);

  // The number fields save as soon as the value changes, so a value
  // that is typed but never confirmed is not lost.
  for (const id of ['idleMinutes', 'idleCpu', 'serverLockMinutes']) {
    const field = $(id);
    field.addEventListener('input', () => {
      field.dataset.editing = '1';
      saveNumberField(id);
    });
    field.addEventListener('change', async () => {
      await saveNumberField(id);
      field.dataset.editing = '0';
      await refresh();
    });
    field.addEventListener('blur', () => {
      field.dataset.editing = '0';
    });
  }

  $('switchBtn').addEventListener('click', async () => {
    const plan = $('planSelect').value;
    const result = await api().switch_plan(plan);
    if (!result.ok) {
      $('currentPlan').textContent = result.message || '切换失败';
    }
    await refresh();
  });

  $('idlePlanSelect').addEventListener('change', async () => {
    await api().save_config({ idle_plan: $('idlePlanSelect').value });
    $('idlePlanValue').textContent = $('idlePlanSelect').value;
  });

  $('activePlanSelect').addEventListener('change', async () => {
    await api().save_config({ active_plan: $('activePlanSelect').value });
    $('activePlanValue').textContent = $('activePlanSelect').value;
  });

  $('serverToggle').addEventListener('change', async (event) => {
    await api().set_server_mode(event.target.checked);
    $('serverValue').textContent = event.target.checked ? '开启' : '关闭';
  });

  $('keepAwakeToggle').addEventListener('change', async (event) => {
    await api().set_keep_awake(event.target.checked);
  });

  $('telemetryToggle').addEventListener('change', async (event) => {
    const wanted = event.target.checked;
    const hint = $('telemetryHint');
    event.target.disabled = true;
    hint.textContent = wanted ? '正在关闭遥测…（要管理员权限）' : '正在恢复遥测…';
    let answer = null;
    try {
      answer = await api().set_telemetry_disabled(wanted);
    } catch (err) {
      answer = { ok: false, message: String(err) };
    }
    event.target.disabled = false;
    if (answer && !answer.ok) {
      hint.textContent = '没成功：' + (answer.message || '');
    }
    // Read the state back either way: the machine decides what the
    // switch shows, not the click.
    await refreshTelemetry();
  });

  $('lockBtn').addEventListener('click', async () => {
    await api().lock_screen();
  });

  $('scheduleBtn').addEventListener('click', async () => {
    const clock = $('sleepClock').value;
    if (!clock) {
      $('scheduleValue').textContent = '先选个时间';
      return;
    }
    const result = await api().schedule_sleep(clock);
    $('scheduleValue').textContent = result.ok
      ? `将在 ${clock} 睡觉`
      : (result.message || '设置失败');
  });

  $('cancelScheduleBtn').addEventListener('click', async () => {
    const result = await api().cancel_schedule();
    $('scheduleValue').textContent = result.ok
      ? '未设置'
      : (result.message || '取消失败');
  });

  $('uninstallBtn').addEventListener('click', async () => {
    const result = await api().uninstall_service();
    $('serviceHint').textContent = result.message || '';
    $('serviceHint').className = result.ok ? 'hint ok' : 'hint bad';
    await refresh();
  });

  $('updateBtn').addEventListener('click', async () => {
    const button = $('updateBtn');
    const hint = $('updateHint');
    button.disabled = true;
    hint.textContent = '正在检查…';
    hint.className = 'hint';
    try {
      const result = await api().check_update();
      hint.textContent = result.message || '';
      hint.className = result.ok ? 'hint ok' : 'hint bad';
    } catch (err) {
      hint.textContent = '检查失败：' + (err && err.message ? err.message : err);
      hint.className = 'hint bad';
    }
    button.disabled = false;
  });

  $('openLogBtn').addEventListener('click', async () => {
    await reloadLog();
  });

  $('logFollowToggle').addEventListener('change', (event) => {
    logFollow = event.target.checked;
    if (logFollow) {
      const view = $('logView');
      view.scrollTop = view.scrollHeight;
    }
  });

  $('clearLogBtn').addEventListener('click', async () => {
    if (!window.confirm('确定要清空日志吗？清空后无法恢复。')) return;
    const result = await api().clear_log();
    $('logHint').textContent = result.message || (result.ok ? '已清空' : '清除失败');
    $('logHint').className = result.ok ? 'hint ok' : 'hint bad';
    logOffset = 0;
    logLines = [];
    $('logView').textContent = '';
  });

  $('applyLogLimitsBtn').addEventListener('click', async () => {
    const lines = parseInt($('logMaxLines').value, 10) || 0;
    const days = parseFloat($('logMaxDays').value) || 0;
    await api().save_config({ log_max_lines: lines, log_max_age_days: days });
    const result = await api().trim_log(lines, days);
    $('logHint').textContent = result.ok
      ? `清理完成，删掉 ${result.removed} 行`
      : (result.message || '清理失败');
    $('logHint').className = result.ok ? 'hint ok' : 'hint bad';
    await reloadLog();
  });

  $('editPlanSelect').addEventListener('change', async (event) => {
    const guid = event.target.value;
    await api().save_config({ last_plan_guid: guid });
    await renderPlanSettings(guid);
  });

  $('duplicatePlanBtn').addEventListener('click', async () => {
    const guid = $('editPlanSelect').value;
    if (!guid) {
      $('planSaveHint').textContent = '先选一个计划';
      return;
    }
    const stamp = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    const result = await api().duplicate_plan(guid, `我的计划 ${stamp}`);
    $('planSaveHint').textContent = result.ok ? '复制好了' : (result.message || '复制失败');
    $('planSaveHint').className = result.ok ? 'hint ok' : 'hint bad';
    await refresh();
  });

  $('deletePlanBtn').addEventListener('click', async () => {
    const guid = $('editPlanSelect').value;
    if (!guid) return;
    const result = await api().delete_plan(guid);
    $('planSaveHint').textContent = result.ok ? '删掉了' : (result.message || '删除失败');
    $('planSaveHint').className = result.ok ? 'hint ok' : 'hint bad';
    await refresh();
  });

  $('restorePlanBtn').addEventListener('click', async () => {
    const guid = $('editPlanSelect').value;
    if (!guid) return;
    if (!window.confirm('把这个计划恢复成 Windows 默认的设置？')) return;
    const result = await api().restore_plan(guid);
    $('planSaveHint').textContent = result.ok ? '恢复好了' : (result.message || '恢复失败');
    $('planSaveHint').className = result.ok ? 'hint ok' : 'hint bad';
    await renderPlanSettings(guid);
  });

  $('gpuApplyBtn').addEventListener('click', async () => {
    const choice = $('gpuPreference').value;
    const result = await api().set_gpu_preference(choice);
    if (!result.supported) {
      $('gpuHint').textContent = result.message || '这台机器不支持这一项';
      $('gpuHint').className = 'hint bad';
      return;
    }
    $('gpuHint').textContent = result.applied
      ? ('已设置：' + result.preference + '。显卡是否真的进入低功耗由驱动决定。')
      : (result.message || '设置失败');
    $('gpuHint').className = result.applied ? 'hint ok' : 'hint bad';
  });

  $('savePlanBtn').addEventListener('click', async () => {
    const guid = $('editPlanSelect').value;
    const changes = Object.values(planChanges);
    if (changes.length === 0) return;
    const result = await api().save_plan_settings(guid, changes);
    $('planSaveHint').textContent = result.ok ? '保存好了' : (result.message || '保存失败');
    $('planSaveHint').className = result.ok ? 'hint ok' : 'hint bad';
    if (result.ok) {
      planChanges = {};
      $('planSaveBar').hidden = true;
    }
  });

  $('addGameBtn').addEventListener('click', async () => {
    const chosen = await pickProcess();
    if (!chosen) return;
    const games = state.config.game_processes || [];
    if (games.includes(chosen)) return;
    await api().save_config({ game_processes: [...games, chosen] });
    await refresh();
  });

  $('ruleAction').addEventListener('change', buildActionValue);

  $('addConditionBtn').addEventListener('click', () => addConditionRow());

  $('ruleForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const hint = $('ruleFormHint');
    const name = $('ruleName').value.trim();
    if (!name) {
      if (hint) {
        hint.textContent = '先给规则起个名字。';
        hint.className = 'hint bad';
      }
      return;
    }

    // Every condition row the user left filled in becomes a condition.
    // An empty row is skipped rather than sent as an empty string, which
    // the daemon would have to guess about.
    const conditions = readConditions();
    const actionKind = $('ruleAction').value;
    const actionBox = $('ruleActionValue');
    const actionValue = actionBox ? actionBox.value.trim() : '';
    const priorityBox = $('rulePriority');
    const priority = priorityBox ? parseInt(priorityBox.value, 10) || 0 : 0;

    if (actionKind === 'set_power_plan' && !actionValue) {
      if (hint) {
        hint.textContent = '选一个要切过去的计划。';
        hint.className = 'hint bad';
      }
      return;
    }

    const result = await api().add_rule({
      name,
      conditions,
      action: { kind: actionKind, value: actionValue },
      enabled: true,
      priority,
    });

    if (hint) {
      hint.textContent = result.ok
        ? `加好了，共 ${conditions.length} 条条件。`
        : (result.message || '没加上');
      hint.className = result.ok ? 'hint ok' : 'hint bad';
    }

    if (result.ok) {
      $('ruleName').value = '';
      $('ruleConditions').innerHTML = '';
      addConditionRow();
      if (priorityBox) priorityBox.value = '0';
    }
    await refresh();
  });

  $('closeProcessSheet').addEventListener('click', (event) => {
    event.stopPropagation();
    closeProcessSheet(null);
  });
  $('processSheet').addEventListener('click', (event) => {
    if (event.currentTarget === event.target) closeProcessSheet(null);
  });
  $('processSearch').addEventListener('input', (event) => {
    loadProcesses(event.target.value);
  });

  wireOverride();
  wireLogTools();
  wireShortcuts();
  addConditionRow();
  buildActionValue();
}

/* --------------------------------------------------- the own power plans */

async function renderOwnPlans() {
  const area = $('ownPlanList');
  if (!area) return;
  const result = await api().own_plans();
  const plans = (result && result.plans) || [];
  area.innerHTML = '';
  if (plans.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = '这一版没有附带任何计划。';
    area.appendChild(empty);
    return;
  }

  for (const plan of plans) {
    const card = document.createElement('div');
    card.className = 'own-plan';
    const label = document.createElement('div');
    label.className = 'own-plan-name';
    label.textContent = plan.name;
    const note = document.createElement('div');
    note.className = 'own-plan-desc';
    note.textContent = plan.description || '';
    const actions = document.createElement('div');
    actions.className = 'card-actions';
    const button = document.createElement('button');
    button.className = 'btn small';
    button.textContent = '创建';
    button.addEventListener('click', async () => {
      button.disabled = true;
      const made = await api().create_own_plan(plan.key);
      button.disabled = false;
      $('planSaveHint').textContent = made.ok ? '做好了，已经切成它' : (made.message || '创建失败');
      $('planSaveHint').className = made.ok ? 'hint ok' : 'hint bad';
      await refresh();
    });
    actions.appendChild(button);
    card.appendChild(label);
    card.appendChild(note);
    card.appendChild(actions);
    area.appendChild(card);
  }
}

/* --------------------------------------------------------- the graphics card */

async function renderGpu() {
  const select = $('gpuPreference');
  const hint = $('gpuHint');
  if (!select) return;
  const result = await api().gpu_state();
  if (!result.supported) {
    select.innerHTML = '';
    select.disabled = true;
    $('gpuApplyBtn').disabled = true;
    hint.textContent = result.message || '这台机器不支持这一项';
    hint.className = 'hint bad';
    return;
  }
  select.disabled = false;
  $('gpuApplyBtn').disabled = false;
  if (select.options.length === 0) {
    select.appendChild(makeOption('saver', '省电'));
    select.appendChild(makeOption('performance', '最高性能'));
  }
  const load = result.utilisation >= 0 ? ('，当前负载 ' + result.utilisation + '%') : '';
  hint.textContent = '当前：' + (result.preference || '跟随系统') + load;
  hint.className = 'hint';
}

/* ------------------------------------------------------------ the log */

function startLogWatch() {
  if (logTimer !== null) return;
  reloadLog();
  logTimer = setInterval(pollLog, 1000);
}

function stopLogWatch() {
  if (logTimer === null) return;
  clearInterval(logTimer);
  logTimer = null;
}

async function reloadLog() {
  logOffset = 0;
  logLines = [];
  const view = $('logView');
  if (view) view.textContent = '';
  await pollLog();
}

async function pollLog() {
  const result = await api().log_tail(logOffset);
  if (!result || !result.ok) return;
  logOffset = result.offset;
  const fresh = result.lines || [];
  if (fresh.length === 0) return;
  logLines = logLines.concat(fresh);
  if (logLines.length > LOG_MAX_ON_SCREEN) {
    logLines = logLines.slice(logLines.length - LOG_MAX_ON_SCREEN);
  }
  applyLogFilter();
}

/* Draw the log with the current filter, and keep the overview feed in
 * step with it.  Both read the same buffer: the overview shows the last
 * few lines, the log page shows everything. */
function applyLogFilter() {
  let shown = logLines;
  if (LOG_LEVELS[logLevel]) {
    shown = shown.filter((line) => LOG_LEVELS[logLevel].test(line));
  }
  if (logQuery) {
    const needle = logQuery.toLowerCase();
    shown = shown.filter((line) => line.toLowerCase().includes(needle));
  }

  const view = $('logView');
  if (view) {
    view.textContent = shown.join('\n');
    if (logFollow) view.scrollTop = view.scrollHeight;
  }

  const count = $('logCount');
  if (count) {
    const filtered = shown.length !== logLines.length;
    count.textContent = filtered
      ? `显示 ${shown.length} / ${logLines.length} 行`
      : `${logLines.length} 行`;
  }

  renderOverviewFeed();
}

/* The last few lines, on the overview.  Picking the last ones rather
 * than the first means the newest thing that happened is what you see. */
function renderOverviewFeed() {
  const feed = $('overviewLog');
  if (!feed) return;
  feed.innerHTML = '';

  if (logLines.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = '还没有日志。';
    feed.appendChild(empty);
    return;
  }

  const tail = logLines.slice(-6);
  for (const line of tail) {
    const row = document.createElement('div');
    row.className = 'feed-line';
    if (LOG_LEVELS.error.test(line)) row.className += ' error';
    else if (LOG_LEVELS.warn.test(line)) row.className += ' warn';
    row.textContent = line;
    feed.appendChild(row);
  }
}

window.addEventListener('pywebviewready', () => {
  wire();
  refresh().then(() => {
    renderPlanSettings($('editPlanSelect').value);
    // The overview shows the tail of the log, and the log is only read
    // while its own page is open.  Read it once at startup so the feed
    // is not empty until the user visits the log page.
    pollLog().then(renderOverviewFeed);
  });
  setInterval(refresh, 5000);
});
