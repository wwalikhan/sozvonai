// session.js — тонкая обёртка над Supabase Auth (ES-модуль).
//
// Когда backend работает в локальном режиме (`auth_enabled: false`, SQLite,
// значение по умолчанию) — весь модуль превращается в набор безопасных
// no-op'ов, чтобы вызывающему коду не нужно было ветвиться на каждой странице.
// Когда `auth_enabled: true` — лениво создаём клиент @supabase/supabase-js
// (CDN ESM-сборка) и делегируем ему логин/сессию/refresh токена.

let configPromise = null;
let supabaseClientPromise = null;

async function loadConfig() {
  if (!configPromise) {
    configPromise = fetch('/config')
      .then((res) => {
        if (!res.ok) throw new Error('bad /config status');
        return res.json();
      })
      .catch(() => ({
        auth_enabled: false,
        supabase_url: null,
        supabase_anon_key: null,
        live_enabled: true,
        direct_upload_enabled: false,
      }));
  }
  return configPromise;
}

async function getSupabaseClient() {
  const config = await loadConfig();
  if (!config.auth_enabled) return null;

  if (!supabaseClientPromise) {
    supabaseClientPromise = import('https://esm.sh/@supabase/supabase-js@2')
      .then(({ createClient }) => createClient(config.supabase_url, config.supabase_anon_key));
  }
  return supabaseClientPromise;
}

/** Загружает /config один раз и кеширует результат на всю жизнь страницы. */
export async function isAuthEnabled() {
  const config = await loadConfig();
  return Boolean(config.auth_enabled);
}

/** false только в облачном деплое (CLOUD_MODE) — live-звонки там недоступны
 * (WebSocket не работает на serverless-хостинге). По умолчанию true — на
 * случай устаревшего кешированного /config без этого поля. */
export async function isLiveEnabled() {
  const config = await loadConfig();
  return config.live_enabled !== false;
}

/** true только в облачном деплое (CLOUD_MODE) — там тело запроса к самой Vercel-
 * функции ограничено ~4.5МБ (платформенный лимит), поэтому файл грузится
 * напрямую в Supabase Storage по signed URL, а не через POST /calls/upload. */
export async function isDirectUploadEnabled() {
  const config = await loadConfig();
  return Boolean(config.direct_upload_enabled);
}

/** Текущая сессия Supabase (или null — как в локальном режиме, так и без логина). */
export async function getSession() {
  const client = await getSupabaseClient();
  if (!client) return null;
  const { data, error } = await client.auth.getSession();
  if (error) return null;
  return data.session || null;
}

/** access_token текущей сессии (или null). */
export async function getAccessToken() {
  const session = await getSession();
  return session ? session.access_token : null;
}

/** Подписка на изменения состояния авторизации. В локальном режиме — no-op,
 * возвращает функцию-заглушку для отписки, чтобы вызывающий код не падал. */
export async function onAuthStateChange(cb) {
  const client = await getSupabaseClient();
  if (!client) return () => {};
  const { data } = client.auth.onAuthStateChange((_event, session) => cb(session));
  return () => data.subscription.unsubscribe();
}

export async function signInWithPassword(email, password) {
  const client = await getSupabaseClient();
  if (!client) return { error: null };
  return client.auth.signInWithPassword({ email, password });
}

export async function signUp(email, password) {
  const client = await getSupabaseClient();
  if (!client) return { error: null };
  return client.auth.signUp({ email, password });
}

export async function signOut() {
  const client = await getSupabaseClient();
  if (!client) return;
  await client.auth.signOut();
}
