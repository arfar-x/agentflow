import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { ChangePreview, SourceChange } from '../src/api/types';
import { ApprovalDialog } from '../src/features/sources/ApprovalDialog';
import { mockFetch } from './helpers';

afterEach(() => vi.unstubAllGlobals());

const change: SourceChange = { action: 'update', id: 'wiki', config: { store_raw_content: true } };
const preview: ChangePreview = {
  action: 'update',
  source_id: 'wiki',
  summary: "Change the source 'wiki'",
  changed: true,
  etag: 'abc123',
  diff: '--- kb-sources.yaml (now)\n+++ kb-sources.yaml (after)\n@@ -3,1 +3,2 @@\n     enabled: true\n+    store_raw_content: true\n',
  warnings: [{ level: 'danger', text: 'Turning on stored text makes every document readable by every user of the catalog.' }],
};

function open(over: Partial<ChangePreview> = {}, onApplied = vi.fn(), onClose = vi.fn()) {
  render(<ApprovalDialog change={change} preview={{ ...preview, ...over }} onApplied={onApplied} onClose={onClose} />);
  return { onApplied, onClose };
}

describe('ApprovalDialog', () => {
  it('shows the warnings in words and the diff, and writes nothing yet', () => {
    const { calls } = mockFetch({});
    open();
    expect(screen.getByText(/readable by every user of the catalog/)).toBeInTheDocument();
    expect(screen.getByLabelText('Changes to the file')).toHaveTextContent(/\+\s+store_raw_content: true/);
    expect(calls).toEqual([]);
  });

  it('will not write until the person says they have read it', async () => {
    mockFetch({ 'POST /api/sources/apply': { body: { changed: true, etag: 'z', revision_id: 1 } } });
    open();
    const write = screen.getByRole('button', { name: /write the change/i });
    expect(write).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/I have read what this does/));
    expect(write).toBeEnabled();
  });

  it('sends the previews etag with an explicit confirmation, then tells the screen', async () => {
    const { calls } = mockFetch({ 'POST /api/sources/apply': { body: { changed: true, etag: 'z', revision_id: 1 } } });
    const { onApplied } = open();
    await userEvent.click(screen.getByLabelText(/I have read what this does/));
    await userEvent.click(screen.getByRole('button', { name: /write the change/i }));

    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(calls[0]!.body).toEqual({ change, etag: 'abc123', confirm: true });
  });

  it('shows why a write was refused, and stays open', async () => {
    mockFetch({ 'POST /api/sources/apply': { status: 409, body: { error: { type: 'conflict', message: 'The file changed after you previewed this.' } } } });
    const { onApplied } = open();
    await userEvent.click(screen.getByLabelText(/I have read what this does/));
    await userEvent.click(screen.getByRole('button', { name: /write the change/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('The file changed after you previewed this.');
    expect(onApplied).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /write the change/i })).toBeEnabled();
  });

  it('offers nothing to write when the change would change nothing', () => {
    open({ changed: false, diff: '', warnings: [] });
    expect(screen.getByText(/would not change the file/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /write the change/i })).not.toBeInTheDocument();
  });

  it('cancelling changes nothing', async () => {
    const { calls } = mockFetch({});
    const { onClose } = open();
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onClose).toHaveBeenCalled();
    expect(calls).toEqual([]);
  });
});
