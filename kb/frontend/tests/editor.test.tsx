import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { SourceCard, SourcesResponse } from '../src/api/types';
import { SourceEditorModal } from '../src/features/sources/SourceEditorModal';
import { mockFetch } from './helpers';

afterEach(() => vi.unstubAllGlobals());

const data: SourcesResponse = {
  config_error: null,
  editable: true,
  orphans: [],
  sources: [],
  defaults: { mechanisms: { incremental: { enabled: true, every: '15m' }, full_scrape: { enabled: true, at: '03:00' } }, raw_content_max_bytes: 10485760 },
};

const PREVIEW = { action: 'create', source_id: 'x', summary: 's', diff: '', changed: true, warnings: [], etag: 'e' };

describe('SourceEditorModal', () => {
  it('adds a source: the id and the query go to the server as a preview, not as a write', async () => {
    const { calls } = mockFetch({ 'POST /api/sources/preview': { body: PREVIEW } });
    const onPreview = vi.fn();
    render(<SourceEditorModal source={null} data={data} onPreview={onPreview} onClose={vi.fn()} />);

    await userEvent.selectOptions(screen.getByLabelText('Kind'), 'jira');
    await userEvent.type(screen.getByLabelText('Id'), 'jira-ops');
    await userEvent.type(screen.getByLabelText('JQL'), 'project = OPS');
    await userEvent.click(screen.getByRole('button', { name: /review the change/i }));

    await waitFor(() => expect(onPreview).toHaveBeenCalled());
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(['POST /api/sources/preview']);
    expect(calls[0]!.body).toEqual({
      change: { action: 'create', config: { id: 'jira-ops', kind: 'jira', enabled: false, store_raw_content: false, jql: 'project = OPS' } },
    });
  });

  it('cannot be reviewed while something obvious is missing, and says what', async () => {
    mockFetch({});
    render(<SourceEditorModal source={null} data={data} onPreview={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole('button', { name: /review the change/i })).toBeDisabled();
    expect(screen.getByText(/Give the source an id/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Id'), 'form');
    expect(screen.getByText(/reserved for the manual records/)).toBeInTheDocument();
  });

  it('keeps stored text off, and explains who could read it when it is turned on', async () => {
    mockFetch({});
    render(<SourceEditorModal source={null} data={data} onPreview={vi.fn()} onClose={vi.fn()} />);
    const toggle = screen.getByRole('switch', { name: /keep each document's text/i });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText(/every user of the catalog/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Largest document kept/)).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.getByLabelText(/Largest document kept/)).toBeInTheDocument();
  });

  it('shows the server\'s reason when it refuses the change', async () => {
    mockFetch({ 'POST /api/sources/preview': { status: 400, body: { error: { type: 'invalid_change', message: 'there is already a source called "jira-ops"' } } } });
    render(<SourceEditorModal source={null} data={data} onPreview={vi.fn()} onClose={vi.fn()} />);
    await userEvent.selectOptions(screen.getByLabelText('Kind'), 'jira');
    await userEvent.type(screen.getByLabelText('Id'), 'jira-ops');
    await userEvent.type(screen.getByLabelText('JQL'), 'x');
    await userEvent.click(screen.getByRole('button', { name: /review the change/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already a source called');
  });

  it('editing: the id and kind are fixed, and an unchanged form changes nothing it did not touch', async () => {
    const source: SourceCard = {
      id: 'confluence-eng', kind: 'confluence', enabled: true, mechanisms: { incremental: { enabled: true, every: '15m' } },
      own_mechanisms: {}, store_raw_content: false, raw_content_max_bytes: 10485760, own_raw_content_max_bytes: null,
      settings: { spaces: ['ENG'] }, entries: 3, content: { documents: 0, text_bytes: 0 }, last_run: null, credentials: {}, origin: 'yaml',
    };
    const { calls } = mockFetch({ 'POST /api/sources/preview': { body: PREVIEW } });
    render(<SourceEditorModal source={source} data={data} onPreview={vi.fn()} onClose={vi.fn()} />);

    expect(screen.getByLabelText('Id')).toBeDisabled();
    expect(screen.getByLabelText('Kind')).toBeDisabled();
    expect(screen.getByLabelText('Spaces')).toHaveValue('ENG');
    // Inherited mechanisms show what they would be, and are not pinned by saving.
    expect(screen.getByLabelText('Incremental: on or off')).toHaveValue('inherit');

    await userEvent.click(screen.getByRole('button', { name: /review the change/i }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0]!.body).toEqual({
      change: { action: 'update', id: 'confluence-eng', config: { enabled: true, store_raw_content: false, raw_content_max_bytes: null, mechanisms: {}, spaces: ['ENG'], exclude_labels: [], type_from_labels: {} } },
    });
  });
});
