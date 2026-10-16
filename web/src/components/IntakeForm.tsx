import { useState } from 'react';
import type { FormEvent } from 'react';
import type { Inputs } from '../types';

export default function IntakeForm({initial, save, cancel}: {initial?: Inputs; save: (inputs: Inputs) => Promise<void>; cancel?: () => void}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const text = (key: string) => String(data.get(key) ?? '').trim() || null;
    setBusy(true); setError('');
    try { await save({label: text('label')!, profile: {description: text('description')!, mobility_aid: text('aid'), aid_width_m: text('width') ? Number(text('width')) / 100 : null, vision: text('vision'), support_needs: text('support'), relevant_history: text('history'), daily_routes: (text('routes') ?? '').split('\n').map(r => r.trim()).filter(Boolean)}, profile_confirmed: data.has('confirmed'), staged_data_confirmed: true, processing_consent: true, external_processing_consent: data.has('external')}); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <form className="intake paper" onSubmit={submit}>
    <span className="eyebrow">01 / il contesto</span><h2>Ogni casa è diversa.<br />Ogni persona, anche.</h2>
    <p className="muted">Solo profili fittizi e immagini dimostrative. I campi sconosciuti possono restare vuoti.</p>
    <label>Nome della valutazione<input name="label" required maxLength={120} defaultValue={initial?.label} placeholder="Es. Casa dimostrativa · soggiorno" /></label>
    <label>Descrizione del profilo fittizio<textarea name="description" required maxLength={2000} rows={3} defaultValue={initial?.profile.description} placeholder="Mobilità, abitudini e supporto disponibile…" /></label>
    <div className="form-grid"><label>Ausilio alla mobilità<input name="aid" maxLength={200} defaultValue={initial?.profile.mobility_aid ?? ''} placeholder="Non specificato" /></label><label>Larghezza ausilio, se nota (cm)<input name="width" type="number" min="1" max="300" step="0.1" defaultValue={initial?.profile.aid_width_m ? initial.profile.aid_width_m * 100 : ''} /></label></div>
    <div className="form-grid"><label>Esigenze visive<input name="vision" maxLength={500} defaultValue={initial?.profile.vision ?? ''} /></label><label>Supporto disponibile<input name="support" maxLength={500} defaultValue={initial?.profile.support_needs ?? ''} /></label></div>
    <label>Storia rilevante fornita, se nota<input name="history" maxLength={1000} defaultValue={initial?.profile.relevant_history ?? ''} /></label>
    <label>Percorsi quotidiani (uno per riga)<textarea name="routes" rows={2} defaultValue={initial?.profile.daily_routes.join('\n')} placeholder="Camera → bagno" /></label>
    <label className="check"><input type="checkbox" name="confirmed" required defaultChecked={initial?.profile_confirmed} /> Ho verificato i dati inseriti; il profilo è fittizio.</label>
    <label className="check"><input type="checkbox" required /> Confermo l’uso di sole immagini dimostrative e acconsento alla loro elaborazione.</label>
    <label className="check"><input type="checkbox" name="external" defaultChecked={initial?.external_processing_consent} /> Se Jev è abilitato, autorizzo l’invio delle foto preparate a TypeSafe. Il profilo resta sul server.</label>
    {error && <p className="error" role="alert">{error}</p>}<div className="actions"><button disabled={busy}>{busy ? 'Salvataggio…' : initial ? 'Salva il profilo' : 'Crea la valutazione →'}</button>{cancel && <button type="button" className="secondary" onClick={cancel}>Annulla</button>}</div>
  </form>;
}
