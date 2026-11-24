/**
 * Field recovery for an ECIA / ANSI MH10.8.2 bag code whose separators
 * were lost — e.g. a USB keyboard-wedge reader that drops GS/RS, giving
 *
 *   K4048970914K027PC201,C202,C503Q10011K0925716424LJP1VMurata
 *
 * for K=40489709 · 14K=027 · P=C201,C202,C503 · Q=100 · 11K=092571642 ·
 * 4L=JP · 1V=Murata. A Data Identifier can abut an alphanumeric value
 * here, so the boundaries are not visible; this module tries every way of
 * cutting the string into `DI + value` fields and keeps the best one.
 *
 * Rules (each DI at most once; every character belongs to some field):
 *  - "Strict" DIs carry a value shape we can check — Q, 14K, 11K, 10K, 1K
 *    are digits, 10D/9D are four digits (YYWW), 4L is a two-letter country,
 *    1V is a name and only ever the last field. A reading that recovers
 *    more strict fields wins: those are the boundaries we can trust.
 *  - Next, a reading with a 1P (manufacturer P/N) beats one without.
 *  - Next, fewer free-text fields (P, 1P, K, 1T, …) — a free-text
 *    field could otherwise be split anywhere a letter happens to look like
 *    a DI, e.g. the "T" in "LM317T".
 *  - Last, longer DIs (11K over 1K: "Q10011K…" is Q=100 + 11K).
 *
 * DigiKey prints an empty K field, so K alone may be empty, and an empty K
 * is not counted as a free-text field.
 *
 * Conservative by design: when two equally good readings disagree about a
 * field, that field is left out rather than guessed. When nothing covers
 * the whole string, `null` tells the caller to use its older fallback.
 */

export type SegmentField = { di: string; value: string };

type Rule = { di: string; shape: RegExp; strict: boolean; lastOnly?: boolean; mayBeEmpty?: boolean };

const TEXT = /^.+$/;
const TOKEN = /^\S+$/;

const RULES: Rule[] = [
  { di: "Q", shape: /^\d{1,7}$/, strict: true },
  // Mouser zero-pads the line item to three digits ("027"). Without that
  // floor "14K021PQ201" also reads as 14K=02 + 1P=Q201, and the 1P
  // preference below would pick it — a customer P/N taken for the MPN.
  { di: "14K", shape: /^\d{3,6}$/, strict: true },
  { di: "11K", shape: /^\d{1,12}$/, strict: true },
  { di: "10K", shape: /^\d{1,12}$/, strict: true },
  // 1K is DigiKey's (numeric) sales order. A non-numeric PO only ever
  // seen with separators intact, where this module is not used.
  { di: "1K", shape: /^\d{1,12}$/, strict: true },
  { di: "10D", shape: /^\d{4}$/, strict: true },
  { di: "9D", shape: /^\d{4}$/, strict: true },
  { di: "4L", shape: /^[A-Z]{2}$/, strict: true },
  { di: "1V", shape: /^[A-Za-z].*$/, strict: true, lastOnly: true },
  { di: "1P", shape: TOKEN, strict: false },
  { di: "30P", shape: TOKEN, strict: false },
  { di: "P", shape: TEXT, strict: false },
  { di: "K", shape: /^.*$/, strict: false, mayBeEmpty: true },
  { di: "1T", shape: TEXT, strict: false },
  { di: "1S", shape: TEXT, strict: false },
  // DigiKey trailer fields — recognised only so they don't bleed into
  // the value before them.
  { di: "11Z", shape: TEXT, strict: false },
  { di: "12Z", shape: TEXT, strict: false },
  { di: "13Z", shape: TEXT, strict: false },
  { di: "20Z", shape: TEXT, strict: false },
];

/** Keep at most this many equally good readings per sub-problem — enough
 *  to notice a disagreement, bounded so a pathological input stays cheap. */
const MAX_TIES = 16;

type Score = [strict: number, hasMpn: number, freeText: number, diLength: number];

const ZERO: Score = [0, 0, 0, 0];

function fieldScore(rule: Rule, value: string): Score {
  return [
    rule.strict ? 1 : 0,
    rule.di === "1P" ? 1 : 0,
    !rule.strict && value ? -1 : 0,
    rule.di.length,
  ];
}

function add(a: Score, b: Score): Score {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3]];
}

function compare(a: Score, b: Score): number {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] - b[i];
  return 0;
}

/** Every (position, rule) where a DI's text occurs in `s`. */
function diStarts(s: string): Map<number, Rule[]> {
  const out = new Map<number, Rule[]>();
  for (const rule of RULES) {
    for (let i = s.indexOf(rule.di); i !== -1; i = s.indexOf(rule.di, i + 1)) {
      out.set(i, [...(out.get(i) ?? []), rule]);
    }
  }
  return out;
}

type Best = { score: Score; readings: SegmentField[][] } | null;

/** Best-scoring complete readings of `s`, by memoised search over
 *  (position, DIs already used). Every score component is additive per
 *  field, so the best suffix from a state is independent of the prefix. */
function bestReadings(s: string): Best {
  const starts = diStarts(s);
  const boundaries = [...starts.keys(), s.length].sort((a, b) => a - b);
  const memo = new Map<string, Best>();

  const solve = (pos: number, used: string[]): Best => {
    if (pos === s.length) return { score: ZERO, readings: [[]] };
    const key = `${pos}|${used.join(",")}`;
    if (memo.has(key)) return memo.get(key)!;
    let best: Best = null;
    for (const rule of starts.get(pos) ?? []) {
      if (used.includes(rule.di)) continue;
      const from = pos + rule.di.length;
      const nextUsed = [...used, rule.di].sort();
      for (const end of boundaries) {
        if (end < from || (end === from && !rule.mayBeEmpty)) continue;
        if (rule.lastOnly && end !== s.length) continue;
        const value = s.slice(from, end);
        if (!rule.shape.test(value)) continue;
        const tail = solve(end, nextUsed);
        if (!tail) continue;
        const sc = add(fieldScore(rule, value), tail.score);
        const field = { di: rule.di, value };
        const readings = tail.readings.map(r => [field, ...r]);
        const cmp = best ? compare(sc, best.score) : 1;
        if (cmp > 0) best = { score: sc, readings: readings.slice(0, MAX_TIES) };
        else if (cmp === 0 && best) best.readings = [...best.readings, ...readings].slice(0, MAX_TIES);
      }
    }
    memo.set(key, best);
    return best;
  };

  return solve(0, []);
}

/**
 * Best reading of a separator-less field string (header already stripped),
 * or null when there is none. Fields the best readings disagree on are
 * omitted. Empty values (DigiKey's blank K) are dropped.
 */
export function segmentWithoutSeparators(s: string): SegmentField[] | null {
  const best = bestReadings(s);
  if (!best) return null;
  const valueOf = (r: SegmentField[], di: string) => r.find(f => f.di === di)?.value;
  const [first, ...rest] = best.readings;
  return first.filter(f => f.value && rest.every(r => valueOf(r, f.di) === f.value));
}
