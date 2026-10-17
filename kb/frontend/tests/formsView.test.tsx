import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ConsoleProvider } from '../src/components/ConsoleContext';
import { ToastProvider } from '../src/components/ui/Toast';
import { ContentInspector } from '../src/features/forms/ContentInspector';
import { FormsView } from '../src/features/forms/FormsView';
import { mockFetch } from './helpers';

afterEach(() => vi.unstubAllGlobals());

const record = (slug: string, publication: 'published' | 'pending', summary: Record<string, string> = { en: 's' }) => ({
  slug,
  title: { en: `Title of ${slug}` },
  body: 'text',
  type: 'doc',
  summary,
  tags: [],
  keywords: [],
  aliases: [],
  owner_team: 'platform',
  revision: 2,
  created_at: null,
  updated_at: '2026-10-15T09:00:00Z',
  publication,
});

const routes = (records = [record('glossary', 'published'), record('release', 'pending', {})]) => ({
  'GET /api/forms': { body: { records, languages: ['en'], summarizer_configured: true, max_bytes: 1024 } },
  'GET /api/overview': { body: { now: '2026-10-15T09:05:00Z', health: 'healthy', problems: [], queue: { queued: 0, running: 0 } } },
  'GET /api/config': { body: { languages: ['en'], entry_types: ['doc', 'spec'], summarizer_configured: true } },
});

function view(param: string | null = null) {
  const navigate = vi.fn();
  render(
    <ToastProvider>
      <ConsoleProvider>
        <FormsView param={param} onNavigate={navigate} />
      </ConsoleProvider>
    </ToastProvider>,
  );
  return { navigate };
}

describe('FormsView', () => {
  it('lists the records with what the catalog says about each', async () => {
    mockFetch(routes());
    view();
    expect(await screen.findByText('Title of glossary')).toBeInTheDocument();
    const pending = screen.getByText('Title of release').closest('tr')!;
    expect(within(pending).getByText(/waiting for the scheduler/i)).toBeInTheDocument();
    expect(within(pending).getByText(/no summary/i)).toBeInTheDocument();
    expect(within(screen.getByText('Title of glossary').closest('tr')!).getByText('published')).toBeInTheDocument();
  });

  it('opens a blank record from a search nobody answered', async () => {
    mockFetch(routes());
    const { navigate } = view('new:hiring plan');
    expect(await screen.findByLabelText('Slug')).toHaveValue('hiring-plan');
    expect(screen.getByLabelText(/^Title/)).toHaveValue('hiring plan');
    expect(navigate).toHaveBeenCalledWith('forms'); // the address is cleaned up
  });

  it('deleting asks first, says what happens, and sends the delete', async () => {
    const { calls } = mockFetch({ ...routes(), 'DELETE /api/forms/glossary': { body: { deleted: 'glossary' } } });
    view();
    await userEvent.click(await screen.findByRole('button', { name: 'Delete glossary' }));
    expect(screen.getByText(/stays searchable until the/i)).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: /delete the record/i }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
  });

  it('says so when there is nothing yet', async () => {
    mockFetch(routes([]));
    view();
    expect(await screen.findByText(/No manual records yet/i)).toBeInTheDocument();
  });
});

describe('ContentInspector', () => {
  const page = (offset: number, text: string, next: number | null) => ({
    body: {
      slug: 'glossary',
      publication: 'published',
      content: { text, offset, total_chars: 30, next_offset: next, truncated: false, original_bytes: 30 },
      entry: null,
    },
  });

  it('pages the way kb_get does, by the offsets the server hands back', async () => {
    const { calls } = mockFetch({
      'GET /api/forms/glossary/content': (call) => (call.path.includes('offset=20') ? page(20, 'second page', null) : page(0, 'first page', 20)),
    });
    render(<ContentInspector slug="glossary" onClose={vi.fn()} />);
    expect(await screen.findByText('first page')).toBeInTheDocument();
    expect(screen.getByText(/next_offset: 20/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /previous page/i })).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: /next page/i }));
    expect(await screen.findByText('second page')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /next page/i })).toBeDisabled();
    expect(calls.at(-1)!.path).toBe('/api/forms/glossary/content?offset=20');

    await userEvent.click(screen.getByRole('button', { name: /previous page/i }));
    expect(await screen.findByText('first page')).toBeInTheDocument();
  });

  it('explains a record the catalog does not have yet', async () => {
    mockFetch({ 'GET /api/forms/glossary/content': { body: { slug: 'glossary', publication: 'pending', content: null, entry: null } } });
    render(<ContentInspector slug="glossary" onClose={vi.fn()} />);
    expect(await screen.findByText(/Not in the catalog yet/i)).toBeInTheDocument();
  });
});
