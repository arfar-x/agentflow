import {
  applyDraft,
  byteLength,
  emptyForm,
  fromRecord,
  isRtl,
  parseFormsParam,
  problems,
  slugify,
  splitList,
  toInput,
} from '../src/lib/forms';
import { renderMarkdown } from '../src/lib/markdown';
import type { FormRecord } from '../src/api/types';

const record: FormRecord = {
  slug: 'glossary',
  title: { en: 'Glossary', fa: 'واژه‌نامه' },
  body: 'Terms.',
  type: 'term',
  summary: { en: 'Our terms.' },
  tags: ['terms'],
  keywords: ['glossary'],
  aliases: [],
  owner_team: 'platform',
  revision: 3,
  created_at: null,
  updated_at: null,
  publication: 'pending',
};

describe('slugify', () => {
  it('makes a slug the server accepts, or nothing', () => {
    expect(slugify('Release  process!')).toBe('release-process');
    expect(slugify('Café à la carte')).toBe('cafe-a-la-carte');
    expect(slugify('فرایند انتشار')).toBe('');
    expect(slugify('x'.repeat(200)).length).toBe(80);
  });
});

describe('lists', () => {
  it('splits on commas, newlines and the Persian comma, dropping blanks and repeats', () => {
    expect(splitList('a, b,, a\nc،d')).toEqual(['a', 'b', 'c', 'd']);
  });
});

describe('the form', () => {
  it('starts with a tab per configured language and a slug from the seed', () => {
    const form = emptyForm(['en', 'fa'], 'Hiring plan');
    expect(Object.keys(form.title)).toEqual(['en', 'fa']);
    expect(form.title.en).toBe('Hiring plan');
    expect(form.slug).toBe('hiring-plan');
  });

  it('keeps a language the record has even when it is no longer configured', () => {
    const form = fromRecord({ ...record, title: { ...record.title, de: 'Glossar' } }, ['en']);
    expect(Object.keys(form.title).sort()).toEqual(['de', 'en', 'fa']);
  });

  it('sends only what has text, as the server stores it', () => {
    const form = { ...fromRecord(record, ['en', 'fa', 'de']), ownerTeam: '  ' };
    expect(toInput(form)).toEqual({
      slug: 'glossary',
      title: { en: 'Glossary', fa: 'واژه‌نامه' },
      body: 'Terms.',
      type: 'term',
      summary: { en: 'Our terms.' },
      tags: ['terms'],
      keywords: ['glossary'],
      aliases: [],
      owner_team: null,
    });
  });

  it('says what is missing before asking the server', () => {
    expect(problems(emptyForm(['en']), true)).toHaveLength(3);
    expect(problems(fromRecord(record, ['en']), false)).toEqual([]);
    expect(problems({ ...fromRecord(record, ['en']), slug: 'Bad Slug' }, true)[0]).toMatch(/slug/);
  });

  it('counts bytes, not characters', () => {
    expect(byteLength('abc')).toBe(3);
    expect(byteLength('واژه')).toBe(8);
  });

  it('knows which languages read right to left', () => {
    expect(isRtl('fa')).toBe(true);
    expect(isRtl('ar-EG')).toBe(true);
    expect(isRtl('en')).toBe(false);
  });
});

describe('applying a generated draft', () => {
  const form = fromRecord(record, ['en', 'fa']);

  it('replaces summaries but never overwrites a title the operator wrote', () => {
    const next = applyDraft(form, {
      title: { en: 'A different title', fa: 'عنوان' },
      summary: { en: 'New summary.', fa: 'خلاصه' },
      keywords: [],
      aliases: [],
    });
    expect(next.title).toEqual({ en: 'Glossary', fa: 'واژه‌نامه' });
    expect(next.summary).toEqual({ en: 'New summary.', fa: 'خلاصه' });
  });

  it('fills a title that was left empty', () => {
    const next = applyDraft({ ...form, title: { en: 'Glossary', fa: '' } }, { title: { fa: 'واژه‌نامه' }, summary: {}, keywords: [], aliases: [] });
    expect(next.title.fa).toBe('واژه‌نامه');
  });

  it('adds keywords and aliases to the operator’s own', () => {
    const next = applyDraft(form, { title: {}, summary: {}, keywords: ['terms', 'vocabulary'], aliases: ['dictionary'] });
    expect(next.keywords).toBe('glossary, terms, vocabulary');
    expect(next.aliases).toBe('dictionary');
  });

  it('leaves a summary alone when the draft has none for that language', () => {
    const next = applyDraft(form, { title: {}, summary: { fa: '' }, keywords: [], aliases: [] });
    expect(next.summary).toEqual({ en: 'Our terms.', fa: '' });
  });
});

describe('routes into the editor', () => {
  it('opens a blank record, optionally seeded by a search nobody answered', () => {
    expect(parseFormsParam('new')).toEqual({ seed: '' });
    expect(parseFormsParam('new:hiring plan')).toEqual({ seed: 'hiring plan' });
    expect(parseFormsParam(null)).toBeNull();
    expect(parseFormsParam('glossary')).toBeNull();
  });
});

describe('the preview', () => {
  it('renders Markdown', () => {
    expect(renderMarkdown('# Title\n\n- one')).toContain('<h1');
  });

  it('cannot run anything: scripts, handlers and javascript links are stripped', () => {
    const html = renderMarkdown('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[x](javascript:alert(1))');
    expect(html).not.toMatch(/<script|onerror|javascript:/i);
    expect(html).toContain('<a');  // the link text survives, harmless
  });
});
