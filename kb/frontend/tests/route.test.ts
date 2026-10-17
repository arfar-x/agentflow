import { parseHash } from '../src/lib/route';

describe('parseHash', () => {
  it('defaults to the overview', () => {
    expect(parseHash('')).toEqual({ name: 'overview', param: null });
    expect(parseHash('#/')).toEqual({ name: 'overview', param: null });
  });
  it('reads a screen and an optional id', () => {
    expect(parseHash('#/sources')).toEqual({ name: 'sources', param: null });
    expect(parseHash('#/reports/12')).toEqual({ name: 'reports', param: '12' });
    expect(parseHash('#/forms/adr%20one')).toEqual({ name: 'forms', param: 'adr one' });
  });
});
