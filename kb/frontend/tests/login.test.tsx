import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { LoginScreen } from '../src/components/LoginScreen';
import { mockFetch } from './helpers';

afterEach(() => vi.unstubAllGlobals());

describe('LoginScreen', () => {
  it('logs in with the token and lets the app in', async () => {
    const { calls } = mockFetch({ 'POST /api/session': { body: { authenticated: true } } });
    const onLoggedIn = vi.fn();
    render(<LoginScreen onLoggedIn={onLoggedIn} />);

    await userEvent.type(screen.getByLabelText('Admin token'), '  s3cret-token  ');
    await userEvent.click(screen.getByRole('button', { name: /log in/i }));

    await waitFor(() => expect(onLoggedIn).toHaveBeenCalled());
    expect(calls[0]!.body).toEqual({ token: 's3cret-token' });
  });

  it('says so plainly when the token is wrong, and does not log in', async () => {
    mockFetch({ 'POST /api/session': { status: 401, body: { error: { type: 'unauthorized', message: 'x' } } } });
    const onLoggedIn = vi.fn();
    render(<LoginScreen onLoggedIn={onLoggedIn} />);

    await userEvent.type(screen.getByLabelText('Admin token'), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: /log in/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('That is not the admin token.');
    expect(onLoggedIn).not.toHaveBeenCalled();
  });

  it('will not submit an empty token', () => {
    mockFetch({});
    render(<LoginScreen onLoggedIn={() => undefined} />);
    expect(screen.getByRole('button', { name: /log in/i })).toBeDisabled();
  });
});
