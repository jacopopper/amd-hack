import { useState } from 'react';
import type { Anchor, Hazard, Priority } from '../types';

export default function ReviewControls({hazard, save, pickedAnchor, disabled}: {hazard: Hazard; save: (value: unknown) => Promise<void>; pickedAnchor: Anchor | null; disabled: boolean}) {
  const current = hazard.review?.effective ?? hazard;
  const [decision, setDecision] = useState(hazard.review?.decision ?? 'approve');
  const [observation, setObservation] = useState(current.observation);
  const [action, setAction] = useState(current.suggested_action);
  const [priority, setPriority] = useState<Priority>(current.priority);
  const [note, setNote] = useState(hazard.review?.note ?? '');
  const [removeAnchor, setRemoveAnchor] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  return <form data-hazard-id={hazard.id} className="review-controls" onSubmit={async e => { e.preventDefault(); setBusy(true); setMessage(''); try { await save({decision, ...(decision === 'edit' ? {observation, suggested_action: action, priority, anchor: removeAnchor ? null : pickedAnchor, remove_anchor: removeAnchor} : {}), note}); setMessage('Decisione salvata.'); } catch (e) { setMessage((e as Error).message); } finally { setBusy(false); } }}>
    <label>Decisione del revisore<select value={decision} onChange={e => setDecision(e.target.value)} disabled={disabled}><option value="approve">Approva</option><option value="edit">Modifica</option><option value="reject">Rifiuta</option><option value="defer">Rinvia / richiedi evidenza</option></select></label>
    {decision === 'edit' && <><label htmlFor="review-observation">Osservazione</label><textarea id="review-observation" value={observation} onChange={e => setObservation(e.target.value)} required maxLength={2000} /><label htmlFor="review-action">Azione</label><textarea id="review-action" value={action} onChange={e => setAction(e.target.value)} required maxLength={2000} /><label>Priorità<select value={priority} onChange={e => setPriority(e.target.value as Priority)}><option value="urgent">Urgente</option><option value="important">Importante</option><option value="recommended">Raccomandata</option></select></label><label className="check"><input type="checkbox" checked={removeAnchor} onChange={e => setRemoveAnchor(e.target.checked)} /> Rimuovi il punto 3D; conserva l’evidenza fotografica</label>{pickedAnchor && <p className="notice">Nuovo punto scelto nella scena. Verrà salvato con questa modifica.</p>}</>}
    <label htmlFor="review-note">Nota {decision === 'defer' ? '(obbligatoria)' : '(facoltativa)'}</label><textarea id="review-note" value={note} onChange={e => setNote(e.target.value)} maxLength={2000} required={decision === 'defer'} />
    <button disabled={busy || disabled}>{busy ? 'Salvataggio…' : 'Salva decisione'}</button><p role="status">{message}</p>
  </form>;
}
