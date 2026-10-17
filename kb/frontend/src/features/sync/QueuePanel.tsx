import { ListOrdered, X } from 'lucide-react';

import { api } from '../../api/api';
import type { RequestStatus, SyncRequestView } from '../../api/types';
import { Badge } from '../../components/ui/Badge';
import { card, sectionTitle } from '../../components/ui/styles';
import { useToast } from '../../components/ui/Toast';
import { useResource } from '../../lib/useResource';

const ACTIVE: RequestStatus[] = ['queued', 'running'];

export function describeRequest(request: SyncRequestView): string {
  if (request.kind === 'purge_content') return `Purge stored text · ${request.source_id}`;
  const target = request.source_id === '*' ? 'all enabled sources' : request.source_id;
  const flags = [request.mode, request.dry_run ? 'dry run' : null, request.forced ? 'forced' : null].filter(Boolean);
  return `Sync ${target} (${flags.join(', ')})`;
}

/** What has been asked of the scheduler and has not finished. */
export function QueuePanel() {
  const toast = useToast();
  const requests = useResource(() => api.syncRequests(ACTIVE), [], 3000);
  const list = requests.data?.requests ?? [];

  const cancel = async (id: number) => {
    try {
      await api.cancelRequest(id);
      toast('Request cancelled.');
    } catch (err) {
      toast((err as Error).message, 'error');
    }
    requests.reload();
  };

  return (
    <div className={`${card} p-5`}>
      <h2 className={`${sectionTitle} mb-3 flex items-center gap-2`}>
        <ListOrdered className="h-4 w-4 text-slate-400" /> Waiting for the scheduler
      </h2>
      {list.length ? (
        <ul className="space-y-2">
          {list.map((request) => (
            <li key={request.id} className="flex items-center justify-between gap-2 rounded-lg bg-slate-950/60 p-2.5 text-xs">
              <span className="text-slate-200">{describeRequest(request)}</span>
              <span className="flex shrink-0 items-center gap-2">
                {request.status === 'queued' ? (
                  <>
                    <Badge tone="slate">#{request.position} in line</Badge>
                    <button type="button" onClick={() => cancel(request.id)} className="text-slate-500 hover:text-rose-400" aria-label={`Cancel request ${request.id}`}>
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </>
                ) : (
                  <Badge tone="blue">running</Badge>
                )}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-slate-500">Nothing is waiting.</p>
      )}
    </div>
  );
}
