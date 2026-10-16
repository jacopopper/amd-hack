import { useEffect, useState } from 'react';
import { api } from '../api';

type Job = {status: string; kind: string; stage: string | null; error: string | null; stages: Record<string, {status: string; elapsed_seconds: number | null; provenance: string; warnings?: unknown[]}>};
const labels: Record<string, string> = {prepare: 'Preparazione foto', jev: 'Candidati visivi', reconstruct: 'Ricostruzione', geometry: 'Geometria e scala', gemma: 'Valutazione del profilo', report: 'Rapporto e PDF'};

export default function JobProgress({jobId}: {jobId: string}) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api<Job>(`/jobs/${jobId}`, {signal: abort.signal});
        if (abort.signal.aborted) return;
        setJob(result); setError('');
        if (['queued', 'running'].includes(result.status)) timer = setTimeout(poll, 1500);
      } catch (e) { if (!abort.signal.aborted) setError((e as Error).message); }
    };
    poll();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [jobId]);
  if (error) return <p className="error" role="alert">Stato del lavoro non disponibile: {error}</p>;
  if (!job) return <p className="notice" role="status">Caricamento dello stato…</p>;
  const active = ['queued', 'running'].includes(job.status);
  return <details className="coverage" open={active}><summary>{active ? 'Elaborazione in corso' : job.status === 'succeeded' ? 'Elaborazione completata' : 'Elaborazione interrotta o fallita'} · {job.stage ? labels[job.stage] : 'in coda'}</summary><div className="coverage-grid">{Object.entries(job.stages).map(([name, stage]) => <p key={name}><strong>{labels[name] ?? name}</strong><br />{stage.status} · {stage.provenance}{stage.elapsed_seconds != null ? ` · ${stage.elapsed_seconds.toFixed(1)} s` : ''}{!!stage.warnings?.length && <><br />Avvisi: {stage.warnings.length}. Verificare copertura e scene.</>}</p>)}</div>{job.error && <p className="error">{job.error}</p>}{active && <p className="muted">Puoi ricaricare questa pagina senza perdere il lavoro. Il worker conserva lo stato sul server.</p>}</details>;
}
