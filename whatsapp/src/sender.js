import { getSocket, toJid } from './auth.js';
import { checkLimit, recordSent, triggerPause, randomDelayMs } from './ratelimit.js';
import { pickTemplateFor, render } from './templates.js';
import { nextJob, markDone, markFailed, pendingCount } from './queue.js';
import { logSend, log } from './logger.js';

// Error patterns that suggest WA is rate-limiting or banning us
const BAN_PATTERNS = [
  /rate.?limit/i, /too many/i, /banned/i, /block/i,
  /408/, /429/, /503/, /policy/i,
];

function looksBanned(err) {
  const msg = String(err?.message ?? err);
  return BAN_PATTERNS.some(p => p.test(msg));
}

let _running = false;
let _sleepReject = null;

export function stop() {
  _running = false;
  log.info('Stop signal received — will halt after current message.');
  if (_sleepReject) _sleepReject();  // cancel any pending delay immediately
}

// Main send loop — runs until stopped or queue is empty.
export async function startWorker(dryRun = false) {
  _running = true;

  if (dryRun) {
    log.info('[DRY RUN] No messages will actually be sent.');
  }

  log.info({ pendingCount: pendingCount() }, 'Worker starting');

  while (_running) {
    // Rate-limit check
    const limit = checkLimit();
    if (!limit.allowed) {
      if (limit.reason === 'paused') {
        log.warn({ resumeAt: limit.resumeAt }, 'Paused — waiting for resume');
        await sleep(60_000);
        continue;
      }
      if (limit.reason === 'daily_cap') {
        log.info({ count: limit.count }, 'Daily cap reached — done for today');
        break;
      }
    }

    const job = nextJob();
    if (!job) {
      log.info('Queue empty — worker idle');
      break;
    }

    const jid = toJid(job.phone);
    if (!jid) {
      log.warn({ lead_id: job.lead_id, phone: job.phone }, 'Unparseable phone — skipping');
      markFailed(job.id, 'invalid_phone');
      continue;
    }

    const template = pickTemplateFor(job.lead_id);
    const message  = render(template, job);

    log.info(
      { lead_id: job.lead_id, name: job.name, jid, template: template.variant, dryRun },
      dryRun ? '[DRY RUN] Would send' : 'Sending'
    );

    if (dryRun) {
      log.info({ preview: message.slice(0, 120) + '…' }, 'Message preview');
      markDone(job.id);
      await sleep(200); // fast in dry-run
      continue;
    }

    try {
      const sock = await getSocket();

      // Verify number is on WhatsApp — also pre-establishes the Signal session
      // so the recipient doesn't see "waiting for this message"
      const [onWA] = await sock.onWhatsApp(jid);
      if (!onWA?.exists) {
        log.warn({ lead_id: job.lead_id, jid }, 'Not on WhatsApp — skipping');
        logSend({ leadId: job.lead_id, phone: jid, status: 'failed', error: 'not_on_whatsapp' });
        markFailed(job.id, 'not_on_whatsapp');
        continue;
      }

      await sock.sendMessage(jid, { text: message });

      logSend({
        leadId:     job.lead_id,
        templateId: template.id,
        phone:      jid,
        status:     'sent',
      });

      recordSent();
      markDone(job.id);

      log.info({ lead_id: job.lead_id, template: template.variant }, 'Sent ✓');

    } catch (err) {
      log.error({ err: err.message, lead_id: job.lead_id }, 'Send failed');

      if (looksBanned(err)) {
        const resumeAt = triggerPause('send_error');
        log.error({ resumeAt }, 'Ban/rate-limit signal — pausing for 24h');
        logSend({ leadId: job.lead_id, phone: jid, status: 'failed', error: err.message });
        markFailed(job.id, err.message);
        await sleep(5_000);
        continue;
      }

      logSend({ leadId: job.lead_id, phone: jid, status: 'failed', error: err.message });
      markFailed(job.id, err.message);
    }

    // Random delay between messages (45–90 s)
    if (_running) {
      const delay = randomDelayMs();
      log.debug({ delayMs: Math.round(delay) }, 'Waiting before next send');
      await sleep(delay);
    }
  }

  log.info('Worker stopped.');
}

function sleep(ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    _sleepReject = () => { clearTimeout(t); resolve(); };
  });
}
