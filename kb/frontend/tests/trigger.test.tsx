import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { TriggerSyncModal } from '../src/features/sync/TriggerSyncModal';
import { mockFetch } from './helpers';

const sources = {
  config_error: null,
  sources: [
    { id: 'confluence-eng', kind: 'confluence', enabled: true },
    { id: 'jira-product', kind: 'jira', enabled: false },
  ],
};

afterEach(() => vi.unstubAllGlobals());

function open(initial = '*') {
  return render(<TriggerSyncModal initialSourceId={initial} onClose={vi.fn()} onOpenRun={vi.fn()} />);
}

describe('TriggerSyncModal', () => {
  it('queues exactly what was chosen', async () => {
    const { calls } = mockFetch({
      'GET /api/sources': { body: sources },
      'POST /api/sync': { status: 202, body: { request_id: 7 } },
      'GET /api/sync/requests': { body: { requests: [] } },
    });
    open('confluence-eng');
    await screen.findByRole('option', { name: /jira-product/ });

    await userEvent.click(screen.getByLabelText(/Full/));
    await userEvent.click(screen.getByLabelText(/Force re-summarization/));
    await userEvent.click(screen.getByRole('button', { name: /queue the sync/i }));

    await waitFor(() => expect(calls.find((c) => c.method === 'POST')).toBeTruthy());
    expect(calls.find((c) => c.method === 'POST')!.body).toEqual({
      source_id: 'confluence-eng', mode: 'full', dry_run: false, force: true,
    });
  });

  it('a dry run cannot be forced, and never sends force', async () => {
    const { calls } = mockFetch({
      'GET /api/sources': { body: sources },
      'POST /api/sync': { status: 202, body: { request_id: 7 } },
      'GET /api/sync/requests': { body: { requests: [] } },
    });
    open('confluence-eng');
    await screen.findByRole('option', { name: /jira-product/ });

    await userEvent.click(screen.getByLabelText(/Force re-summarization/));
    await userEvent.click(screen.getByLabelText(/Dry run/));
    expect(screen.getByLabelText(/Force re-summarization/)).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /queue a dry run/i }));

    await waitFor(() => expect(calls.find((c) => c.method === 'POST')).toBeTruthy());
    expect(calls.find((c) => c.method === 'POST')!.body).toMatchObject({ dry_run: true, force: false });
  });

  it('explains why a disabled source can still be run by hand', async () => {
    mockFetch({ 'GET /api/sources': { body: sources } });
    open('jira-product');
    expect(await screen.findByText(/This source is disabled/)).toBeInTheDocument();
  });

  it('shows what the server refused', async () => {
    mockFetch({
      'GET /api/sources': { body: sources },
      'POST /api/sync': { status: 400, body: { error: { type: 'invalid_request', message: 'no source with id "x"' } } },
    });
    open('confluence-eng');
    await screen.findByRole('option', { name: /jira-product/ });
    await userEvent.click(screen.getByRole('button', { name: /queue the sync/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('no source with id "x"');
  });

  it('follows the request from queued to finished and links its report', async () => {
    let stage = 0;
    mockFetch({
      'GET /api/sources': { body: sources },
      'POST /api/sync': { status: 202, body: { request_id: 7 } },
      'GET /api/sync/requests': () => {
        stage += 1;
        const base = { id: 7, kind: 'sync', source_id: 'confluence-eng', mode: 'full', dry_run: false, forced: false, run_ids: [] as number[], error: null, position: null };
        return { body: { requests: [stage < 2 ? { ...base, status: 'queued', position: 1 } : { ...base, status: 'done', run_ids: [3] }] } };
      },
      'GET /api/runs/3': {
        body: { id: 3, source_id: 'confluence-eng', mechanism: 'full_scrape', status: 'success', seen: 5, created: 5, updated: 0, missing: 0, content_stored: 0, rejected_count: 0, duration_ms: 1200, rejected: [], checkpoint: null },
      },
    });
    open('confluence-eng');
    await screen.findByRole('option', { name: /jira-product/ });
    await userEvent.click(screen.getByRole('button', { name: /queue the sync/i }));

    expect(await screen.findByText(/next in line/)).toBeInTheDocument();
    expect(await screen.findByText('Finished.', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(await screen.findByText(/Open the full report/)).toBeInTheDocument();
  });
});
