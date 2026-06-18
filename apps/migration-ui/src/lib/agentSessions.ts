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

export function chatCacheKey(profileId: string, sessionId: string, accountKey: string) {
  return `ado2gh-agent-chat:${sanitizeAccountKey(accountKey)}:${profileId}:${sessionId}`;
}

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

export function saveSessionIndex(
  profileId: string,
  accountKey: string,
  entries: SessionIndexEntry[],
) {
  const sorted = [...entries].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  localStorage.setItem(indexKey(profileId, accountKey), JSON.stringify(sorted));
}

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

export function removeSessionIndex(
  profileId: string,
  accountKey: string,
  sessionId: string,
) {
  const next = loadSessionIndex(profileId, accountKey).filter((e) => e.sessionId !== sessionId);
  saveSessionIndex(profileId, accountKey, next);
  localStorage.removeItem(chatCacheKey(profileId, sessionId, accountKey));
}

export function loadActiveSessionId(profileId: string, accountKey: string): string | null {
  return localStorage.getItem(activeKey(profileId, accountKey));
}

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

export function truncateTitle(text: string, max = 56): string {
  const oneLine = text.replace(/\s+/g, ' ').trim();
  if (oneLine.length <= max) return oneLine || 'New chat';
  return `${oneLine.slice(0, max - 1)}…`;
}

export function mergeSessionLists(
  local: SessionIndexEntry[],
  remote: SessionIndexEntry[],
): SessionIndexEntry[] {
  const byId = new Map<string, SessionIndexEntry>();
  for (const entry of [...local, ...remote]) {
    const prev = byId.get(entry.sessionId);
    if (!prev || entry.updatedAt > prev.updatedAt) {
      byId.set(entry.sessionId, entry);
    }
  }
  return [...byId.values()].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}
