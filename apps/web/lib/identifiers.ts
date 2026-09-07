/**
 * Shapes in a question that look like they identify a person.
 *
 * The research-use banner said the same sentence on every screen, which is how people
 * learn to stop reading it. This is the version that earns its space: silent until the
 * field actually contains something identifying, and specific about what it found and
 * where.
 *
 * Everything here runs in the browser and stays there. Nothing detected is sent anywhere -
 * the point is to stop the text being submitted, not to record that it existed.
 *
 * Deliberately conservative. A false positive costs a reader one dismissal; a false
 * negative sends a name to a research pipeline. Where the two trade off this errs toward
 * flagging - but not so far that ordinary clinical prose ("viral load 1200 copies/mL",
 * "World Health Organization") trips it, because a guard that cries wolf is the banner
 * again.
 */

export type IdentifierKind = "name" | "date" | "record-number" | "email" | "phone";

export interface DetectedIdentifier {
  /** Character offset into the question, so the span can be marked in place. */
  start: number;
  end: number;
  kind: IdentifierKind;
  label: string;
  text: string;
}

const KIND_LABEL: Record<IdentifierKind, string> = {
  name: "a name",
  date: "a date of birth",
  "record-number": "a record number",
  email: "an email address",
  phone: "a phone number",
};

/**
 * Words that begin a sentence, name an organisation, or are clinical vocabulary.
 *
 * A capitalised pair is the weakest signal here, so it is filtered hardest: without this
 * list "Viral Load", "Treatment Failure" and "World Health" all read as names.
 */
const NOT_A_NAME = new Set([
  "the", "a", "an", "for", "in", "on", "at", "of", "and", "or", "but", "with", "without",
  "what", "when", "where", "which", "who", "whom", "how", "why", "should", "does", "do",
  "is", "are", "was", "were", "can", "could", "would", "will", "shall", "may", "might",
  "adult", "adults", "child", "children", "patient", "patients", "person", "people",
  "man", "woman", "men", "women", "infant", "infants", "adolescent", "adolescents",
  "world", "health", "organization", "organisation", "guideline", "guidelines",
  "consolidated", "recommendation", "recommendations", "annex", "section", "table",
  "viral", "load", "treatment", "failure", "therapy", "regimen", "antiretroviral",
  "prophylaxis", "exposure", "testing", "services", "diagnostic", "monitoring",
  "first", "second", "third", "line", "dose", "dosing", "copies", "count", "confirmed",
  "hiv", "aids", "art", "prep", "pep", "tb", "who", "cdc", "nih", "dhhs", "eacs", "bhiva",
  // The releases beyond HIV: hypertension, chronic care, the wider WHO catalogue.
  "blood", "pressure", "hypertension", "hypertensive", "systolic", "diastolic", "target",
  "targets", "diabetes", "diabetic", "kidney", "renal", "chronic", "acute", "disease",
  "diseases", "cardiovascular", "heart", "stroke", "coronary", "artery", "atrial",
  "fibrillation", "obstructive", "pulmonary", "asthma", "cancer", "cervical", "screening",
  "risk", "assessment", "management", "medication", "medications", "medicine", "medicines",
  "pharmacological", "lifestyle", "salt", "sodium", "glucose", "cholesterol", "lipid",
  "lipids", "statin", "statins", "thiazide", "calcium", "channel", "blocker", "blockers",
  "ace", "inhibitor", "inhibitors", "angiotensin", "receptor", "beta", "hepatitis",
  "tuberculosis", "preventive", "cryptococcal", "meningitis", "syphilis", "sexually",
  "transmitted", "infection", "infections", "pregnancy", "pregnant", "breastfeeding",
  "antenatal", "postnatal", "prevention", "advanced", "package", "care", "primary",
  "communicable", "noncommunicable", "non-communicable", "digital", "adaptation", "kit",
  "type", "stage", "grade", "mmhg", "bmi", "ncd", "ncds", "smart",
]);

function isNameWord(word: string): boolean {
  if (!/^[A-Z][a-z'’-]+$/.test(word)) return false;
  return !NOT_A_NAME.has(word.toLowerCase());
}

function digitCount(value: string): number {
  return value.replaceAll(/\D/g, "").length;
}

interface Span {
  start: number;
  end: number;
  text: string;
}

interface Rule {
  kind: IdentifierKind;
  pattern: RegExp;
  /**
   * Narrow or reject a raw match.
   *
   * Narrowing matters for names: the pattern matches any run of capitalised words, and
   * such a run routinely begins with the sentence's first word ("For Maria Okonkwo").
   * Rejecting the whole run because of its first word would miss the name entirely, so
   * the run is trimmed to the part that reads as one.
   */
  refine?: (match: RegExpExecArray) => Span | null;
}

const RULES: Rule[] = [
  {
    kind: "email",
    pattern: /[\w.%+-]+@[\w-]+(?:\.[\w-]+)+/g,
  },
  {
    // A full date, written any of the usual ways. A bare year is not a date of birth.
    kind: "date",
    pattern:
      /\b(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b/gi,
  },
  {
    /*
     * A long digit run that is not a measurement.
     *
     * Clinical values carry a unit, so a number followed by one is left alone: a viral
     * load of 1200000 copies/mL is data, not an identity.
     */
    kind: "record-number",
    pattern: /\b(?:[A-Z]{2,4}[-\s]?)?\d{6,}\b/g,
    refine: (match) => {
      const end = match.index + match[0].length;
      const after = match.input.slice(end, end + 14);
      if (/^\s*(?:copies|cells|mg|ml|kg|mmol|mcg|iu|%|\/)/i.test(after)) return null;
      return { start: match.index, end, text: match[0] };
    },
  },
  {
    /*
     * A dialling-shaped run, counted rather than parsed.
     *
     * Grouping varies by country far more than one pattern can encode, so this matches a
     * plausible run of digits and separators and then decides on the digit count. A
     * separator is required, which keeps it from re-reporting the long bare numbers the
     * record-number rule already claims.
     */
    kind: "phone",
    pattern: /\+?\d[\d\s().-]{7,}\d/g,
    refine: (match) => {
      const text = match[0].trim();
      const digits = digitCount(text);
      if (digits < 9 || digits > 15) return null;
      if (!/[\s().-]/.test(text)) return null;
      return { start: match.index, end: match.index + text.length, text };
    },
  },
  {
    // A run of capitalised words, trimmed to the part that reads as a name.
    kind: "name",
    pattern: /\b[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)+\b/g,
    refine: (match) => {
      const words = match[0].split(/\s+/);
      let first = 0;
      let last = words.length - 1;
      while (first <= last && !isNameWord(words[first]!)) first += 1;
      while (last >= first && !isNameWord(words[last]!)) last -= 1;
      if (last - first < 1) return null;
      const kept = words.slice(first, last + 1);
      if (!kept.every((word) => isNameWord(word))) return null;
      const text = kept.join(" ");
      const offset = match[0].indexOf(text);
      if (offset < 0) return null;
      return { start: match.index + offset, end: match.index + offset + text.length, text };
    },
  },
];

/**
 * Identifier-shaped spans in `question`, in the order they appear.
 *
 * Overlaps are resolved in rule order, so an address is never also reported as a name.
 */
export function detectIdentifiers(question: string): DetectedIdentifier[] {
  const found: DetectedIdentifier[] = [];
  const claimed: Span[] = [];

  for (const rule of RULES) {
    const pattern = new RegExp(rule.pattern.source, rule.pattern.flags);
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(question)) !== null) {
      if (match[0].length === 0) {
        pattern.lastIndex += 1;
        continue;
      }
      const span = rule.refine
        ? rule.refine(match)
        : { start: match.index, end: match.index + match[0].length, text: match[0] };
      if (!span) continue;
      if (claimed.some((taken) => span.start < taken.end && span.end > taken.start)) continue;
      claimed.push(span);
      found.push({ ...span, kind: rule.kind, label: KIND_LABEL[rule.kind] });
    }
  }

  return found.sort((left, right) => left.start - right.start);
}

/** "a name and a date of birth" — what the warning leads with. */
export function describeIdentifiers(found: DetectedIdentifier[]): string {
  const labels = [...new Set(found.map((item) => item.label))];
  if (labels.length <= 1) return labels[0] ?? "";
  if (labels.length === 2) return `${labels[0]} and ${labels[1]}`;
  return `${labels.slice(0, -1).join(", ")} and ${labels.at(-1)}`;
}

/** The question with every detected span removed, for the one-click fix. */
export function stripIdentifiers(question: string, found: DetectedIdentifier[]): string {
  let result = question;
  for (const item of [...found].sort((left, right) => right.start - left.start)) {
    result = result.slice(0, item.start) + result.slice(item.end);
  }
  return result
    .replaceAll(/\s{2,}/g, " ")
    .replaceAll(/\s+([,.;:?])/g, "$1")
    .replaceAll(/,\s*,/g, ",")
    .trim();
}
