const BOT_SUFFIX = '@bot';

export function isNativeBotChat(chatId) {
  return typeof chatId === 'string' && chatId.endsWith(BOT_SUFFIX);
}

/** Normalize the narrow, verified native Muse companion event shape. */
export function normalizeNativeBotEvents(payload) {
  if (!Array.isArray(payload)) return [];
  const seen = new Set();
  const events = [];
  for (const item of payload) {
    if (!item || typeof item !== 'object') continue;
    const chatId = typeof item.chatId === 'string' ? item.chatId : '';
    const body = typeof item.text === 'string' ? item.text.trim() : '';
    const senderId = typeof item.sender === 'string' ? item.sender : chatId;
    const messageId = typeof item.messageId === 'string' ? item.messageId : '';
    const timestamp = typeof item.timestamp === 'string' ? item.timestamp : '';
    if (!isNativeBotChat(chatId) || !body || !senderId || !messageId) continue;
    // The companion can surface the same semantic response twice with unique
    // message IDs. Keep one event for a chat/text/timestamp tuple.
    const dedupeKey = `${chatId}\u0000${body}\u0000${timestamp}`;
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    events.push({
      messageId,
      chatId,
      senderId,
      senderName: 'Muse',
      chatName: 'Muse',
      isGroup: false,
      body,
      fromMe: false,
      timestamp: timestamp || new Date().toISOString(),
    });
  }
  return events;
}
