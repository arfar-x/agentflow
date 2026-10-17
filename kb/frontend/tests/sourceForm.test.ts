import type { SourceCard } from '../src/api/types';
import { blankForm, blankProject, blankScope, csv, formFromCard, formToConfig, problems } from '../src/lib/sourceForm';

function card(over: Partial<SourceCard>): SourceCard {
  return {
    id: 'confluence-eng', kind: 'confluence', enabled: true, mechanisms: {}, own_mechanisms: {}, store_raw_content: false,
    raw_content_max_bytes: 10485760, own_raw_content_max_bytes: null, settings: {}, entries: 0,
    content: { documents: 0, text_bytes: 0 }, last_run: null, credentials: {}, origin: 'yaml', ...over,
  };
}

describe('csv', () => {
  it('splits on commas and newlines and drops blanks', () => {
    expect(csv(' ENG, PRODUCT ,,\nBIZ ')).toEqual(['ENG', 'PRODUCT', 'BIZ']);
    expect(csv('')).toEqual([]);
  });
});

describe('a new source', () => {
  it('starts disabled and never shares its text by default', () => {
    const form = blankForm('jira');
    expect(form.enabled).toBe(false);
    expect(form.storeRaw).toBe(false);
  });

  it('is sent without the settings the form left empty', () => {
    const form = { ...blankForm('confluence'), id: ' wiki-ops ', spaces: 'OPS, SRE' };
    expect(formToConfig(form, 'create')).toEqual({
      id: 'wiki-ops', kind: 'confluence', enabled: false, store_raw_content: false, spaces: ['OPS', 'SRE'],
    });
  });

  it('carries a jira query, and a gitlab token as a variable NAME', () => {
    expect(formToConfig({ ...blankForm('jira'), id: 'j', jql: ' project = X ' }, 'create')).toMatchObject({ jql: 'project = X' });
    const gitlab = { ...blankForm('gitlab'), id: 'g', baseUrl: 'https://gl', projects: [{ ...blankProject(), path: 'a/b', scopes: [{ ...blankScope(), dir: 'docs', type: 'spec', tags: 'adr, x' }] }] };
    expect(formToConfig(gitlab, 'create')).toMatchObject({
      base_url: 'https://gl',
      token_env: 'GITLAB_TOKEN',
      projects: [{ path: 'a/b', scopes: [{ dir: 'docs', type: 'spec', tags: ['adr', 'x'] }] }],
    });
  });
});

describe('mechanisms', () => {
  it('sends only what the source itself sets, so defaults stay inherited', () => {
    const form = blankForm('jira');
    form.mechanisms.incremental = { enabled: 'inherit', every: '5m', at: '', stale_after: '' };
    form.mechanisms.full_scrape = { enabled: 'off', every: '', at: '', stale_after: '' };
    const config = formToConfig({ ...form, id: 'j', jql: 'x' }, 'create') as { mechanisms: unknown };
    expect(config.mechanisms).toEqual({ incremental: { every: '5m' }, full_scrape: { enabled: false } });
  });

  it('sends nothing when everything is inherited', () => {
    expect(formToConfig({ ...blankForm('jira'), id: 'j', jql: 'x' }, 'create')).not.toHaveProperty('mechanisms');
  });
});

describe('editing an existing source', () => {
  it('starts from what the file says, not from the defaults it inherits', () => {
    const form = formFromCard(card({
      settings: { spaces: ['ENG'], exclude_labels: ['archive'], type_from_labels: { 'kb-glossary': 'term' } },
      own_mechanisms: { incremental: { every: '5m' }, webhook: { enabled: false } },
      mechanisms: { incremental: { enabled: true, every: '5m' }, full_scrape: { enabled: true, at: '03:00' } },
      store_raw_content: true,
      own_raw_content_max_bytes: 2048,
    }));
    expect(form.spaces).toBe('ENG');
    expect(form.labelTypes).toEqual([{ label: 'kb-glossary', type: 'term' }]);
    expect(form.mechanisms.incremental).toMatchObject({ enabled: 'inherit', every: '5m' });
    expect(form.mechanisms.full_scrape.enabled).toBe('inherit');
    expect(form.mechanisms.webhook.enabled).toBe('off');
    expect(form.storeRaw).toBe(true);
    expect(form.maxBytes).toBe('2048');
  });

  it('an untouched form sends back exactly what the file already says', () => {
    const source = card({ settings: { spaces: ['ENG'] }, own_mechanisms: { incremental: { every: '5m' } } });
    const config = formToConfig(formFromCard(source), 'update');
    expect(config).toMatchObject({ enabled: true, store_raw_content: false, spaces: ['ENG'], mechanisms: { incremental: { every: '5m' } } });
    expect(config).not.toHaveProperty('id');
  });

  it('a field the person emptied is sent empty, which clears it', () => {
    const form = formFromCard(card({ settings: { spaces: ['ENG'], exclude_labels: ['archive'] } }));
    form.excludeLabels = '';
    expect(formToConfig(form, 'update')).toMatchObject({ exclude_labels: [], type_from_labels: {} });
  });
});

describe('problems', () => {
  it('catches the obvious mistakes before a round trip', () => {
    expect(problems(blankForm('confluence'), 'create')).toEqual(['Give the source an id.', 'List at least one space.']);
    expect(problems({ ...blankForm('jira'), id: 'form', jql: 'x' }, 'create')).toEqual(["'form' is reserved for the manual records."]);
    expect(problems({ ...blankForm('jira'), id: 'has space', jql: 'x' }, 'create')[0]).toMatch(/letters, digits/);
    expect(problems({ ...blankForm('jira'), id: 'j' }, 'create')).toEqual(['Write the JQL that selects the issues to catalogue.']);
  });

  it('refuses a secret where a variable name belongs', () => {
    const form = { ...blankForm('gitlab'), id: 'g', baseUrl: 'https://gl', tokenEnv: 'glpat-abc123' };
    expect(problems(form, 'create').join(' ')).toMatch(/NAME of an environment variable/);
  });

  it('wants a sensible text cap, and an id is not asked for when editing', () => {
    expect(problems({ ...blankForm('jira'), id: 'j', jql: 'x', maxBytes: '10' }, 'create').join(' ')).toMatch(/at least 1024/);
    expect(problems({ ...blankForm('jira'), jql: 'x' }, 'update')).toEqual([]);
  });
});
