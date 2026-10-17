import * as format from '../src/lib/format';

const { formatBytes, formatDuration, humanize, percent, relativeTime } = format;

describe('formatBytes', () => {
  it('reads like a person would', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(19200)).toBe('18.75 KB');
    expect(formatBytes(10 * 1024 * 1024)).toBe('10 MB');
  });
});

describe('formatDuration', () => {
  it('scales its unit', () => {
    expect(formatDuration(null)).toBe('—');
    expect(formatDuration(850)).toBe('850 ms');
    expect(formatDuration(2000)).toBe('2.0 s');
    expect(formatDuration(94_000)).toBe('1 min 34 s');
    expect(formatDuration(3_900_000)).toBe('1 h 5 min');
  });
});

describe('relativeTime', () => {
  const now = '2026-10-14T12:00:00Z';
  it('says how long ago or how long until, against the given clock', () => {
    expect(relativeTime('2026-10-14T11:55:00Z', now)).toBe('5 min ago');
    expect(relativeTime('2026-10-14T12:12:00Z', now)).toBe('in 12 min');
    expect(relativeTime('2026-10-14T12:00:02Z', now)).toBe('now');
    expect(relativeTime('2026-10-14T09:00:00Z', now)).toBe('3 h ago');
    expect(relativeTime('2026-10-16T12:00:00Z', now)).toBe('in 2 d');
    expect(relativeTime(null, now)).toBe('—');
  });
});

describe('small helpers', () => {
  it('formats a rate, and does not invent one', () => {
    expect(percent(0.8)).toBe('80%');
    expect(percent(null)).toBe('—');
  });
  it('humanizes identifiers', () => {
    expect(humanize('full_scrape')).toBe('Full scrape');
  });
});
