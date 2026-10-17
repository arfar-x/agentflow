import { BookOpen } from 'lucide-react';
import { useState } from 'react';

import { api } from '../../api/api';
import { Badge } from '../../components/ui/Badge';
import { Loading, LoadError, Notice } from '../../components/ui/Page';
import { Modal } from '../../components/ui/Modal';
import { button } from '../../components/ui/styles';
import { useResource } from '../../lib/useResource';

/**
 * A record the way an agent reads it: the same pages, offsets and totals
 * `kb_get` returns (FR-ADM-14), so "what will the model actually see?" has an
 * answer that is not a guess.
 */
export function ContentInspector({ slug, onClose }: { slug: string; onClose: () => void }) {
  // The offsets already visited, so "previous" needs no arithmetic on page size.
  const [trail, setTrail] = useState<number[]>([0]);
  const offset = trail[trail.length - 1];
  const result = useResource(() => api.formContent(slug, offset), [slug, offset]);
  const page = result.data?.content ?? null;

  return (
    <Modal
      title={`Read ${slug} as an agent`}
      subtitle="What kb_get returns for this record, a page at a time."
      icon={<BookOpen className="h-5 w-5" />}
      width="max-w-3xl"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={button.secondary} disabled={trail.length < 2} onClick={() => setTrail(trail.slice(0, -1))}>
            Previous page
          </button>
          <button
            type="button"
            className={button.secondary}
            disabled={page?.next_offset == null}
            onClick={() => page?.next_offset != null && setTrail([...trail, page.next_offset])}
          >
            Next page
          </button>
          <button type="button" className={button.primary} onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      {!result.data ? (
        result.error ? <LoadError error={result.error} onRetry={result.reload} /> : <Loading />
      ) : page ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
            <Badge tone={result.data.publication === 'published' ? 'emerald' : 'amber'}>{result.data.publication}</Badge>
            <span>
              characters {page.offset.toLocaleString()}–{(page.offset + page.text.length).toLocaleString()} of {page.total_chars.toLocaleString()}
            </span>
            <span>next_offset: {page.next_offset ?? 'none'}</span>
            {page.truncated ? <Badge tone="amber">cut when kept ({page.original_bytes.toLocaleString()} bytes originally)</Badge> : null}
          </div>
          {result.data.publication === 'pending' ? (
            <Notice tone="amber">There are unpublished changes: an agent still reads the text below until the scheduler picks the new revision up.</Notice>
          ) : null}
          <pre dir="auto" className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-800 bg-slate-950 p-4 font-mono text-xs leading-relaxed text-slate-200">
            {page.text}
          </pre>
        </div>
      ) : (
        <Notice tone="amber">
          Not in the catalog yet. A saved record is published by the scheduler, usually within a few seconds; until then there is nothing for an agent to read.
        </Notice>
      )}
    </Modal>
  );
}
