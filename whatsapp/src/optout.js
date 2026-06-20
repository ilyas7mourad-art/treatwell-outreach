import { getDb } from './db.js';

const STOP_RE = /\b(stop|unsubscribe|remove me|opt.?out|no thanks|not interested|leave me alone|don.?t (contact|message|text)|do not contact|remove|block)\b/i;

export function isOptOut(text) {
  return STOP_RE.test((text || '').toLowerCase());
}

// Mark a lead as DNC by WhatsApp JID (phone@s.whatsapp.net) or raw phone.
export function flagDnc(jid, reason = 'whatsapp_reply') {
  const db = getDb();
  const phone = jid.replace(/@.+$/, ''); // strip @s.whatsapp.net

  const result = db.prepare(`
    UPDATE leads
    SET do_not_contact = 1,
        dnc_reason     = ?,
        dnc_at         = datetime('now')
    WHERE REPLACE(REPLACE(REPLACE(phone, '+', ''), ' ', ''), '-', '') = ?
      AND do_not_contact = 0
  `).run(reason, phone);

  return result.changes;
}

// Also cancel any queued jobs for this lead.
export function cancelQueuedJobs(jid) {
  const db = getDb();
  const phone = jid.replace(/@.+$/, '');

  db.prepare(`
    UPDATE wa_queue SET status = 'skipped'
    WHERE lead_id IN (
      SELECT id FROM leads
      WHERE REPLACE(REPLACE(REPLACE(phone, '+', ''), ' ', ''), '-', '') = ?
    ) AND status = 'pending'
  `).run(phone);
}
