import type { FormInput, FormRecord, SummaryDraft } from '../api/types';

// The record editor's own shape and the translations to and from what the
// server stores. Pure, so the rules -- what is sent, what a generated draft is
// allowed to overwrite -- can be tested without rendering anything.

const RTL = new Set(['fa', 'ar', 'he', 'ur', 'ps', 'ckb', 'yi', 'dv']);

/** Persian and Arabic read right to left; the editor must not mangle them. */
export function isRtl(language: string): boolean {
  return RTL.has((language.split('-')[0] ?? '').toLowerCase());
}

export function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) ?? code;
  } catch {
    return code;
  }
}

export interface FormState {
  slug: string;
  title: Record<string, string>;
  summary: Record<string, string>;
  type: string;
  ownerTeam: string;
  tags: string;
  keywords: string;
  aliases: string;
  body: string;
}

export function emptyForm(languages: string[], seedTitle = ''): FormState {
  return {
    slug: seedTitle ? slugify(seedTitle) : '',
    title: Object.fromEntries(languages.map((l, i) => [l, i === 0 ? seedTitle : ''])),
    summary: Object.fromEntries(languages.map((l) => [l, ''])),
    type: 'doc',
    ownerTeam: '',
    tags: '',
    keywords: '',
    aliases: '',
    body: '',
  };
}

export function fromRecord(record: FormRecord, languages: string[]): FormState {
  // Show every configured language, and any the record already carries (a
  // language dropped from KB_SUMMARY_LANGUAGES must not silently lose its text).
  const all = [...new Set([...languages, ...Object.keys(record.title), ...Object.keys(record.summary)])];
  return {
    slug: record.slug,
    title: Object.fromEntries(all.map((l) => [l, record.title[l] ?? ''])),
    summary: Object.fromEntries(all.map((l) => [l, record.summary[l] ?? ''])),
    type: record.type,
    ownerTeam: record.owner_team ?? '',
    tags: record.tags.join(', '),
    keywords: record.keywords.join(', '),
    aliases: record.aliases.join(', '),
    body: record.body,
  };
}

/** "Release process" -> "release-process"; Persian and other non-Latin titles fall back to "". */
export function slugify(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80)
    .replace(/-+$/g, '');
}

export function splitList(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[,\n،]/)) {
    const item = part.trim();
    if (item) seen.add(item);
  }
  return [...seen];
}

function joinList(items: string[]): string {
  return items.join(', ');
}

function nonEmpty(values: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim()));
}

export function toInput(form: FormState): FormInput {
  return {
    slug: form.slug.trim(),
    title: nonEmpty(form.title),
    body: form.body,
    type: form.type,
    summary: nonEmpty(form.summary),
    tags: splitList(form.tags),
    keywords: splitList(form.keywords),
    aliases: splitList(form.aliases),
    owner_team: form.ownerTeam.trim() || null,
  };
}

/** What the editor can check before asking; the server checks again. */
export function problems(form: FormState, isNew: boolean): string[] {
  const found: string[] = [];
  if (isNew && !/^[a-z0-9]([a-z0-9-]{0,78}[a-z0-9])?$/.test(form.slug.trim())) {
    found.push('The slug needs lowercase letters, digits and "-" (at most 80 characters).');
  }
  if (!Object.values(form.title).some((t) => t.trim())) found.push('Give it a title in at least one language.');
  if (!form.body.trim()) found.push('Write the record’s text.');
  return found;
}

export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/**
 * Apply a generated draft to the form without taking anything away from the
 * operator: summaries are replaced (they asked for them), titles only fill the
 * languages left empty, and keywords and aliases are added to, not swapped.
 */
export function applyDraft(form: FormState, draft: SummaryDraft): FormState {
  const title = { ...form.title };
  for (const [language, text] of Object.entries(draft.title)) {
    if (!(title[language] ?? '').trim() && text.trim()) title[language] = text;
  }
  const summary = { ...form.summary };
  for (const [language, text] of Object.entries(draft.summary)) {
    if (text.trim()) summary[language] = text;
  }
  return {
    ...form,
    title,
    summary,
    keywords: joinList(splitList([form.keywords, ...draft.keywords].join(','))),
    aliases: joinList(splitList([form.aliases, ...draft.aliases].join(','))),
  };
}

/** "new" or "new:hiring plan" -> a blank record, seeded from a search nobody answered. */
export function parseFormsParam(param: string | null): { seed: string } | null {
  if (param === 'new') return { seed: '' };
  if (param?.startsWith('new:')) return { seed: param.slice(4) };
  return null;
}

export function recordTitle(record: Pick<FormRecord, 'title' | 'slug'>, languages: string[]): string {
  for (const l of languages) if (record.title[l]) return record.title[l];
  return Object.values(record.title)[0] ?? record.slug;
}
