/** One chat session as shown in the sidebar: id, display title, last-touched time, status. */
export type SessionIndexEntry = {
  sessionId: string;
  title: string;
  updatedAt: string;
  status?: string;
};

function sanitizeAccountKey(accountKey: string): string {
  const trimmed = accountKey.trim();
  if (!trimmed) return 'anonymous';
  return trimmed.replace(/[^a-zA-Z0-9_-]/g, '_');
}

function indexKey(profileId: string, accountKey: string) {
  return `ado2gh-agent-sessions:${sanitizeAccountKey(accountKey)}:${profileId}`;
}

function activeKey(profileId: string, accountKey: string) {
  return `ado2gh-agent-active:${sanitizeAccountKey(accountKey)}:${profileId}`;
}

/** localStorage key holding a session's cached chat transcript, scoped to profile and account. */
export function chatCacheKey(profileId: string, sessionId: string, accountKey: string) {
  return `ado2gh-agent-chat:${sanitizeAccountKey(accountKey)}:${profileId}:${sessionId}`;
}

/** localStorage key holding a session's cached thinking events, scoped to profile and account. */
export function thinkingCacheKey(profileId: string, sessionId: string, accountKey: string) {
  return `ado2gh-agent-thinking:${sanitizeAccountKey(accountKey)}:${profileId}:${sessionId}`;
}

/** localStorage key holding the thinking events archived against a single turn of a session. */
export function turnThinkingCacheKey(
  profileId: string,
  sessionId: string,
  turnIndex: number,
  accountKey: string,
) {
  return `ado2gh-agent-turn-thinking:${sanitizeAccountKey(accountKey)}:${profileId}:${sessionId}:${turnIndex}`;
}

/**
 * Archive a turn's thinking events so they can be re-shown after the live stream ends.
 * No-op when the turn produced no events.
 */
export function saveTurnThinking(
  profileId: string,
  sessionId: string,
  accountKey: string,
  turnIndex: number,
  events: unknown[],
) {
  if (!events.length) return;
  localStorage.setItem(
    turnThinkingCacheKey(profileId, sessionId, turnIndex, accountKey),
    JSON.stringify(events),
  );
}

/** Read back one turn's archived thinking events; empty array when missing or unparseable. */
export function loadTurnThinking(
  profileId: string,
  sessionId: string,
  accountKey: string,
  turnIndex: number,
): unknown[] {
  const raw = localStorage.getItem(
    turnThinkingCacheKey(profileId, sessionId, turnIndex, accountKey),
  );
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/**
 * Collect the archived thinking events for every user turn in a session, keyed by turn index.
 * Turns with nothing stored are omitted.
 */
export function loadAllTurnThinking(
  profileId: string,
  sessionId: string,
  accountKey: string,
  userMessageCount: number,
): Record<number, unknown[]> {
  const archived: Record<number, unknown[]> = {};
  for (let i = 0; i < userMessageCount; i++) {
    const events = loadTurnThinking(profileId, sessionId, accountKey, i);
    if (events.length) archived[i] = events;
  }
  return archived;
}

/** Drop every archived turn-thinking entry for a session, used when the session goes away. */
export function clearSessionTurnThinking(
  profileId: string,
  sessionId: string,
  accountKey: string,
) {
  const prefix = `ado2gh-agent-turn-thinking:${sanitizeAccountKey(accountKey)}:${profileId}:${sessionId}:`;
  const keys: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith(prefix)) keys.push(key);
  }
  keys.forEach((key) => localStorage.removeItem(key));
}

/**
 * Drop every cached agent artefact this browser holds, for all profiles and accounts.
 *
 * Chat transcripts and thinking events carry tool-call metadata about the operator's ADO
 * and GitHub estate (CA-003), so they must not outlive the session that produced them —
 * call this on logout, before the next operator can reach the same browser profile.
 */
export function clearAgentStorage() {
  if (typeof localStorage === 'undefined') return;
  const keys: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith('ado2gh-agent-')) keys.push(key);
  }
  keys.forEach((key) => localStorage.removeItem(key));
}

/** Read a session's cached thinking events; empty array when missing or unparseable. */
export function loadCachedThinking(
  profileId: string,
  sessionId: string,
  accountKey: string,
): unknown[] {
  const raw = localStorage.getItem(thinkingCacheKey(profileId, sessionId, accountKey));
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** A chat message as held in the local cache; all fields optional so older caches still parse. */
export type CachedChatMessage = {
  id?: string;
  role?: string;
  content?: string;
  kind?: string;
};

/**
 * Normalise a user message into a comparison key, folding Python-style True/False to JSON
 * casing so the cached and server copies of the same message match.
 */
export function normalizeUserMessageKey(content: string): string {
  return (content ?? '')
    .trim()
    .replace(/\bTrue\b/g, 'true')
    .replace(/\bFalse\b/g, 'false');
}

/** Merge server chat with local cache after session switch (optimistic / interrupted turns). */
export function mergeChatMessagesForLoad(
  serverMessages: CachedChatMessage[],
  cachedMessages: CachedChatMessage[] | null,
  sessionId: string,
): CachedChatMessage[] {
  const withIds = (messages: CachedChatMessage[]) =>
    messages.map((m, i) => ({
      ...m,
      id: m.id ?? `${m.kind ?? m.role ?? 'message'}-${i}-${sessionId}`,
    }));

  const server = withIds(serverMessages);
  if (!cachedMessages?.length) return server;
  const cached = withIds(cachedMessages);
  if (!server.length) return cached;

  const paired = new Map<string, number>();
  for (const message of server) {
    if (message.role !== 'user') continue;
    const key = normalizeUserMessageKey(message.content ?? '');
    paired.set(key, (paired.get(key) ?? 0) + 1);
  }
  const pendingUsers: CachedChatMessage[] = [];
  for (const message of cached) {
    if (message.role !== 'user') continue;
    const key = normalizeUserMessageKey(message.content ?? '');
    const count = paired.get(key) ?? 0;
    if (count > 0) {
      paired.set(key, count - 1);
      continue;
    }
    pendingUsers.push(message);
  }

  const serverAssistantBodies = new Set(
    server
      .filter((m) => m.role === 'assistant' && m.content)
      .map((m) => m.content as string),
  );
  const pendingAssistants = cached.filter(
    (m) =>
      m.role === 'assistant' &&
      (m.kind === 'message' || !m.kind) &&
      m.content &&
      !serverAssistantBodies.has(m.content),
  );

  return [...server, ...pendingUsers, ...pendingAssistants];
}

/**
 * Load the sidebar session list for a profile and account, falling back to a one-time migration
 * of legacy storage keys when no current index exists.
 */
export function loadSessionIndex(profileId: string, accountKey: string): SessionIndexEntry[] {
  const raw = localStorage.getItem(indexKey(profileId, accountKey));
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as SessionIndexEntry[];
      if (Array.isArray(parsed)) return parsed;
    } catch {
      /* fall through */
    }
  }
  return migrateLegacyStorage(profileId, accountKey);
}

function migrateLegacyStorage(profileId: string, accountKey: string): SessionIndexEntry[] {
  const legacyKeys = [
    `ado2gh-agent-sessions:${profileId}`,
    `ado2gh-agent-chat:${profileId}`,
  ];
  for (const legacyKey of legacyKeys) {
    const raw = localStorage.getItem(legacyKey);
    if (!raw) continue;
    try {
      if (legacyKey.includes('agent-chat:')) {
        const saved = JSON.parse(raw) as {
          sessionId?: string;
          messages?: Array<{ role?: string; content?: string }>;
        };
        if (!saved.sessionId) continue;
        const firstUser = saved.messages?.find((m) => m.role === 'user');
        const entry: SessionIndexEntry = {
          sessionId: saved.sessionId,
          title: truncateTitle(firstUser?.content || 'Chat'),
          updatedAt: new Date().toISOString(),
        };
        if (saved.messages?.length) {
          localStorage.setItem(
            chatCacheKey(profileId, saved.sessionId, accountKey),
            JSON.stringify(saved.messages),
          );
        }
        saveSessionIndex(profileId, accountKey, [entry]);
        localStorage.removeItem(legacyKey);
        return [entry];
      }
      const parsed = JSON.parse(raw) as SessionIndexEntry[];
      if (Array.isArray(parsed) && parsed.length) {
        saveSessionIndex(profileId, accountKey, parsed);
        localStorage.removeItem(legacyKey);
        return parsed;
      }
    } catch {
      continue;
    }
  }
  return [];
}

/** Write the sidebar session list to localStorage, replacing whatever was stored before. */
export function saveSessionIndex(
  profileId: string,
  accountKey: string,
  entries: SessionIndexEntry[],
) {
  localStorage.setItem(indexKey(profileId, accountKey), JSON.stringify(entries));
}

/** New session — prepended to the list. Does not reorder existing entries. */
export function upsertSessionIndex(
  profileId: string,
  accountKey: string,
  entry: SessionIndexEntry,
) {
  const existing = loadSessionIndex(profileId, accountKey).filter(
    (e) => e.sessionId !== entry.sessionId,
  );
  saveSessionIndex(profileId, accountKey, [entry, ...existing]);
}

/** In-place update — preserves list order and `updatedAt`. */
export function patchSessionIndex(
  profileId: string,
  accountKey: string,
  sessionId: string,
  patch: Partial<Pick<SessionIndexEntry, 'title' | 'status'>>,
) {
  const entries = loadSessionIndex(profileId, accountKey);
  const idx = entries.findIndex((e) => e.sessionId === sessionId);
  if (idx < 0) return;
  const next = [...entries];
  next[idx] = { ...next[idx], ...patch };
  saveSessionIndex(profileId, accountKey, next);
}

/** Drop a session from the sidebar list along with its cached chat and thinking entries. */
export function removeSessionIndex(
  profileId: string,
  accountKey: string,
  sessionId: string,
) {
  const next = loadSessionIndex(profileId, accountKey).filter((e) => e.sessionId !== sessionId);
  saveSessionIndex(profileId, accountKey, next);
  localStorage.removeItem(chatCacheKey(profileId, sessionId, accountKey));
  localStorage.removeItem(thinkingCacheKey(profileId, sessionId, accountKey));
  clearSessionTurnThinking(profileId, sessionId, accountKey);
}

/** The session this profile and account last had open, or null when none was recorded. */
export function loadActiveSessionId(profileId: string, accountKey: string): string | null {
  return localStorage.getItem(activeKey(profileId, accountKey));
}

/** Remember the open session for this profile and account; passing null clears the record. */
export function saveActiveSessionId(
  profileId: string,
  accountKey: string,
  sessionId: string | null,
) {
  if (sessionId) {
    localStorage.setItem(activeKey(profileId, accountKey), sessionId);
  } else {
    localStorage.removeItem(activeKey(profileId, accountKey));
  }
}

/** Collapse whitespace and clip a message down to a sidebar title, defaulting to 'New chat'. */
export function truncateTitle(text: string, max: number = 56): string {
  const oneLine = text.replace(/\s+/g, ' ').trim();
  if (oneLine.length <= max) return oneLine || 'New chat';
  return `${oneLine.slice(0, max - 1)}…`;
}

/**
 * Merge the locally cached session list with the server's, keeping local ordering and titles
 * while taking status from the server and appending sessions seen only remotely.
 */
export function mergeSessionLists(
  local: SessionIndexEntry[],
  remote: SessionIndexEntry[],
): SessionIndexEntry[] {
  const remoteById = new Map(remote.map((entry) => [entry.sessionId, entry]));
  const seen = new Set<string>();
  const merged: SessionIndexEntry[] = [];

  for (const entry of local) {
    const remoteEntry = remoteById.get(entry.sessionId);
    merged.push(
      remoteEntry
        ? {
            ...entry,
            status: remoteEntry.status ?? entry.status,
            title: entry.title || remoteEntry.title,
          }
        : entry,
    );
    seen.add(entry.sessionId);
  }

  for (const entry of remote) {
    if (!seen.has(entry.sessionId)) {
      merged.push(entry);
    }
  }

  return merged;
}
