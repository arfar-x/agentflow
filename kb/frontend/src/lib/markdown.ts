import DOMPurify from 'dompurify';
import { marked } from 'marked';

/**
 * The preview of a record's text. Sanitized, because a record is content the
 * console renders as HTML: whatever a record contains (an operator pasted it,
 * or it came from a page) must not be able to run in the operator's session.
 */
export function renderMarkdown(text: string): string {
  const html = marked.parse(text, { async: false, gfm: true, breaks: false }) as string;
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
}
