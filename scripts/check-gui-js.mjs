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
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(`${name} was not found in app.js`);
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

function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`ok    ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`FAIL  ${name}`);
    console.log(`      ${err.message}`);
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

test('the idle time is shown in a readable way', () => {
  assert.strictEqual(formatIdle(30), '30 秒');
  assert.strictEqual(formatIdle(90), '1 分 30 秒');
  assert.strictEqual(formatIdle(3600), '1 小时 0 分');
  assert.strictEqual(formatIdle(-1), '--');
});

/* ------------------------------------------------------------- report */

console.log('');
console.log(`${passed}/${passed + failed} checks passed`);
if (failed > 0) process.exit(1);
