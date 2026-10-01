// Pure calculation source, also embedded verbatim by build.mjs.
export function calculate(input) {
  const errors = [];
  const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
  const number = (x, path) => {
    if (typeof x !== 'number' || !Number.isFinite(x) || x < 0) errors.push(`${path}: 유한한 비음수 숫자가 필요합니다`);
  };
  if (!object(input)) return {status: 'invalid', errors: ['입력 객체가 필요합니다']};
  if (!Array.isArray(input.demand) || !input.demand.length) errors.push('demand: 비어 있지 않은 배열이 필요합니다');
  else Array.from(input.demand).forEach((d, i) => number(d, `demand[${i}]`));
  if (!Array.isArray(input.candidates)) errors.push('candidates: 배열이 필요합니다');
  const ids = new Set();
  if (Array.isArray(input.candidates)) Array.from(input.candidates).forEach((c, i) => {
    const p = `candidates[${i}]`;
    if (!object(c)) { errors.push(`${p}: 객체가 필요합니다`); return; }
    if (typeof c.id !== 'string' || !c.id.trim() || ids.has(c.id)) errors.push(`${p}.id: 비어 있지 않은 고유 ID가 필요합니다`);
    ids.add(c.id);
    number(c.capacity, `${p}.capacity`);
    number(c.fixedCost, `${p}.fixedCost`);
    if (!Array.isArray(c.unitCosts) || c.unitCosts.length !== input.demand?.length) errors.push(`${p}.unitCosts: 수요와 같은 길이의 배열이 필요합니다`);
    if (Array.isArray(c.unitCosts)) Array.from(c.unitCosts).forEach((v, j) => number(v, `${p}.unitCosts[${j}]`));
  });
  if (typeof input.baseline !== 'string' || !input.baseline.trim()) errors.push('baseline: 기준 ID가 필요합니다');
  else if (ids.size && !ids.has(input.baseline)) errors.push('baseline: 존재하지 않는 기준 ID');
  if (errors.length) return {status: 'invalid', errors};
  const totalDemand = input.demand.reduce((a, b) => a + b, 0);
  const candidates = input.candidates.map(c => {
    const shippingCost = input.demand.reduce((s, d, i) => s + d * c.unitCosts[i], 0);
    return {id: c.id, capacity: c.capacity, fixedCost: c.fixedCost, shippingCost,
      totalCost: c.fixedCost + shippingCost, feasible: c.capacity >= totalDemand, slack: c.capacity - totalDemand};
  });
  if (!Number.isFinite(totalDemand) || candidates.some(c => !Number.isFinite(c.totalCost)))
    return {status: 'invalid', errors: ['계산 범위 초과: 수요 합계 또는 비용의 overflow']};
  const feasible = candidates.filter(c => c.feasible);
  let best = null;
  for (const c of feasible) if (!best || c.totalCost < best.totalCost || (c.totalCost === best.totalCost && c.id < best.id)) best = c;
  const baseline = candidates.find(c => c.id === input.baseline) ?? null;
  const comparable = !!(best && baseline?.feasible);
  const savings = comparable ? baseline.totalCost - best.totalCost : null;
  const savingsPercent = comparable && baseline.totalCost > 0 ? (savings / baseline.totalCost) * 100 : null;
  return {status: best ? 'ok' : 'no_feasible', totalDemand, candidates, bestId: best?.id ?? null,
    bestCost: best?.totalCost ?? null, tiedIds: best ? feasible.filter(c => c.totalCost === best.totalCost).map(c => c.id).sort() : [],
    baselineId: input.baseline, baselineCost: baseline?.totalCost ?? null, comparable, savings, savingsPercent};
}
