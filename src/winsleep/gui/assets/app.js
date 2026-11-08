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
    $('processSheet').hidden = false;
    $('processSearch').value = '';
    $('processList').innerHTML = '<p class="empty">正在读进程列表…</p>';
    loadProcesses('');
  });
}

function closeProcessSheet(result) {
  $('processSheet').hidden = true;
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
    item.addEventListener('click', () => closeProcessSheet(row.name));
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
    const result = await action();
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
  hint.textContent = '';
  actions.appendChild(
    serviceButton('停止', () => api().stop_service(), false)
  );
}

/* ------------------------------------------------------------- refresh */

async function refresh() {
  const data = await api().get_state();
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

  // The config keeps seconds, the interface works in minutes.
  $('idleMinutes').value = Math.round((config.idle_seconds ?? 600) / 60);
  $('idleCpu').value = config.idle_cpu_percent ?? 15;
  $('serverLockMinutes').value = Math.round((config.server_lock_seconds ?? 300) / 60);
  $('cpuValue').textContent = `低于 ${config.idle_cpu_percent ?? 15}%`;

  $('serverToggle').checked = !!config.server_mode;
  $('serverValue').textContent = config.server_mode ? '开启' : '关闭';
  $('keepAwakeToggle').checked = !!config.keep_awake;
  $('keepAwakeValue').textContent = config.keep_awake ? '开启' : '关闭';

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
    });
  }

  $('refreshBtn').addEventListener('click', refresh);

  $('switchBtn').addEventListener('click', async () => {
    const plan = $('planSelect').value;
    const result = await api().switch_plan(plan);
    if (!result.ok) {
      $('currentPlan').textContent = result.message || '切换失败';
    }
    await refresh();
  });

  $('idleMinutes').addEventListener('change', async () => {
    const minutes = parseFloat($('idleMinutes').value);
    if (!Number.isFinite(minutes) || minutes <= 0) return;
    await api().save_config({ idle_seconds: Math.round(minutes * 60) });
  });

  $('idleCpu').addEventListener('change', async () => {
    const percent = parseFloat($('idleCpu').value);
    if (!Number.isFinite(percent) || percent < 0) return;
    await api().save_config({ idle_cpu_percent: percent });
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

  $('serverLockMinutes').addEventListener('change', async () => {
    const minutes = parseFloat($('serverLockMinutes').value);
    if (!Number.isFinite(minutes) || minutes <= 0) return;
    await api().save_config({ server_lock_seconds: Math.round(minutes * 60) });
  });

  $('keepAwakeToggle').addEventListener('change', async (event) => {
    await api().set_keep_awake(event.target.checked);
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

  $('openLogBtn').addEventListener('click', async () => {
    const result = await api().open_log();
    $('logView').textContent = (result.lines || []).join('\n');
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

  $('closeProcessSheet').addEventListener('click', () => closeProcessSheet(null));
  $('processSheet').addEventListener('click', (event) => {
    if (event.target === $('processSheet')) closeProcessSheet(null);
  });
  $('processSearch').addEventListener('input', (event) => {
    loadProcesses(event.target.value);
  });

  buildConditionValue();
  buildActionValue();
}

window.addEventListener('pywebviewready', () => {
  wire();
  refresh().then(() => {
    const guid = guidOfPlan($('editPlanSelect').value);
    renderPlanSettings(guid);
  });
  setInterval(refresh, 5000);
});
