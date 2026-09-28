// api.js — обёртки над fetch/WebSocket-URL/скачиванием файлов, добавляющие
// заголовок Authorization, когда включена авторизация (см. session.js).
// В локальном режиме (`auth_enabled: false`) — ведут себя как обычный fetch/<a href>.

import { getAccessToken } from './session.js';

/** fetch(...), который добавляет `Authorization: Bearer <token>`, если есть сессия.
 * Без авторизации (локальный режим) — обычный fetch без каких-либо изменений. */
export async function authedFetch(url, options = {}) {
  const token = await getAccessToken();
  if (!token) return fetch(url, options);

  const headers = new Headers(options.headers || {});
  headers.set('Authorization', `Bearer ${token}`);
  return fetch(url, { ...options, headers });
}

/** Часть query string для WebSocket-URL: `&token=...`, либо '' без авторизации. */
export async function wsAuthParam() {
  const token = await getAccessToken();
  return token ? `&token=${encodeURIComponent(token)}` : '';
}

/** Скачивание файла с авторизацией — обычная навигация `<a href>` не может
 * выставить заголовок Authorization, поэтому качаем через fetch и отдаём
 * пользователю Blob через синтетическую ссылку. */
export async function downloadWithAuth(url, filename) {
  const res = await authedFetch(url);
  if (!res.ok) throw new Error(`download failed: ${res.status}`);
  const blob = await res.blob();
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = blobUrl;
  a.download = filename || '';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(blobUrl);
}
