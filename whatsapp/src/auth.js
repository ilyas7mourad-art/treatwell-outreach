import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  Browsers,
} from '@whiskeysockets/baileys';
import { log } from './logger.js';
import { isOptOut, flagDnc, cancelQueuedJobs } from './optout.js';

const __dir = dirname(fileURLToPath(import.meta.url));
const SESSIONS_DIR = join(__dir, '..', 'sessions');

let _sock = null;
let _onMessage = null;

export function setMessageHandler(fn) {
  _onMessage = fn;
}

export async function getSocket() {
  if (_sock) return _sock;
  _sock = await connect();
  return _sock;
}

async function connect(retries = 0) {
  const { version } = await fetchLatestBaileysVersion();
  const { state, saveCreds } = await useMultiFileAuthState(SESSIONS_DIR);

  const sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, log.child({ module: 'keystore' })),
    },
    browser: Browsers.macOS('Chrome'),
    printQRInTerminal: true,       // show QR on first run
    logger: log.child({ module: 'baileys', level: 'silent' }),
    generateHighQualityLinkPreview: false,
    syncFullHistory: false,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      log.info('Scan the QR code above with WhatsApp to authenticate.');
    }

    if (connection === 'open') {
      log.info('WhatsApp connected ✓');
      _sock = sock;
    }

    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;

      if (loggedOut) {
        log.error('Logged out — delete sessions/ and re-scan QR to re-authenticate.');
        process.exit(1);
      }

      const delay = Math.min(5_000 * 2 ** retries, 60_000);
      log.warn({ code, retries }, `Disconnected — reconnecting in ${delay / 1000}s`);
      _sock = null;
      setTimeout(() => connect(retries + 1), delay);
    }
  });

  // Handle inbound messages (opt-out detection)
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;

    for (const msg of messages) {
      if (msg.key.fromMe) continue;

      const jid  = msg.key.remoteJid ?? '';
      const text = msg.message?.conversation
        ?? msg.message?.extendedTextMessage?.text
        ?? '';

      if (!text) continue;

      log.debug({ jid, text }, 'Inbound message');

      if (isOptOut(text)) {
        const changed = flagDnc(jid, 'whatsapp_stop_reply');
        cancelQueuedJobs(jid);
        if (changed) log.info({ jid }, 'Opt-out received — lead flagged DNC');
      }

      if (_onMessage) _onMessage(jid, text);
    }
  });

  return sock;
}

// Normalise a phone string to WhatsApp JID (e.g. "447911123456@s.whatsapp.net")
export function toJid(rawPhone) {
  if (!rawPhone) return null;
  // Strip everything except digits and leading +
  let digits = rawPhone.replace(/[^\d+]/g, '');
  if (digits.startsWith('+')) digits = digits.slice(1);
  if (digits.startsWith('00')) digits = digits.slice(2);
  // UK: leading 0 → 44
  if (digits.startsWith('0')) digits = '44' + digits.slice(1);
  if (digits.length < 10 || digits.length > 15) return null;
  return `${digits}@s.whatsapp.net`;
}
