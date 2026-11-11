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

function guidOfPlan(name) {
  const index = (state.plans || []).indexOf(name);
  if (index < 0) return '';
  return (state.plan_guids || [])[index] || '';
}

/* ---------------------------------------------------------- rule form */

function buildConditionValue() {
  const area = $('ruleValueArea');
  area.innerHTML = '';
  area.dataset.kind = '';
  const kind = $('ruleKind').value;

  if (kind === 'idle_at_least' || kind === 'idle_below') {
    const number = document.createElement('input');
    number.className = 'num';
    number.type = 'number';
    number.min = '0';
    number.step = '1';
    number.id = 'ruleValue';
    number.placeholder = '10';
    const unit = document.createElement('span');
    unit.className = 'unit';
    unit.textContent = '分钟';
    area.append(number, unit);
    return;
  }

  if (kind === 'cpu_below' || kind === 'cpu_above') {
    const number = document.createElement('input');
    number.className = 'num';
    number.type = 'number';
    number.min = '0';
    number.max = '100';
    number.step = '1';
    number.id = 'ruleValue';
    number.placeholder = '15';
    const unit = document.createElement('span');
    unit.className = 'unit';
    unit.textContent = '%';
    area.append(number, unit);
    return;
  }

  if (kind === 'process_running') {
    const text = document.createElement('input');
    text.className = 'text';
    text.id = 'ruleValue';
    text.placeholder = 'steam.exe';
    const pick = document.createElement('button');
    pick.type = 'button';
    pick.className = 'btn';
    pick.textContent = '选择';
    pick.addEventListener('click', async () => {
      const chosen = await pickProcess();
      if (chosen) text.value = chosen;
    });
    area.append(text, pick);
    return;
  }

  if (kind === 'time_window') {
    const text = document.createElement('input');
    text.className = 'text';
    text.id = 'ruleValue';
    text.placeholder = '09:00-18:00';
    const unit = document.createElement('span');
    unit.className = 'unit';
    unit.textContent = '24 小时制';
    area.append(text, unit);
    return;
  }

  if (kind === 'day_of_week') {
    const days = [
      ['mon', '一'], ['tue', '二'], ['wed', '三'], ['thu', '四'],
      ['fri', '五'], ['sat', '六'], ['sun', '日'],
    ];
    const box = document.createElement('div');
    box.className = 'day-row';
    for (const [key, label] of days) {
      const wrap = document.createElement('label');
      wrap.className = 'day';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = key;
      input.className = 'day-input';
      const span = document.createElement('span');
      span.textContent = label;
      wrap.append(input, span);
      box.appendChild(wrap);
    }
    area.appendChild(box);
    area.dataset.kind = 'day_of_week';
  }
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
  const raw = box.value.trim();
  if (!raw) return '';

  if (CONDITION_UNITS[kind] === '分钟') {
    const minutes = parseFloat(raw);
    if (!Number.isFinite(minutes)) return raw;
    // The rule keeps seconds, so a rule written by hand stays
    // readable and the interface works in minutes.
    return String(Math.round(minutes * 60));
  }
  return raw;
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
    remove.className = 'btn ghost';
    remove.textContent = '删除';
    remove.addEventListener('click', async () => {
      await api().remove_rule(rule.name);
      await refresh();
    });

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

  for (const row of rows) {
    const line = document.createElement('div');
    line.className = 'setting';

    const label = document.createElement('label');
    label.className = 'setting-label';
    label.textContent = row.name;

    let picker;
    if (!row.choices || row.choices.length === 0) {
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

    const unit = document.createElement('span');
    unit.className = 'unit';
    unit.textContent = row.unit || '';

    line.append(label, picker, unit);
    box.appendChild(line);
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
 * running is not a dead end: there is always something to press. */
function renderService(service) {
  const actions = $('serviceActions');
  actions.innerHTML = '';
  const text = $('serviceText');
  const hint = $('serviceHint');

  if (!service) {
    text.textContent = '正在检查…';
    return;
  }

  if (!service.installed) {
    text.textContent = '后台服务未安装。装上之后，登录时它就会自己起来。';
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
    text.textContent = '后台服务的任务还指着程序原来的位置。';
    hint.textContent = '程序被移动过了，把任务重新装一次就好。';
    actions.appendChild(
      serviceButton('重新安装任务', () => api().install_service(), true)
    );
    return;
  }

  if (!service.running) {
    text.textContent = '后台服务已安装，但没有在运行。';
    hint.textContent = '';
    actions.appendChild(
      serviceButton('启动', () => api().start_service(), true)
    );
    return;
  }

  text.textContent = service.agent_running
    ? '后台服务运行中，正在看着你的键盘和鼠标。'
    : '后台服务运行中。代理没有在运行，读不到真实的空闲时间。';

  /* The icon lives in the notification area, which Windows 11 hides
   * behind a small arrow by default.  Saying whether it is really
   * there is the only useful answer to "where is it?". */
  const tray = [];
  if (service.tray_installed) {
    tray.push(service.tray_running ? '托盘图标已显示' : '托盘图标没有起来');
  } else {
    tray.push('托盘图标没有装上');
  }
  hint.textContent = tray.join('，');

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

  const editSelect = $('editPlanSelect');
  const wanted = config.last_plan_guid || data.active_plan_guid || '';
  const current = editSelect.value;
  fillPlanSelect(editSelect, plans, current || wanted);

  let service = null;
  try {
    service = await api().service_status();
  } catch (err) {
    service = null;
  }
  renderService(service);
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
    const guid = guidOfPlan(event.target.value);
    await api().save_config({ last_plan_guid: guid });
    await renderPlanSettings(guid);
  });

  $('duplicatePlanBtn').addEventListener('click', async () => {
    const guid = guidOfPlan($('editPlanSelect').value);
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
    const guid = guidOfPlan($('editPlanSelect').value);
    if (!guid) return;
    const result = await api().delete_plan(guid);
    $('planSaveHint').textContent = result.ok ? '删掉了' : (result.message || '删除失败');
    $('planSaveHint').className = result.ok ? 'hint ok' : 'hint bad';
    await refresh();
  });

  $('restorePlanBtn').addEventListener('click', async () => {
    const guid = guidOfPlan($('editPlanSelect').value);
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
    const guid = guidOfPlan($('editPlanSelect').value);
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

  $('ruleKind').addEventListener('change', buildConditionValue);
  $('ruleAction').addEventListener('change', buildActionValue);

  $('ruleForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const name = $('ruleName').value.trim();
    if (!name) return;

    const kind = $('ruleKind').value;
    const value = conditionValue();
    const actionKind = $('ruleAction').value;
    const actionBox = $('ruleActionValue');
    const actionValue = actionBox ? actionBox.value.trim() : '';

    const result = await api().add_rule({
      name,
      conditions: value ? [{ kind, value }] : [],
      action: { kind: actionKind, value: actionValue },
      enabled: true,
      priority: 0,
    });

    if (result.ok) {
      $('ruleName').value = '';
      buildConditionValue();
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

  buildConditionValue();
  buildActionValue();
}

/* --------------------------------------------------- the own power plans */

async function renderOwnPlans() {
  const area = $('ownPlanList');
  if (!area) return;
  const result = await api().own_plans();
  const plans = (result && result.plans) || [];
  area.innerHTML = '';
  for (const plan of plans) {
    const card = document.createElement('div');
    card.className = 'card glass';
    const label = document.createElement('div');
    label.className = 'card-label';
    label.textContent = plan.name;
    const note = document.createElement('div');
    note.className = 'card-value';
    note.textContent = plan.description || '';
    const actions = document.createElement('div');
    actions.className = 'card-actions';
    const button = document.createElement('button');
    button.className = 'btn';
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
  const view = $('logView');
  view.textContent = logLines.join('\n');
  if (logFollow) {
    view.scrollTop = view.scrollHeight;
  }
}

window.addEventListener('pywebviewready', () => {
  wire();
  refresh().then(() => {
    const guid = guidOfPlan($('editPlanSelect').value);
    renderPlanSettings(guid);
  });
  setInterval(refresh, 5000);
});
