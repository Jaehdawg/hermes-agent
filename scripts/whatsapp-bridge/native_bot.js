const BOT_SUFFIX = '@bot';

// Peer agents have stable outbound routes but WhatsApp can surface inbound
// replies under a Linked Identity Device (LID). Canonicalize them before the
// gateway derives a conversation session, keeping one peer context per agent.
const PEER_AGENT_CHAT_ALIASES = new Map([
  ['23949492670633@lid', '16508702892@s.whatsapp.net'], // Instinct inbound LID → verified route
]);

export function canonicalPeerAgentChatId(chatId) {
  return PEER_AGENT_CHAT_ALIASES.get(chatId) || chatId;
}

export function isPeerAgentChat(chatId) {
  const canonical = canonicalPeerAgentChatId(chatId);
  return canonical === '1807055946647697@bot' || canonical === '16508702892@s.whatsapp.net';
}

export function isNativeBotChat(chatId) {
  return typeof chatId === 'string' && chatId.endsWith(BOT_SUFFIX);
}

/** Extract the native companion's outbound ID for linked-device echo tracking. */
export function nativeBotOutboundMessageId(payload) {
  return typeof payload?.messageId === 'string' && payload.messageId.trim() ? payload.messageId : '';
}

/**
 * Terminal acknowledgements add no actionable state. Forwarding them to the
 * agent loop causes reciprocal "confirmed" / reaction ping-pong.
 */
export function isTerminalAcknowledgement(text) {
  if (typeof text !== 'string') return false;
  const normalized = text.trim().toLowerCase().replace(/[.!]+$/g, '');
  const exact = new Set([
    '👍', '✅', '👌', 'ack', 'acknowledged', 'confirmed', 'got it',
    'all set', 'no action needed', 'understood', 'thanks', 'thank you',
  ]);
  if (exact.has(normalized)) return true;
  if (/^[a-z0-9]+(?:_[a-z0-9]+)*_ack$/.test(normalized)) return true;
  // These phrases are the non-actionable correction/confirmation pattern that
  // caused the observed peer loop. Keep task results, questions, and blockers.
  return /^(receipt confirmed|correction accepted and recorded|agreed(?:\s|—|-|$)|exactly(?:\s|—|-|$)|acknowledged(?:\s|—|-|$)|standing by for your correction)/.test(normalized);
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
