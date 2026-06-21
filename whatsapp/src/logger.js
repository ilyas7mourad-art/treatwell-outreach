import pino from 'pino';
import { getDb } from './db.js';

export const log = pino({
  level: process.env.LOG_LEVEL || 'info',
  transport: process.stdout.isTTY
    ? { target: 'pino-pretty', options: { colorize: true, ignore: 'pid,hostname' } }
    : undefined,
});

export function logSend({ leadId, channel = 'whatsapp', templateId, phone, status, error, followUpNum = 0 }) {
  const db = getDb();
  db.prepare(`
    INSERT INTO sends (lead_id, channel, template_id, phone, status, error, follow_up_num)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `).run(leadId, channel, templateId ?? null, phone ?? null, status, error ?? null, followUpNum);
}

// Called when WhatsApp fires a read/delivery receipt for one of our messages.
// status: 'delivered' (double tick) | 'read' (blue tick)
export function updateSendStatus(phone, status) {
  const db = getDb();
  db.prepare(`
    UPDATE sends SET status = ?
    WHERE phone = ? AND channel = 'whatsapp' AND status = 'sent'
  `).run(status, phone);
}

// Called when a lead replies to us (non-opt-out message).
export function markReplied(jid) {
  const db = getDb();
  const phone = jid.replace('@s.whatsapp.net', '');
  db.prepare(`
    UPDATE leads SET replied = 1, replied_at = datetime('now')
    WHERE phone LIKE ? AND replied = 0
  `).run(`%${phone}%`);
}

export function recentSends(limit = 20) {
  return getDb().prepare(`
    SELECT s.*, l.name, l.city
    FROM sends s
    JOIN leads l ON l.id = s.lead_id
    WHERE s.channel = 'whatsapp'
    ORDER BY s.sent_at DESC
    LIMIT ?
  `).all(limit);
}
