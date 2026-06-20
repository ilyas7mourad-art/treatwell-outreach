import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  Browsers,
} from '@whiskeysockets/baileys';
import { log } from './logger.js';
import { isOptOut, flagDnc, cancelQueuedJobs } from './optout.js';

const require = createRequire(import.meta.url);
const qrcode  = require('qrcode-terminal');

const __dir = dirname(fileURLToPath(import.meta.url));
const SESSIONS_DIR = join(__dir, '..', 'sessions');

let _sock      = null;
let _connected = false;
let _waiters   = [];          // promises waiting for the socket to be ready
let _onMessage = null;

export function setMessageHandler(fn) {
  _onMessage = fn;
}

// Returns a connected, authenticated socket. Waits for 'open' before resolving.
export function getSocket() {
  if (_sock && _connected) return Promise.resolve(_sock);

  // Kick off connection if not started
  if (!_sock) connect();

  // Queue caller until connection.update fires 'open'
  return new Promise((resolve, reject) => {
    _waiters.push({ resolve, reject });
  });
}

export async function connect(retries = 0) {
  const { version } = await fetchLatestBaileysVersion();
  const { state, saveCreds } = await useMultiFileAuthState(SESSIONS_DIR);

  const sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, log.child({ module: 'keystore', level: 'silent' })),
    },
    browser: Browsers.macOS('Chrome'),
    logger: log.child({ module: 'baileys', level: 'silent' }),
    generateHighQualityLinkPreview: false,
    syncFullHistory: false,
    // Needed so WhatsApp can re-request messages that failed to decrypt
    getMessage: async () => ({ conversation: '' }),
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    // Display QR code in terminal (printQRInTerminal option was deprecated)
    if (qr) {
      console.log('\n');
      qrcode.generate(qr, { small: true });
      console.log('\n👆  Scan this QR code with WhatsApp (Settings → Linked Devices → Link a Device)\n');
    }

    if (connection === 'open') {
      log.info('WhatsApp connected ✓');
      _sock      = sock;
      _connected = true;
      // Wake all callers waiting for the socket
      for (const { resolve } of _waiters) resolve(sock);
      _waiters = [];
    }

    if (connection === 'close') {
      _connected = false;
      _sock      = null;

      const code      = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;

      if (loggedOut) {
        log.error('Logged out — delete sessions/ and re-scan QR.');
        for (const { reject } of _waiters) reject(new Error('logged_out'));
        _waiters = [];
        process.exit(1);
      }

      const delay = Math.min(5_000 * 2 ** retries, 60_000);
      log.warn({ code, retries }, `Disconnected — reconnecting in ${delay / 1000}s`);
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
}

// Normalise a UK phone string to WhatsApp JID (e.g. "447911123456@s.whatsapp.net")
export function toJid(rawPhone) {
  if (!rawPhone) return null;
  let digits = rawPhone.replace(/[^\d+]/g, '');
  if (digits.startsWith('+'))  digits = digits.slice(1);
  if (digits.startsWith('00')) digits = digits.slice(2);
  if (digits.startsWith('0'))  digits = '44' + digits.slice(1);
  if (digits.length < 10 || digits.length > 15) return null;
  return `${digits}@s.whatsapp.net`;
}
