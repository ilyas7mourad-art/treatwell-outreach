import pino from 'pino';
import { getDb } from './db.js';

export const log = pino({
  level: process.env.LOG_LEVEL || 'info',
  transport: process.stdout.isTTY
    ? { target: 'pino-pretty', options: { colorize: true, ignore: 'pid,hostname' } }
    : undefined,
});

export function logSend({ leadId, channel = 'whatsapp', templateId, phone, status, error }) {
  const db = getDb();
  db.prepare(`
    INSERT INTO sends (lead_id, channel, template_id, phone, status, error)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(leadId, channel, templateId ?? null, phone ?? null, status, error ?? null);
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
