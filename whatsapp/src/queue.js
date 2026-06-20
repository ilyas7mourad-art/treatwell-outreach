import { getDb } from './db.js';

const FU_DAYS = [0, 3, 6, 9]; // day offsets for initial + 3 follow-ups

// Build/refresh the queue — call on startup and when queue runs dry.
// Returns counts of newly added jobs per follow-up level.
export function buildQueue() {
  const db = getDb();
  const insert = db.prepare(
    'INSERT INTO wa_queue (lead_id, follow_up_num) VALUES (?, ?)'
  );

  const insertAll = db.transaction(rows => {
    for (const { id, follow_up_num } of rows) insert.run(id, follow_up_num);
  });

  // ── Initial messages ──────────────────────────────────────────────
  const initial = db.prepare(`
    SELECT l.id, 0 AS follow_up_num FROM leads l
    WHERE l.country = 'UK'
      AND l.phone IS NOT NULL AND l.phone != ''
      AND l.do_not_contact = 0
      AND l.id NOT IN (
        SELECT lead_id FROM wa_queue
        WHERE follow_up_num = 0 AND status IN ('pending','processing','done','failed')
      )
      AND l.phone NOT IN (
        SELECT phone FROM sends
        WHERE channel = 'whatsapp' AND follow_up_num = 0 AND status = 'sent'
      )
    ORDER BY l.id
  `).all();

  // ── Follow-ups 1, 2, 3 ───────────────────────────────────────────
  const fuResults = [0, 0, 0];
  const followUps = [];

  for (const n of [1, 2, 3]) {
    const rows = db.prepare(`
      SELECT l.id, ? AS follow_up_num FROM leads l
      WHERE l.country = 'UK'
        AND l.phone IS NOT NULL AND l.phone != ''
        AND l.do_not_contact = 0
        -- previous follow-up (or initial) was sent 3+ days ago
        AND EXISTS (
          SELECT 1 FROM sends s
          WHERE s.lead_id = l.id
            AND s.channel = 'whatsapp'
            AND s.follow_up_num = ?
            AND s.status = 'sent'
            AND s.sent_at <= datetime('now', '-3 days')
        )
        -- this follow-up not yet queued or sent
        AND l.id NOT IN (
          SELECT lead_id FROM wa_queue
          WHERE follow_up_num = ? AND status IN ('pending','processing','done','failed')
        )
        AND l.id NOT IN (
          SELECT lead_id FROM sends
          WHERE channel = 'whatsapp' AND follow_up_num = ? AND status = 'sent'
        )
      ORDER BY l.id
    `).all(n, n - 1, n, n);

    fuResults[n - 1] = rows.length;
    followUps.push(...rows);
  }

  const all = [...initial, ...followUps];
  if (all.length) insertAll(all);

  return {
    initial: initial.length,
    fu1: fuResults[0],
    fu2: fuResults[1],
    fu3: fuResults[2],
    total: all.length,
  };
}

// Get next pending job, locking it to 'processing'.
export function nextJob() {
  const db = getDb();

  const job = db.prepare(`
    SELECT q.*, l.name, l.phone, l.city, l.email, l.booking_url
    FROM wa_queue q
    JOIN leads l ON l.id = q.lead_id
    WHERE q.status = 'pending'
      AND l.do_not_contact = 0
      AND l.phone IS NOT NULL AND l.phone != ''
      AND q.scheduled_at <= datetime('now')
      AND l.phone NOT IN (
        SELECT phone FROM sends
        WHERE channel = 'whatsapp' AND status = 'sent'
          AND follow_up_num = q.follow_up_num
          AND sent_at > datetime('now', '-7 days')
      )
    ORDER BY q.follow_up_num ASC, q.created_at ASC
    LIMIT 1
  `).get();

  if (!job) return null;

  db.prepare(
    "UPDATE wa_queue SET status = 'processing', attempts = attempts + 1 WHERE id = ?"
  ).run(job.id);

  return job;
}

export function markDone(jobId) {
  getDb().prepare("UPDATE wa_queue SET status = 'done' WHERE id = ?").run(jobId);
}

export function markFailed(jobId, error) {
  const db = getDb();
  const job = db.prepare('SELECT attempts FROM wa_queue WHERE id = ?').get(jobId);
  const newStatus = (job?.attempts ?? 0) >= 3 ? 'failed' : 'pending';
  db.prepare(`
    UPDATE wa_queue
    SET status = ?, last_error = ?, scheduled_at = datetime('now', '+10 minutes')
    WHERE id = ?
  `).run(newStatus, String(error), jobId);
}

export function pendingCount() {
  return getDb().prepare(
    "SELECT COUNT(*) as n FROM wa_queue WHERE status = 'pending'"
  ).get().n;
}

export function queueStats() {
  return getDb().prepare(`
    SELECT follow_up_num, status, COUNT(*) as n
    FROM wa_queue GROUP BY follow_up_num, status ORDER BY follow_up_num, status
  `).all();
}
