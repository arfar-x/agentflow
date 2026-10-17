import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { FormRecord } from '../src/api/types';
import { ToastProvider } from '../src/components/ui/Toast';
import { FormEditorModal } from '../src/features/forms/FormEditorModal';
import { mockFetch } from './helpers';

afterEach(() => vi.unstubAllGlobals());

const saved: FormRecord = {
  slug: 'release-process',
  title: { en: 'Release process', fa: '' },
  body: 'Releases go out on Tuesdays.',
  type: 'doc',
  summary: {},
  tags: [],
  keywords: [],
  aliases: [],
  owner_team: null,
  revision: 1,
  created_at: null,
  updated_at: null,
  publication: 'pending',
};

function open(props: Partial<React.ComponentProps<typeof FormEditorModal>> = {}) {
  const onSaved = vi.fn();
  render(
    <ToastProvider>
      <FormEditorModal
        record={null}
        languages={['en', 'fa']}
        entryTypes={['doc', 'spec']}
        summarizerConfigured
        maxBytes={1024}
        onClose={vi.fn()}
        onSaved={onSaved}
        {...props}
      />
    </ToastProvider>,
  );
  return { onSaved };
}

describe('FormEditorModal', () => {
  it('tells the operator, before anything else, who will be able to read the record', () => {
    open();
    expect(screen.getByText(/every catalog user/i)).toBeInTheDocument();
  });

  it('cannot save until it has a title, a slug and text, and then sends exactly the record', async () => {
    const { calls } = mockFetch({ 'POST /api/forms': { status: 201, body: { record: saved } } });
    const { onSaved } = open();
    const save = screen.getByRole('button', { name: /save and publish/i });
    expect(save).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/^Title/), 'Release process');
    expect(screen.getByLabelText('Slug')).toHaveValue('release-process'); // follows the title
    await userEvent.type(screen.getByLabelText('Text'), 'Releases go out on Tuesdays.');
    expect(save).toBeEnabled();
    await userEvent.click(save);

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls.find((c) => c.method === 'POST')!.body).toEqual({
      slug: 'release-process',
      title: { en: 'Release process' },
      body: 'Releases go out on Tuesdays.',
      type: 'doc',
      summary: {},
      tags: [],
      keywords: [],
      aliases: [],
      owner_team: null,
    });
  });

  it('an existing record keeps its slug and is saved with PUT', async () => {
    const { calls } = mockFetch({ 'PUT /api/forms/release-process': { body: { record: { ...saved, revision: 2 } } } });
    const { onSaved } = open({ record: saved });
    expect(screen.getByLabelText('Slug')).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls[0]!.method).toBe('PUT');
  });

  it('shows the server’s reason when it refuses', async () => {
    mockFetch({ 'PUT /api/forms/release-process': { status: 400, body: { error: { type: 'invalid_record', message: 'body: Input should have at least 1 character' } } } });
    open({ record: saved });
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/at least 1 character/);
  });

  it('a generated summary is a draft: it fills the fields, can be undone, and saves nothing', async () => {
    const { calls } = mockFetch({
      'POST /api/forms/draft-summary': {
        body: { draft: { title: { en: 'x' }, summary: { en: 'How releases ship.' }, keywords: ['release'], aliases: [] }, usable: true },
      },
    });
    open({ record: saved });
    await userEvent.click(screen.getByRole('button', { name: /generate summary/i }));

    expect(await screen.findByLabelText(/^Summary/)).toHaveValue('How releases ship.');
    expect(screen.getByRole('status')).toHaveTextContent(/nothing is saved/i);
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(['POST /api/forms/draft-summary']);

    await userEvent.click(screen.getByRole('button', { name: /undo/i }));
    expect(screen.getByLabelText(/^Summary/)).toHaveValue('');
  });

  it('a draft the model could not make is reported, not applied', async () => {
    mockFetch({ 'POST /api/forms/draft-summary': { body: { draft: { title: {}, summary: {}, keywords: [], aliases: [] }, usable: false } } });
    open({ record: saved });
    await userEvent.click(screen.getByRole('button', { name: /generate summary/i }));
    expect(await screen.findByRole('status')).toHaveTextContent(/nothing usable/i);
    expect(screen.getByLabelText(/^Summary/)).toHaveValue('');
  });

  it('without a summarizer the button is disabled and says why', () => {
    open({ record: saved, summarizerConfigured: false });
    expect(screen.getByRole('button', { name: /generate summary/i })).toBeDisabled();
    expect(screen.getByText(/written by hand/i)).toBeInTheDocument();
  });

  it('a Persian tab is laid out right to left', async () => {
    open({ record: saved });
    await userEvent.click(screen.getByRole('tab', { name: /fa/ }));
    expect(screen.getByLabelText(/^Title/)).toHaveAttribute('dir', 'rtl');
    await userEvent.click(screen.getByRole('tab', { name: /en/ }));
    expect(screen.getByLabelText(/^Title/)).toHaveAttribute('dir', 'ltr');
  });

  it('counts the text in bytes and warns past the cap', async () => {
    open({ record: saved, maxBytes: 10 });
    expect(screen.getByText(/of 10 B/)).toHaveTextContent(/the rest is cut/i);
  });

  it('previews the Markdown without running anything in it', async () => {
    open({ record: { ...saved, body: '# Heading\n\n<img src=x onerror=alert(1)>' } });
    await userEvent.click(screen.getByRole('button', { name: /preview/i }));
    const preview = screen.getByLabelText('Preview');
    expect(preview.querySelector('h1')).not.toBeNull();
    expect(preview.innerHTML).not.toMatch(/onerror/);
  });
});
