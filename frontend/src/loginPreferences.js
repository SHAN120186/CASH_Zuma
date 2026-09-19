const recentLoginKey = 'zuma-recent-logins-v1';
const browserStorage = () => window.localStorage;

function normalizeLogins(values) {
  if (!Array.isArray(values)) return [];
  return [...new Set(values
    .filter(value => typeof value === 'string')
    .map(value => value.trim())
    .filter(value => value.length > 0 && value.length <= 80))].slice(0, 3);
}

export function loadRecentLogins(getStorage = browserStorage) {
  try {
    return normalizeLogins(JSON.parse(getStorage().getItem(recentLoginKey) || '[]'));
  } catch {
    return [];
  }
}

// Call only after successful authentication. Storage failures must not block login.
export function rememberLogin(username, current, getStorage = browserStorage) {
  const next = normalizeLogins([username, ...normalizeLogins(current)]);
  try {
    getStorage().setItem(recentLoginKey, JSON.stringify(next));
  } catch {
    // Private browsing or a full storage quota: keep suggestions for this tab.
  }
  return next;
}

export function matchingLogins(recent, query) {
  const search = query.trim().toLocaleLowerCase();
  return normalizeLogins(recent).filter(name => name.toLocaleLowerCase().includes(search));
}

// The browser owns password storage and consent. Never persist passwords ourselves
// or wait for a password-manager prompt before opening the application.
export function offerPasswordSave({username, password}, browser = window) {
  try {
    if (!browser.isSecureContext || !browser.PasswordCredential ||
        !browser.navigator.credentials?.store) return;
    const credential = new browser.PasswordCredential({id: username, password, name: username});
    Promise.resolve(browser.navigator.credentials.store(credential)).catch(() => {});
  } catch {
    // Native form autocomplete remains available according to browser settings.
  }
}
