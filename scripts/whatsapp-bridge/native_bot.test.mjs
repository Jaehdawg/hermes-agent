import { strict as assert } from 'node:assert';
import { isNativeBotChat, isTerminalAcknowledgement, normalizeNativeBotEvents } from './native_bot.js';

assert.equal(isNativeBotChat('1807055946647697@bot'), true);
assert.equal(isNativeBotChat('16508702892@s.whatsapp.net'), false);
assert.equal(isTerminalAcknowledgement('All set.'), true);
assert.equal(isTerminalAcknowledgement('👍'), true);
assert.equal(isTerminalAcknowledgement('All set — task result: completed'), false);
assert.equal(isTerminalAcknowledgement('Blocker: I need API access'), false);

const events = normalizeNativeBotEvents([
  { messageId: 'one', chatId: '1807055946647697@bot', sender: '1807055946647697@bot', text: 'MUSE_BRIDGE_OK', timestamp: '2026-10-21T00:17:37-05:00' },
  { messageId: 'two', chatId: '1807055946647697@bot', sender: '1807055946647697@bot', text: 'MUSE_BRIDGE_OK', timestamp: '2026-10-21T00:17:37-05:00' },
  { messageId: 'ordinary', chatId: '16508702892@s.whatsapp.net', sender: '16508702892@s.whatsapp.net', text: 'not native', timestamp: '2026-10-21T00:17:37-05:00' },
  { messageId: 'empty', chatId: '1807055946647697@bot', sender: '1807055946647697@bot', text: ' ', timestamp: '2026-10-21T00:17:37-05:00' },
]);
assert.equal(events.length, 1);
assert.deepEqual(events[0], {
  messageId: 'one', chatId: '1807055946647697@bot', senderId: '1807055946647697@bot',
  senderName: 'Muse', chatName: 'Muse', isGroup: false, body: 'MUSE_BRIDGE_OK',
  fromMe: false, timestamp: '2026-10-21T00:17:37-05:00',
});
console.log('✓ native Muse events are restricted, normalized, and deduplicated');
