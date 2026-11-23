/* Progressive enhancement: the Flask forms continue working without JavaScript.
   Keep the server authoritative for saved collection state and filter results. */
document.addEventListener('DOMContentLoaded', () => {
  // Mobile navigation is independent of the page's collection widgets.
  const mobileToggle = document.querySelector('#mobile-menu-toggle');
  const mobileMenu = document.querySelector('#mobile-menu');
  const mobileBackdrop = document.querySelector('#mobile-menu-backdrop');
  const mobileClose = document.querySelector('#mobile-menu-close');
  function setMobileMenu(open) {
    if (!mobileToggle || !mobileMenu || !mobileBackdrop) return;
    mobileMenu.hidden = !open;
    mobileBackdrop.hidden = !open;
    mobileToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    mobileToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    if (open) mobileClose?.focus();
    else mobileToggle.focus();
  }
  mobileToggle?.addEventListener('click', () => setMobileMenu(mobileMenu.hidden));
  mobileBackdrop?.addEventListener('click', () => setMobileMenu(false));
  mobileClose?.addEventListener('click', () => setMobileMenu(false));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && mobileMenu && !mobileMenu.hidden) setMobileMenu(false);
  });

  // Last-known checkmarks are scoped by account and collection to this browser.
  // This allows an OPEN collection page to remain usable if the Pi disconnects.
  // Unsynced changes are explicitly marked pending until the server confirms them.
  const offlineUser = document.body.dataset.user;
  const offlineInstall = document.body.dataset.install;
  const offlinePrefix = offlineUser && offlineInstall ? `acnh-tracker:offline:${offlineInstall}:${offlineUser}:` : null;
  const pendingKey = offlinePrefix ? `${offlinePrefix}pending` : null;
  const offlineStatus = document.querySelector('#offline-status');
  const offlineStatusMessage = document.querySelector('#offline-status-message');
  const offlineRetry = document.querySelector('#offline-retry');
  let syncing = false;
  function getStored(key) {
    if (!key) return {};
    try {
      const saved = JSON.parse(localStorage.getItem(key) || '{}');
      return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
    } catch (_) { return {}; }
  }
  function setStored(key, value) {
    if (!key) return;
    try { localStorage.setItem(key, JSON.stringify(value)); }
    catch (_) { /* Browser storage may be disabled; server still works. */ }
  }
  function markOffline(message) {
    if (!offlineStatus) return;
    offlineStatus.hidden = false;
    if (offlineStatusMessage) offlineStatusMessage.textContent = message;
  }
  function markOnline() {
    if (offlineStatus) offlineStatus.hidden = true;
  }
  function cardSnapshotKey(category) { return offlinePrefix ? `${offlinePrefix}${category}` : null; }
  function changeCard(card, owned) {
    if (!card) return;
    card.dataset.itemOwned = owned ? '1' : '0';
    card.classList.toggle('is-owned', owned);
    const input = card.querySelector('input[name="have"]');
    const button = card.querySelector('.collect-button');
    if (input) input.value = owned ? '0' : '1';
    if (button) {
      button.classList.toggle('collected', owned);
      button.setAttribute('aria-pressed', owned ? 'true' : 'false');
      button.textContent = owned ? '✓ Collected' : '+ Mark collected';
    }
  }
  function saveCardSnapshot(card) {
    if (!card || !offlinePrefix) return;
    const {itemCategory: category, itemName: name} = card.dataset;
    if (!category || !name) return;
    const key = cardSnapshotKey(category);
    const records = getStored(key);
    records[name] = card.dataset.itemOwned === '1';
    setStored(key, records);
  }
  function cacheVisibleCards() {
    if (!offlinePrefix) return;
    document.querySelectorAll('.item-card[data-item-name]').forEach(saveCardSnapshot);
  }
  function queuedId(category, name) { return `${category}\u0000${name}`; }
  function applyOfflineQueueToCards() {
    const queued = getStored(pendingKey);
    document.querySelectorAll('.item-card[data-item-name]').forEach(card => {
      const {itemCategory: category, itemName: name} = card.dataset;
      const entry = queued[queuedId(category, name)];
      if (entry && typeof entry.owned === 'boolean') changeCard(card, entry.owned);
    });
  }
  function queueLocalChange(card, owned) {
    if (!offlinePrefix || !card) return false;
    const {itemCategory: category, itemName: name} = card.dataset;
    if (!category || !name) return false;
    const queued = getStored(pendingKey);
    queued[queuedId(category, name)] = {category, name, owned};
    setStored(pendingKey, queued);
    changeCard(card, owned);
    saveCardSnapshot(card);
    markOffline('Pi is unreachable. Your checkmark is saved in this browser and will sync when the connection returns.');
    return true;
  }
  function restoreCachedCheckmarks() {
    document.querySelectorAll('.item-card[data-item-name]').forEach(card => {
      const {itemCategory: category, itemName: name} = card.dataset;
      const cached = getStored(cardSnapshotKey(category));
      if (typeof cached[name] === 'boolean') changeCard(card, cached[name]);
    });
    applyOfflineQueueToCards();
  }
  async function syncOfflineQueue() {
    if (syncing || !pendingKey || !navigator.onLine) return;
    const queued = getStored(pendingKey);
    if (!Object.keys(queued).length) return;
    syncing = true;
    let completed = 0;
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
    try {
      if (!csrf) return;
      for (const [id, entry] of Object.entries(queued)) {
        if (!entry || !entry.category || !entry.name || typeof entry.owned !== 'boolean') continue;
        const form = new FormData();
        form.append('_csrf', csrf);
        form.append('category', entry.category);
        form.append('name', entry.name);
        form.append('have', entry.owned ? '1' : '0');
        let response;
        try {
          response = await fetch('/collection/set', {
            method:'POST', body:form, credentials:'same-origin',
            headers:{'Accept':'application/json'}, signal:AbortSignal.timeout(10000)
          });
        } catch (_) { break; }
        if (!response.ok) break;
        let result;
        try { result = await response.json(); }
        catch (_) { break; /* An expired login can return an HTML page. */ }
        if (!result.ok) break;
        // A change may have been edited in another tab while syncing. Never
        // remove a newer pending state that differs from this request.
        const latest = getStored(pendingKey);
        if (latest[id] && latest[id].owned === entry.owned) {
          delete latest[id];
          setStored(pendingKey, latest);
          completed += 1;
        }
      }
      const remaining = Object.keys(getStored(pendingKey)).length;
      if (remaining) markOffline(`${remaining} local checkmark change(s) pending. Retry after the Pi is reachable and you are signed in.`);
      else {
        markOnline();
        if (completed && results) await replaceCollectionResults(location.href);
      }
    } finally { syncing = false; }
  }
  offlineRetry?.addEventListener('click', () => syncOfflineQueue());
  window.addEventListener('online', () => syncOfflineQueue());
  document.querySelector('#clear-offline-cache')?.addEventListener('click', () => {
    if (!offlinePrefix) return;
    if (!window.confirm('Clear offline checkmarks and pending changes for this account on this browser? Unsynced changes will be lost.')) return;
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(offlinePrefix)) localStorage.removeItem(key);
    }
    markOnline();
    alert('Browser-local offline data cleared. Your saved Pi data has not been changed.');
  });
  if (offlinePrefix) {
    // Cache states that the server rendered, then overlay any pending offline
    // changes. Never replace an unsynced user action with an older server value.
    cacheVisibleCards();
    applyOfflineQueueToCards();
    if (Object.keys(getStored(pendingKey)).length) {
      markOffline('Local checkmark changes are waiting to sync with your Pi.');
      setTimeout(syncOfflineQueue, 700);
    }
    setInterval(() => {
      if (!document.hidden && Object.keys(getStored(pendingKey)).length) syncOfflineQueue();
    }, 20000);
  }

  // Choose the browser's IANA zone without reading the server clock.
  document.querySelector('#detect-timezone')?.addEventListener('click', () => {
    const field = document.querySelector('#timezone-field');
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (field && zone) field.value = zone;
    else alert('Your browser did not provide a time zone. Enter an IANA zone manually.');
  });

  // An open automatic checklist should not show yesterday's tasks after midnight.
  const dailyChecklist = document.querySelector('#daily-checklist[data-reset-mode="automatic"]');
  if (dailyChecklist) {
    const checkNewDay = () => {
      if (document.hidden) return;
      try {
        const parts = new Intl.DateTimeFormat('en-US', {
          timeZone: dailyChecklist.dataset.timezone, year:'numeric', month:'2-digit', day:'2-digit'
        }).formatToParts(new Date());
        const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
        const today = `${values.year}-${values.month}-${values.day}`;
        if (today !== dailyChecklist.dataset.day) window.location.reload();
      } catch (_) { /* Server timezone remains authoritative. */ }
    };
    document.addEventListener('visibilitychange', checkNewDay);
    setInterval(checkNewDay, 60000);
  }

  const facetField = document.querySelector('#facet-field');
  const facetValue = document.querySelector('#facet-value');
  if (facetField && facetValue) {
    const updateChoices = () => {
      for (const option of facetValue.querySelectorAll('option[data-field]')) {
        option.hidden = option.dataset.field !== facetField.value;
        option.disabled = option.hidden;
      }
      if (facetValue.selectedOptions[0]?.disabled) facetValue.value = '';
    };
    facetField.addEventListener('change', () => { facetValue.value = ''; updateChoices(); });
    updateChoices();
  }

  const filters = document.querySelector('#filters[data-category][data-user]');
  let results = document.querySelector('#collection-results');
  let inFlight = null;
  let requestSerial = 0;
  let debounce = null;
  const preferencesKey = filters
    ? `acnh-tracker:filters:${filters.dataset.user}:${filters.dataset.category}`
    : null;

  function savePreferences(url) {
    if (!preferencesKey) return;
    try {
      const params = new URL(url).searchParams;
      params.delete('page');
      params.delete('limit');
      params.delete('view');
      params.delete('reset');
      localStorage.setItem(preferencesKey, params.toString() ? '?' + params : '');
    } catch (_error) { /* localStorage may be disabled */ }
  }

  if (filters) {
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.has('reset')) {
        localStorage.removeItem(preferencesKey);
        history.replaceState(null, '', window.location.pathname);
      } else if (!params.toString()) {
        const saved = localStorage.getItem(preferencesKey);
        if (saved && saved.startsWith('?')) {
          window.location.replace(window.location.pathname + saved);
          return;
        }
      } else {
        savePreferences(window.location.href);
      }
    } catch (_error) { /* URL filters still work */ }
  }

  async function replaceCollectionResults(url, {updateUrl = false, push = false} = {}) {
    if (!results) return false;
    if (inFlight) inFlight.abort();
    inFlight = new AbortController();
    const currentRequest = ++requestSerial;
    results.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(url, {
        credentials: 'same-origin', headers: {'Accept':'text/html'},
        signal: inFlight.signal
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const nextResults = doc.querySelector('#collection-results');
      if (!nextResults) {
        // In particular, a timed-out session redirects to /login.
        window.location.assign(response.url || url);
        return false;
      }
      if (currentRequest !== requestSerial) return false;
      results.replaceWith(nextResults);
      results = nextResults;
      cacheVisibleCards();
      applyOfflineQueueToCards();
      if (!Object.keys(getStored(pendingKey)).length) markOnline();
      const nextProgress = doc.querySelector('#collection-progress');
      const progress = document.querySelector('#collection-progress');
      if (nextProgress && progress) progress.replaceWith(nextProgress);
      if (updateUrl) {
        const stateUrl = new URL(url, location.origin);
        if (push) history.pushState(null, '', stateUrl);
        else history.replaceState(null, '', stateUrl);
        savePreferences(stateUrl.href);
      }
      return true;
    } catch (error) {
      if (error.name === 'AbortError') return false;
      console.warn('Collection refresh failed:', error);
      if (results) results.setAttribute('data-refresh-error', 'true');
      restoreCachedCheckmarks();
      markOffline('Collection page unavailable. Showing saved browser checkmarks; the Pi may be offline.');
      return false;
    } finally {
      if (currentRequest === requestSerial && results) {
        results.setAttribute('aria-busy', 'false');
      }
    }
  }

  function urlFromFilters() {
    const url = new URL(filters.action || location.pathname, location.origin);
    const data = new FormData(filters);
    for (const [key, value] of data.entries()) {
      if (value !== '') url.searchParams.append(key, value);
    }
    return url;
  }

  if (filters && results) {
    const applyFilters = () => replaceCollectionResults(urlFromFilters().href, {updateUrl:true});
    filters.addEventListener('submit', event => { event.preventDefault(); clearTimeout(debounce); applyFilters(); });
    filters.addEventListener('input', event => {
      if (!event.target.matches('input[type="search"], input[name="q"]')) return;
      clearTimeout(debounce);
      debounce = setTimeout(applyFilters, 320);
    });
    filters.addEventListener('change', event => {
      if (event.target.name === 'q') return;
      clearTimeout(debounce);
      applyFilters();
    });
    window.addEventListener('popstate', () => window.location.reload());
  }

  // A manually deleted/corrupt image should not leave a broken-image icon.
  document.addEventListener('error', event => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement) || !img.classList.contains('item-artwork')) return;
    const fallback = document.createElement('div');
    fallback.className = 'item-artwork-placeholder';
    fallback.setAttribute('role', 'img');
    fallback.setAttribute('aria-label', 'Artwork unavailable');
    const icon = document.createElement('span');
    icon.textContent = '🖼️';
    const caption = document.createElement('small');
    caption.textContent = 'Artwork unavailable';
    fallback.append(icon, caption);
    img.replaceWith(fallback);
  }, true);

  // Click-to-collect works on cards inserted by a live filter update as well.
  document.addEventListener('click', event => {
    const pageLink = event.target.closest('a[data-collection-page]');
    if (pageLink && results && event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
      event.preventDefault();
      replaceCollectionResults(pageLink.href, {updateUrl:true, push:true});
      return;
    }
    const card = event.target.closest('.item-card');
    if (!card) return;
    if (event.target.closest('form, button, input, a, details, summary, select')) return;
    if (window.getSelection?.()?.toString()) return;
    card.querySelector('.collect-form button[type="submit"]')?.click();
  });

  // Event delegation matters: results are replaced after live filters/checkmarks.
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.matches('.collect-form, .task-form')) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (!button || button.disabled) return;
    button.disabled = true;
    try {
      const response = await fetch(form.action, {
        method:'POST', body:new FormData(form),
        headers:{'Accept':'application/json'}, credentials:'same-origin',
        signal:typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(12000) : undefined
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!data.ok) throw new Error('The update was not saved');
      const input = form.querySelector('input[name="have"], input[name="done"]');
      if (input?.name === 'have') {
        // Only change the UI after a successful server acknowledgement.
        const nowOwned = Boolean(data.owned);
        const wasOwned = input.value === '0';
        input.value = nowOwned ? '0' : '1';
        button.classList.toggle('collected', nowOwned);
        button.closest('.item-card')?.classList.toggle('is-owned', nowOwned);
        button.textContent = nowOwned ? '✓ Collected' : '+ Mark collected';
        button.setAttribute('aria-pressed', nowOwned ? 'true' : 'false');
        const savedCard = button.closest('.item-card');
        if (savedCard) {
          savedCard.dataset.itemOwned = nowOwned ? '1' : '0';
          saveCardSnapshot(savedCard);
        }
        const progress = document.querySelector('#collection-progress');
        if (progress && nowOwned !== wasOwned) {
          const total = Number(progress.dataset.total);
          const owned = Number(progress.dataset.owned) + (nowOwned ? 1 : -1);
          if (Number.isFinite(total) && Number.isFinite(owned)) {
            progress.dataset.owned = String(owned);
            progress.textContent = `${owned} / ${total} collected · ${progress.textContent.split('·').slice(1).join('·').trim()}`;
            const remaining = document.querySelector('#collection-remaining');
            if (remaining) {
              remaining.dataset.remaining = String(total - owned);
              remaining.textContent = remaining.textContent.replace(/\d+ still to collect/, `${total - owned} still to collect`);
            }
          }
        }
        if (results) {
          // Refresh on the *current* filter/page so "Uncollected" membership,
          // group totals, pagination and progress all stay accurate.
          await replaceCollectionResults(window.location.href);
        }
      } else if (input?.name === 'done') {
        const done = input.value === '1';
        input.value = done ? '0' : '1';
        form.classList.toggle('done', done);
        form.querySelector('.task-check').textContent = done ? '✓' : '+';
      }
    } catch (error) {
      console.warn('Could not save item:', error);
      const card = form.closest('.item-card');
      const itemValue = form.querySelector('input[name="have"]');
      const isNetworkFailure = error instanceof TypeError || error.name === 'AbortError' || error.name === 'TimeoutError' || !navigator.onLine;
      if (card && itemValue && isNetworkFailure && queueLocalChange(card, itemValue.value === '1')) {
        // This is explicitly pending; do not tell the user the Pi saved it.
      } else {
        alert('Could not save this change on the Pi. Please refresh or sign in again.');
      }
    } finally {
      button.disabled = false;
    }
  });
});
