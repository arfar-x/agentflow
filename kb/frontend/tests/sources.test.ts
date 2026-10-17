import type { SourceCard } from '../src/api/types';
import { credentialsMissing, describeMechanisms, describeSettings } from '../src/lib/sources';

function card(over: Partial<SourceCard>): SourceCard {
  return {
    id: 's', kind: 'confluence', enabled: true, mechanisms: {}, store_raw_content: false,
    raw_content_max_bytes: 10, settings: {}, entries: 0, content: { documents: 0, text_bytes: 0 },
    last_run: null, credentials: {}, origin: 'yaml', ...over,
  };
}

describe('credentialsMissing', () => {
  it('wants a base URL and either a token or a username with a password', () => {
    const jira = (vars: Record<string, boolean>) => credentialsMissing(card({ kind: 'jira', credentials: vars }));
    expect(jira({ KB_JIRA_BASE_URL: true, KB_JIRA_PAT: true })).toEqual([]);
    expect(jira({ JIRA_BASE_URL: true, JIRA_USERNAME: true, JIRA_PASSWORD: true })).toEqual([]);
    expect(jira({ KB_JIRA_BASE_URL: true, KB_JIRA_USERNAME: true })).toEqual(['KB_JIRA_PAT (or KB_JIRA_USERNAME + KB_JIRA_PASSWORD)']);
    expect(jira({ KB_JIRA_PAT: true })).toEqual(['KB_JIRA_BASE_URL']);
  });

  it('wants the one variable a GitLab source names', () => {
    expect(credentialsMissing(card({ kind: 'gitlab', credentials: { GITLAB_TOKEN: false } }))).toEqual(['GITLAB_TOKEN']);
    expect(credentialsMissing(card({ kind: 'gitlab', credentials: { GITLAB_TOKEN: true } }))).toEqual([]);
  });

  it('says nothing when the scheduler has not reported yet', () => {
    expect(credentialsMissing(card({ kind: 'jira', credentials: {} }))).toEqual([]);
  });
});

describe('describing a source', () => {
  it('shows what matters for each kind', () => {
    expect(describeSettings(card({ settings: { spaces: ['ENG', 'BIZ'], exclude_labels: ['draft'] } }))).toEqual([
      ['Spaces', 'ENG, BIZ'],
      ['Excluded labels', 'draft'],
    ]);
    expect(describeSettings(card({ kind: 'jira', settings: { jql: 'project = X' } }))).toEqual([['JQL', 'project = X']]);
    expect(describeSettings(card({ kind: 'gitlab', settings: { base_url: 'https://g', projects: [{ path: 'a/b' }] } }))).toEqual([
      ['Base URL', 'https://g'],
      ['Projects', 'a/b'],
    ]);
  });

  it('lists every mechanism, off ones included', () => {
    const rows = describeMechanisms({ incremental: { enabled: true, every: '15m' }, full_scrape: { enabled: false } });
    expect(rows.map((r) => [r.name, r.on, r.detail])).toEqual([
      ['Incremental', true, 'every 15m'],
      ['Nightly full', false, ''],
      ['Webhook', false, ''],
      ['Lazy refresh', false, ''],
    ]);
  });
});
