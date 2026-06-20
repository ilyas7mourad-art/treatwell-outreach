import { getDb } from './db.js';

// Enqueue all eligible UK leads that haven't been WhatsApp-messaged this week.
export function buildQueue() {
  const db = getDb();

  const weekAgo = new Date(Date.now() - 7 * 86_400_000).toISOString();

  // Find leads eligible for WhatsApp outreach:
  //   - UK, has phone, not DNC
  //   - not already queued (pending/done this week)
  //   - not already sent on WhatsApp this week
  const eligible = db.prepare(`
    SELECT l.id FROM leads l
    WHERE l.country    = 'UK'
      AND l.phone      IS NOT NULL AND l.phone != ''
      AND l.do_not_contact = 0
      AND l.id NOT IN (
        SELECT DISTINCT lead_id FROM wa_queue
        WHERE status IN ('pending','processing','done')
          AND created_at > ?
      )
      AND l.id NOT IN (
        SELECT DISTINCT lead_id FROM sends
        WHERE channel = 'whatsapp' AND sent_at > ?
      )
    ORDER BY l.id
  `).all(weekAgo, weekAgo);

  const insert = db.prepare(
    'INSERT OR IGNORE INTO wa_queue (lead_id) VALUES (?)'
  );

  let added = 0;
  const addMany = db.transaction(leads => {
    for (const { id } of leads) {
      insert.run(id);
      added++;
    }
  });
  addMany(eligible);

  return added;
}

// Get the next pending job, locking it to 'processing'.
export function nextJob() {
  const db = getDb();

  const job = db.prepare(`
    SELECT q.*, l.name, l.phone, l.city, l.email, l.booking_url
    FROM wa_queue q
    JOIN leads l ON l.id = q.lead_id
    WHERE q.status       = 'pending'
      AND l.do_not_contact = 0
      AND l.phone        IS NOT NULL AND l.phone != ''
      AND q.scheduled_at <= datetime('now')
    ORDER BY q.created_at ASC
    LIMIT 1
  `).get();

  if (!job) return null;

  db.prepare(
    "UPDATE wa_queue SET status = 'processing', attempts = attempts + 1 WHERE id = ?"
  ).run(job.id);

  return job;
}

export function markDone(jobId) {
  getDb().prepare(
    "UPDATE wa_queue SET status = 'done' WHERE id = ?"
  ).run(jobId);
}

export function markFailed(jobId, error) {
  const db = getDb();
  const job = db.prepare('SELECT attempts FROM wa_queue WHERE id = ?').get(jobId);

  // After 3 attempts, skip permanently
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
  const db = getDb();
  return db.prepare(`
    SELECT status, COUNT(*) as n FROM wa_queue GROUP BY status
  `).all();
}
