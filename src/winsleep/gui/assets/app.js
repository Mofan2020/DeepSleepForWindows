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
  run_command: '运行命令',
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

function describeRule(rule) {
  const conditions = (rule.conditions || [])
    .map((c) => `${CONDITION_LABELS[c.kind] || c.kind} ${c.value}`)
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

async function refresh() {
  const state = await api().get_state();
  const config = state.config || {};

  $('currentPlan').textContent = state.active_plan || '未知';
  $('activePlan').textContent = state.active_plan || '未知';

  const idle = state.idle_seconds;
  $('idleInfo').textContent = '空闲 ' + formatIdle(idle);

  const dot = $('statusDot');
  if (!state.daemon_running) {
    dot.className = 'dot off';
    $('daemonState').textContent = '后台服务未运行';
  } else {
    dot.className = 'dot on';
    $('daemonState').textContent = '后台服务运行中';
  }

  const plans = state.plans || [];
  fillPlanSelect($('planSelect'), plans, state.active_plan);
  fillPlanSelect($('idlePlanSelect'), plans, config.idle_plan);

  $('idlePlanValue').textContent = config.idle_plan || '未设置';
  $('idleSeconds').value = config.idle_seconds ?? 600;

  $('serverToggle').checked = !!config.server_mode;
  $('serverValue').textContent = config.server_mode ? '开启' : '关闭';
  $('keepAwakeToggle').checked = !!config.keep_awake;

  renderRules(config.rules || []);
}

function wire() {
  $('refreshBtn').addEventListener('click', refresh);

  $('switchBtn').addEventListener('click', async () => {
    const plan = $('planSelect').value;
    const result = await api().switch_plan(plan);
    if (!result.ok) {
      $('currentPlan').textContent = result.message || '切换失败';
    }
    await refresh();
  });

  $('idleSeconds').addEventListener('change', async () => {
    const seconds = parseInt($('idleSeconds').value, 10);
    if (!Number.isFinite(seconds) || seconds <= 0) return;
    await api().save_config({ idle_seconds: seconds });
  });

  $('idlePlanSelect').addEventListener('change', async () => {
    await api().save_config({ idle_plan: $('idlePlanSelect').value });
    $('idlePlanValue').textContent = $('idlePlanSelect').value;
  });

  $('serverToggle').addEventListener('change', async (event) => {
    await api().set_server_mode(event.target.checked);
    $('serverValue').textContent = event.target.checked ? '开启' : '关闭';
  });

  $('keepAwakeToggle').addEventListener('change', async (event) => {
    await api().set_keep_awake(event.target.checked);
  });

  $('lockBtn').addEventListener('click', async () => {
    await api().lock_screen();
  });

  $('ruleForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const name = $('ruleName').value.trim();
    if (!name) return;

    const kind = $('ruleKind').value;
    const value = $('ruleValue').value.trim();
    const actionKind = $('ruleAction').value;
    const actionValue = $('ruleActionValue').value.trim();

    const result = await api().add_rule({
      name,
      conditions: value ? [{ kind, value }] : [],
      action: { kind: actionKind, value: actionValue },
      enabled: true,
      priority: 0,
    });

    if (result.ok) {
      $('ruleName').value = '';
      $('ruleValue').value = '';
      $('ruleActionValue').value = '';
    }
    await refresh();
  });
}

window.addEventListener('pywebviewready', () => {
  wire();
  refresh();
  setInterval(refresh, 5000);
});
