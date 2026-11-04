'use strict';

(() => {
  // ====================================================================
  // Utilitaires
  // ====================================================================
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const icon = (name, cls = '') => `<svg class="ico ${cls}"><use href="#i-${name}"/></svg>`;

  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem('hm.' + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
    },
    set(key, value) { try { localStorage.setItem('hm.' + key, JSON.stringify(value)); } catch { /* navigation privée */ } },
  };

  const pad = (n) => String(n).padStart(2, '0');
  const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const parseYmd = (s) => { const [y, m, d] = s.slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
  const parseDT = (s) => { const d = parseYmd(s); const [h, mi] = (s.slice(11, 16) || '00:00').split(':').map(Number); d.setHours(h, mi, 0, 0); return d; };
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const firstOfMonth = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
  const diffDays = (a, b) => Math.round((parseYmd(a) - parseYmd(b)) / 864e5);

  const fmtDayLong = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });
  const fmtMonth = new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric' });
  const fmtShort = new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short' });
  const fmtClock = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });

  function relDay(day, today) {
    const n = diffDays(day, today);
    if (n === 0) return "aujourd'hui";
    if (n === 1) return 'demain';
    if (n === -1) return 'hier';
    if (n > 1 && n < 7) return fmtShort.format(parseYmd(day)).split(' ')[0].replace('.', '');
    return n > 0 ? `dans ${n} j` : `il y a ${-n} j`;
  }

  const COLORS = ['#4655d4', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#8b5cf6', '#64748b'];
  const VIEWS = { home: 'Accueil', calendar: 'Calendrier', chores: 'Tâches ménagères', shopping: 'Courses', history: 'Historique' };
  const ROOMS = ['Cuisine', 'Salon', 'Salle de bain', 'Toilettes', 'Chambres', 'Entrée', 'Buanderie', 'Extérieur', 'Voiture'];
  const RECURRENCES = { none: 'Ne se répète pas', daily: 'Tous les jours', weekly: 'Toutes les semaines', monthly: 'Tous les mois', yearly: 'Tous les ans' };
  const ALERTS_TIMED = [[0, 'Au début'], [5, '5 min avant'], [15, '15 min avant'], [30, '30 min avant'], [60, '1 h avant'], [120, '2 h avant'], [1440, '1 jour avant'], [2880, '2 jours avant']];
  const ALERTS_ALLDAY = [[-540, 'Le jour même (9 h)'], [900, 'La veille (9 h)'], [2340, '2 jours avant (9 h)'], [9540, '1 semaine avant (9 h)']];
  const MODULES = { calendar: ['📅', 'Calendrier'], chores: ['🧹', 'Tâches'], shopping: ['🛒', 'Courses'], settings: ['⚙️', 'Réglages'] };
  const LIST_ICONS = ['🛒', '🥕', '🏪', '💊', '🏠', '🎁', '🐾', '🍷', '🧴', '📦'];

  // ====================================================================
  // État
  // ====================================================================
  const now = new Date();
  const state = {
    view: VIEWS[store.get('view')] ? store.get('view') : 'home',
    me: store.get('me', ''),
    members: [],
    rev: -1,
    authRequired: false,
    cal: { month: firstOfMonth(now), selected: ymd(now), events: [] },
    chores: { list: [], today: ymd(now), filter: store.get('choresFilter', 'all'), showDone: false, stats: [] },
    shop: { lists: [], categories: [], current: store.get('list', null), items: Object.assign([], { loading: true }), frequent: [], draft: '', draftQty: '' },
    hist: { items: [], module: '', more: false },
    home: { events: [], items: {}, loaded: false },
  };
  const memberById = (id) => state.members.find((m) => m.id === id);

  // ====================================================================
  // API
  // ====================================================================
  function setSync(status) {
    const dot = $('#sync-dot');
    dot.className = 'sync-dot' + (status === 'ok' ? '' : ' ' + status);
    dot.title = status === 'offline' ? 'Hors ligne' : status === 'busy' ? 'Synchronisation…' : 'Synchronisé';
  }

  async function api(method, path, body) {
    const opts = { method, credentials: 'same-origin', headers: { 'X-User': encodeURIComponent(state.me) } };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, opts);
    } catch {
      setSync('offline');
      throw new Error('Connexion impossible. Vérifiez le réseau.');
    }
    setSync('ok');
    if (res.status === 401) { showLogin(); throw new Error('Authentification requise.'); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Erreur ${res.status}`);
    return data;
  }

  /** Exécute une modification puis resynchronise ; affiche l'erreur éventuelle. */
  async function mutate(fn) {
    try {
      const result = await fn();
      await sync(true);
      return result;
    } catch (err) {
      toast(err.message, { error: true });
      throw err;
    }
  }

  // ====================================================================
  // Chargement des données
  // ====================================================================
  function calRange() {
    const first = state.cal.month;
    const start = addDays(first, -((first.getDay() + 6) % 7));
    const last = new Date(first.getFullYear(), first.getMonth() + 1, 0);
    return [start, addDays(last, 6 - ((last.getDay() + 6) % 7))];
  }

  async function loadCalendar() {
    const [start, end] = calRange();
    state.cal.events = await api('GET', `/api/events?start=${ymd(start)}&end=${ymd(end)}`);
  }

  async function loadChores() {
    const [data, stats] = await Promise.all([
      api('GET', '/api/chores' + (state.chores.showDone ? '?include_done=1' : '')),
      api('GET', '/api/chores/stats?days=30'),
    ]);
    state.chores.list = data.chores;
    state.chores.today = data.today;
    state.chores.stats = stats.per_person;
  }

  async function loadShopping() {
    const data = await api('GET', '/api/lists');
    state.shop.lists = data.lists;
    state.shop.categories = data.categories;
    if (!data.lists.some((l) => l.id === state.shop.current)) state.shop.current = data.lists[0]?.id ?? null;
    if (state.view === 'shopping' && state.shop.current) {
      [state.shop.items, state.shop.frequent] = await Promise.all([
        api('GET', `/api/lists/${state.shop.current}/items`),
        api('GET', '/api/catalog?limit=24'),
      ]);
    }
  }

  async function loadHistory(append = false) {
    const before = append && state.hist.items.length ? `&before=${state.hist.items.at(-1).id}` : '';
    const data = await api('GET', `/api/history?module=${state.hist.module}&limit=50${before}`);
    state.hist.items = append ? state.hist.items.concat(data.items) : data.items;
    state.hist.more = data.more;
  }

  async function loadHome() {
    const today = new Date();
    const [events, ...items] = await Promise.all([
      api('GET', `/api/events?start=${ymd(today)}&end=${ymd(addDays(today, 7))}`),
      ...state.shop.lists.map((l) => api('GET', `/api/lists/${l.id}/items`)),
    ]);
    state.home.events = events;
    state.home.items = Object.fromEntries(state.shop.lists.map((l, i) => [l.id, items[i]]));
    state.home.loaded = true;
  }

  async function loadAll() {
    const tasks = [
      api('GET', '/api/members').then((m) => { state.members = m; }),
      loadChores(),
      loadShopping().then(() => (state.view === 'home' ? loadHome() : null)),
    ];
    if (state.view === 'calendar') tasks.push(loadCalendar());
    if (state.view === 'history') tasks.push(loadHistory());
    await Promise.all(tasks);
  }

  let syncing = null;
  async function sync(force = false) {
    if (syncing) {
      await syncing;
      if (!force) return;
    }
    while (syncing) await syncing;
    syncing = (async () => {
      try {
        const { rev } = await api('GET', '/api/sync');
        if (force || rev !== state.rev) {
          setSync('busy');
          await loadAll();
          state.rev = rev;
          setSync('ok');
          render();
        }
      } catch { /* hors ligne : on réessaiera */ } finally { syncing = null; }
    })();
    return syncing;
  }

  // ====================================================================
  // Rendu général
  // ====================================================================
  function render() {
    $('#view-title').textContent = VIEWS[state.view];
    document.title = `${VIEWS[state.view]} · Maison`;
    $$('.nav-item[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === state.view));
    $('#fab').hidden = state.view === 'history' || state.view === 'home';
    $('.topbar').classList.toggle('is-home', state.view === 'home');
    renderBadges();
    const main = $('#main');
    const focused = document.activeElement?.name === 'name' && main.contains(document.activeElement);
    main.innerHTML = { home: renderHome, calendar: renderCalendar, chores: renderChores, shopping: renderShopping, history: renderHistory }[state.view]();
    if (focused) $('#quick-add input[name=name]')?.focus();
  }

  function renderBadges() {
    const due = state.chores.list.filter((c) => c.active && c.due_date && c.due_date <= state.chores.today).length;
    const remaining = state.shop.lists.reduce((sum, l) => sum + l.remaining, 0);
    setBadge('#badge-chores', due);
    setBadge('#badge-shopping', remaining);
  }

  function setBadge(sel, n) {
    const el = $(sel);
    el.hidden = !n;
    el.textContent = n > 99 ? '99+' : n;
  }

  const memberTag = (id) => {
    const m = memberById(id);
    return m ? `<span class="tag"><i class="dot" style="background:${m.color}"></i>${esc(m.name)}</span>` : '';
  };

  const memberOptions = (selected) => `<option value="">—</option>` + state.members
    .map((m) => `<option value="${m.id}" ${m.id === selected ? 'selected' : ''}>${esc(m.name)}</option>`).join('');

  // ====================================================================
  // Accueil (tableau de bord)
  // ====================================================================
  const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const fmtHeroDate = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

  function greeting() {
    const h = new Date().getHours();
    const hello = h < 5 ? 'Bonne nuit' : h < 18 ? 'Bonjour' : 'Bonsoir';
    return state.me ? `${hello} ${state.me}` : hello;
  }

  /** Alertes des 7 prochains jours (et celles de la dernière demi-heure). */
  function upcomingAlerts() {
    const nowTs = Date.now();
    const out = [];
    for (const ev of state.home.events) {
      for (const m of ev.alerts) {
        const at = parseDT(ev.occurrence_start).getTime() - m * 60000;
        if (at > nowTs - 30 * 60000 && at < nowTs + 7 * 864e5) out.push({ at, ev });
      }
    }
    return out.sort((a, b) => a.at - b.at);
  }

  function whenLabel(ts) {
    const mins = Math.round((ts - Date.now()) / 60000);
    if (mins < 0) return `il y a ${-mins} min`;
    if (mins === 0) return 'maintenant';
    if (mins < 60) return `dans ${mins} min`;
    const d = new Date(ts);
    const day = relDay(ymd(d), ymd(new Date()));
    return `${day === "aujourd'hui" ? "aujourd'hui" : day} à ${hm(d)}`;
  }

  const widgetHead = (emoji, title, view, extra = '') => `<div class="widget-head">
      <h2>${emoji} ${title}</h2>${extra}
      ${view ? `<button class="btn ghost small" data-action="go" data-view="${view}">Tout voir ${icon('right', 'sm')}</button>` : ''}
    </div>`;

  function renderHome() {
    const n = new Date();
    const today = ymd(n);
    const nowStr = `${today}T${hm(n)}`;
    const loading = !state.home.loaded;

    // Agenda : événements pas encore terminés, rangés par jour (un événement en cours apparaît aujourd'hui).
    const agenda = state.home.events
      .filter((ev) => ev.occurrence_end >= nowStr)
      .map((ev) => ({ ev, day: ev.occurrence_start.slice(0, 10) < today ? today : ev.occurrence_start.slice(0, 10) }))
      .sort((a, b) => a.day.localeCompare(b.day) || b.ev.all_day - a.ev.all_day || a.ev.occurrence_start.localeCompare(b.ev.occurrence_start))
      .slice(0, 8);
    const todayCount = state.home.events.filter((ev) => ev.occurrence_start.slice(0, 10) <= today && ev.occurrence_end.slice(0, 10) >= today).length;
    let lastDay = '';
    const agendaHtml = agenda.map(({ ev, day }) => {
      let head = '';
      if (day !== lastDay) {
        const label = day === today ? "Aujourd'hui" : diffDays(day, today) === 1 ? 'Demain' : capitalize(fmtDayLong.format(parseYmd(day)));
        head = `<li class="mini-day">${label}</li>`;
        lastDay = day;
      }
      return head + eventRow(ev, day);
    }).join('');

    // Alertes à venir
    const alerts = upcomingAlerts().slice(0, 6);
    const alertsHtml = alerts.map(({ at, ev }) => {
      const mins = (at - Date.now()) / 60000;
      const cls = mins < 0 ? 'past' : mins < 60 ? 'soon' : '';
      return `<li class="alert-row ${cls}">
        <span class="alert-ico" style="--c:${ev.color}">${icon('bell', 'sm')}</span>
        <button class="row-main" data-action="edit-event" data-id="${ev.id}" data-occ="${ev.occurrence_start}">
          <div class="row-title">${esc(ev.title)}</div>
          <div class="row-meta">${ev.all_day ? 'Journée' : ev.occurrence_start.slice(11, 16)} · ${fmtShort.format(parseDT(ev.occurrence_start))}</div>
        </button>
        <span class="alert-when">${whenLabel(at)}</span>
      </li>`;
    }).join('');

    // Tâches : en retard + aujourd'hui, sinon la prochaine à venir
    const chores = state.chores.list.filter((c) => c.active);
    const dueChores = chores.filter((c) => c.due_date && c.due_date <= today);
    const nextChore = chores.find((c) => c.due_date > today);
    const choresHtml = dueChores.slice(0, 6).map((c) => choreRow(c, today)).join('');

    // Courses : chaque liste avec ses articles restants
    const remaining = state.shop.lists.reduce((s, l) => s + l.remaining, 0);
    const shopHtml = state.shop.lists.map((l) => {
      // Les plus récents d'abord : un article ajouté depuis l'accueil y apparaît aussitôt.
      const todo = (state.home.items[l.id] || []).filter((i) => !i.checked).sort((a, b) => b.updated_at.localeCompare(a.updated_at) || b.id - a.id);
      if (!todo.length) return '';
      return `<li class="mini-day">${esc(l.icon)} ${esc(l.name)} <span class="count">${todo.length}</span></li>` +
        todo.slice(0, 5).map((i) => `<li class="row compact">
          <button class="check" data-action="toggle-item" data-id="${i.id}" aria-label="Acheté">${icon('check')}</button>
          <button class="row-main" data-action="open-list" data-id="${l.id}"><span class="row-title">${esc(i.name)}</span>${i.quantity ? ` <span class="item-qty">· ${esc(i.quantity)}</span>` : ''}</button>
        </li>`).join('') +
        (todo.length > 5 ? `<li class="more-row"><button class="btn ghost small" data-action="open-list" data-id="${l.id}">+ ${todo.length - 5} autre(s)</button></li>` : '');
    }).join('');
    const list = currentList();

    const stat = (view, emoji, count, label) => `<button class="stat" data-action="go" data-view="${view}"><b>${count}</b> ${emoji} ${label}</button>`;

    return `<div class="home">
      <section class="hero">
        <div>
          <div class="hero-time" id="home-time">${hm(n)}</div>
          <div class="hero-date" id="home-date">${capitalize(fmtHeroDate.format(n))}</div>
        </div>
        <div class="hero-side">
          <p class="hero-hello">${esc(greeting())} 👋</p>
          <div class="hero-stats">
            ${stat('calendar', '📅', todayCount, `événement${todayCount > 1 ? 's' : ''} aujourd'hui`)}
            ${stat('chores', '🧹', dueChores.length, `tâche${dueChores.length > 1 ? 's' : ''} à faire`)}
            ${stat('shopping', '🛒', remaining, `article${remaining > 1 ? 's' : ''} à acheter`)}
          </div>
        </div>
      </section>

      <section class="card widget">
        ${widgetHead('📅', 'Agenda', 'calendar', `<button class="icon-btn" data-action="home-new-event" aria-label="Ajouter un événement">${icon('plus', 'sm')}</button>`)}
        ${loading ? '<p class="empty small">Chargement…</p>' : agendaHtml ? `<ul class="list">${agendaHtml}</ul>` : '<p class="empty small">Rien de prévu cette semaine.</p>'}
      </section>

      <section class="card widget">
        ${widgetHead('🔔', 'Prochaines alertes', '')}
        ${loading ? '<p class="empty small">Chargement…</p>' : alertsHtml ? `<ul class="list">${alertsHtml}</ul>` : '<p class="empty small">Aucune alerte dans les 7 prochains jours.</p>'}
      </section>

      <section class="card widget">
        ${widgetHead('🧹', 'Tâches du jour', 'chores', `<button class="icon-btn" data-action="new-chore" aria-label="Ajouter une tâche">${icon('plus', 'sm')}</button>`)}
        ${choresHtml ? `<ul class="list">${choresHtml}</ul>${dueChores.length > 6 ? `<p class="more-row"><button class="btn ghost small" data-action="go" data-view="chores">+ ${dueChores.length - 6} autre(s)</button></p>` : ''}`
          : `<p class="empty small">✨ Rien à faire aujourd'hui.${nextChore ? `<br>Prochaine : <b>${esc(nextChore.title)}</b> ${relDay(nextChore.due_date, today)}` : ''}</p>`}
      </section>

      <section class="card widget">
        ${widgetHead('🛒', 'Courses', 'shopping')}
        <form id="quick-add" class="add-bar" data-form="quick-add" autocomplete="off">
          <input class="input" name="name" placeholder="Ajouter à « ${esc(list?.name || '')} »…" value="${esc(state.shop.draft)}" maxlength="100" enterkeyhint="done">
          <input class="input" name="quantity" placeholder="Qté" maxlength="40" value="${esc(state.shop.draftQty)}">
          <button class="btn primary" aria-label="Ajouter">${icon('plus', 'sm')}</button>
          <div class="suggest" id="suggest" hidden></div>
        </form>
        ${loading ? '<p class="empty small">Chargement…</p>' : shopHtml ? `<ul class="list">${shopHtml}</ul>` : '<p class="empty small">🧺 Toutes les listes sont vides.</p>'}
      </section>
    </div>`;
  }

  // ====================================================================
  // Calendrier
  // ====================================================================
  const eventsOn = (day) => state.cal.events.filter((ev) => ev.occurrence_start.slice(0, 10) <= day && ev.occurrence_end.slice(0, 10) >= day);

  function renderCalendar() {
    const { month, selected } = state.cal;
    const today = ymd(new Date());
    const [start, end] = calRange();
    let cells = '';
    for (let d = start; d <= end; d = addDays(d, 1)) {
      const key = ymd(d);
      const evs = eventsOn(key);
      const cls = [d.getMonth() !== month.getMonth() && 'other', key === today && 'today', key === selected && 'selected'].filter(Boolean).join(' ');
      cells += `<button class="cal-day ${cls}" data-action="pick-day" data-date="${key}" aria-label="${fmtDayLong.format(d)}, ${evs.length} événement(s)">
        <span class="num">${d.getDate()}</span>
        <span class="cal-dots">${evs.slice(0, 3).map((ev) => `<i style="background:${ev.color}"></i>`).join('')}</span>
        <span class="cal-pills">${evs.slice(0, 3).map((ev) => `<span class="pill ${ev.all_day ? 'allday' : ''}" style="--c:${ev.color}">${ev.all_day ? '' : esc(ev.occurrence_start.slice(11, 16)) + ' '}${esc(ev.title)}</span>`).join('')}
        ${evs.length > 3 ? `<span class="pill-more">+${evs.length - 3}</span>` : ''}</span>
      </button>`;
    }
    const dows = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'].map((d) => `<div class="cal-dow">${d}</div>`).join('');
    const dayEvents = eventsOn(selected);

    return `<div class="cal-layout">
      <section class="card">
        <div class="cal-head">
          <h2>${fmtMonth.format(month)}</h2>
          <button class="btn ghost" data-action="cal-today">Aujourd'hui</button>
          <button class="icon-btn" data-action="cal-nav" data-delta="-1" aria-label="Mois précédent">${icon('left')}</button>
          <button class="icon-btn" data-action="cal-nav" data-delta="1" aria-label="Mois suivant">${icon('right')}</button>
        </div>
        <div class="cal-grid">${dows}${cells}</div>
      </section>
      <section class="card">
        <div class="agenda-head">
          <h2>${fmtDayLong.format(parseYmd(selected))}</h2>
          <p class="muted small" style="margin:2px 0 0">${selected === today ? "Aujourd'hui" : relDay(selected, today)} · ${dayEvents.length || 'aucun'} événement${dayEvents.length > 1 ? 's' : ''}</p>
        </div>
        ${dayEvents.length ? `<ul class="list" style="padding-bottom:8px">${dayEvents.map((ev) => eventRow(ev, selected)).join('')}</ul>`
          : `<div class="empty"><span class="big">🗓️</span>Rien de prévu.<br><button class="btn primary" style="margin-top:14px" data-action="new-event">${icon('plus', 'sm')} Ajouter un événement</button></div>`}
      </section>
    </div>`;
  }

  function eventRow(ev, day) {
    const startsToday = ev.occurrence_start.slice(0, 10) === day;
    const endsToday = ev.occurrence_end.slice(0, 10) === day;
    let time;
    if (ev.all_day) time = 'Journée';
    else if (startsToday) time = `${ev.occurrence_start.slice(11, 16)}<small>${endsToday ? ev.occurrence_end.slice(11, 16) : '→'}</small>`;
    else time = endsToday ? `→<small>${ev.occurrence_end.slice(11, 16)}</small>` : 'Journée';
    const meta = [
      ev.location && `<span>${icon('pin', 'sm')} ${esc(ev.location)}</span>`,
      memberTag(ev.member_id),
      ev.alerts.length && `<span title="Alerte">${icon('bell', 'sm')}</span>`,
      ev.recurrence !== 'none' && `<span title="${RECURRENCES[ev.recurrence]}">${icon('repeat', 'sm')}</span>`,
    ].filter(Boolean).join('');
    return `<li class="row event-row" style="--c:${ev.color}">
      <div class="event-time">${time}</div>
      <button class="row-main" data-action="edit-event" data-id="${ev.id}" data-occ="${ev.occurrence_start}">
        <div class="row-title">${esc(ev.title)}</div>
        ${meta ? `<div class="row-meta">${meta}</div>` : ''}
      </button>
    </li>`;
  }

  const alertLabel = (m, allDay) => {
    const preset = (allDay ? ALERTS_ALLDAY : ALERTS_TIMED).find(([v]) => v === m);
    if (preset) return preset[1];
    if (m < 0) return `${-m} min après`;
    return m % 1440 === 0 ? `${m / 1440} j avant` : m % 60 === 0 ? `${m / 60} h avant` : `${m} min avant`;
  };

  function alertOptions(allDay, selected) {
    const values = (allDay ? ALERTS_ALLDAY : ALERTS_TIMED).map(([v]) => v);
    selected.forEach((v) => { if (!values.includes(v)) values.push(v); });
    return values.map((v) => `<label class="opt"><input type="checkbox" name="alerts" value="${v}" ${selected.includes(v) ? 'checked' : ''}><span>${alertLabel(v, allDay)}</span></label>`).join('');
  }

  function openEventForm(ev, occurrence) {
    const isNew = !ev;
    if (isNew) {
      const base = parseYmd(state.cal.selected);
      const n = new Date();
      if (state.cal.selected === ymd(n)) base.setHours(n.getHours() + 1, 0); else base.setHours(9, 0);
      const end = new Date(base.getTime() + 3600e3);
      ev = { title: '', all_day: false, start: `${ymd(base)}T${hm(base)}`, end: `${ymd(end)}T${hm(end)}`, location: '', description: '',
        color: COLORS[0], member_id: null, alerts: [15], recurrence: 'none', recurrence_until: null };
    }
    const recurring = ev.recurrence !== 'none';
    openSheet({
      title: isNew ? 'Nouvel événement' : "Modifier l'événement",
      body: `<form id="sheet-form" autocomplete="off">
        ${!isNew && recurring ? `<p class="tag" style="margin:0 0 12px">${icon('repeat', 'sm')} Les modifications s'appliquent à toute la série</p>` : ''}
        <label class="field"><span>Titre</span><input name="title" required maxlength="200" value="${esc(ev.title)}" placeholder="Rendez-vous, anniversaire…"></label>
        <label class="switch">Toute la journée <input type="checkbox" name="all_day" ${ev.all_day ? 'checked' : ''}></label>
        <div class="grid-2">
          <label class="field"><span>Début</span><input type="date" name="start_date" required value="${ev.start.slice(0, 10)}"></label>
          <label class="field timed" ${ev.all_day ? 'hidden' : ''}><span>Heure</span><input type="time" name="start_time" value="${ev.start.slice(11, 16)}"></label>
        </div>
        <div class="grid-2">
          <label class="field"><span>Fin</span><input type="date" name="end_date" required value="${ev.end.slice(0, 10)}"></label>
          <label class="field timed" ${ev.all_day ? 'hidden' : ''}><span>Heure</span><input type="time" name="end_time" value="${ev.end.slice(11, 16)}"></label>
        </div>
        <div class="field"><span>Alertes</span><div class="opt-group" id="alert-opts">${alertOptions(ev.all_day, ev.alerts)}</div></div>
        <div class="grid-2">
          <label class="field"><span>Répétition</span><select name="recurrence">${Object.entries(RECURRENCES).map(([k, v]) => `<option value="${k}" ${k === ev.recurrence ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
          <label class="field" id="until-field" ${recurring ? '' : 'hidden'}><span>Jusqu'au (facultatif)</span><input type="date" name="recurrence_until" value="${ev.recurrence_until || ''}"></label>
        </div>
        <div class="grid-2">
          <label class="field"><span>Lieu</span><input name="location" maxlength="200" value="${esc(ev.location)}"></label>
          <label class="field"><span>Pour qui</span><select name="member_id">${memberOptions(ev.member_id)}</select></label>
        </div>
        <div class="field"><span>Couleur</span><div class="swatches">${COLORS.map((c) => `<label class="swatch"><input type="radio" name="color" value="${c}" ${c === ev.color ? 'checked' : ''}><span style="--c:${c}"></span></label>`).join('')}</div></div>
        <label class="field"><span>Notes</span><textarea name="description" maxlength="2000">${esc(ev.description)}</textarea></label>
        <div class="form-actions">
          ${isNew ? '' : recurring
            ? `<button type="button" class="btn danger" data-del="occ">${icon('trash', 'sm')} Cette fois</button><button type="button" class="btn danger" data-del="all">Toute la série</button>`
            : `<button type="button" class="btn danger" data-del="all">${icon('trash', 'sm')} Supprimer</button>`}
          <button class="btn primary">${isNew ? 'Ajouter' : 'Enregistrer'}</button>
        </div>
      </form>`,
      onMount(form) {
        const f = form.elements;
        if (!ev.color || !COLORS.includes(ev.color)) f.color[0].checked = true;
        f.all_day.addEventListener('change', () => {
          $$('.timed', form).forEach((el) => { el.hidden = f.all_day.checked; });
          $('#alert-opts', form).innerHTML = alertOptions(f.all_day.checked, f.all_day.checked ? [900] : [15]);
        });
        f.recurrence.addEventListener('change', () => { $('#until-field', form).hidden = f.recurrence.value === 'none'; });
        // Garde la fin après le début.
        const keepOrder = () => {
          const start = `${f.start_date.value}T${f.start_time.value || '00:00'}`;
          const end = `${f.end_date.value}T${f.end_time.value || '00:00'}`;
          if (f.start_date.value && (!f.end_date.value || end < start)) {
            const e = new Date(parseDT(start).getTime() + 3600e3);
            f.end_date.value = f.all_day.checked ? f.start_date.value : ymd(e);
            f.end_time.value = hm(e);
          }
        };
        f.start_date.addEventListener('change', keepOrder);
        f.start_time.addEventListener('change', keepOrder);
        $$('[data-del]', form).forEach((btn) => btn.addEventListener('click', async () => {
          const one = btn.dataset.del === 'occ';
          if (!confirm(one ? 'Supprimer uniquement cette occurrence ?' : `Supprimer « ${ev.title} »${recurring ? ' et toutes ses répétitions' : ''} ?`)) return;
          await mutate(() => api('DELETE', `/api/events/${ev.id}${one ? `?occurrence=${encodeURIComponent(occurrence)}` : ''}`));
          closeSheet();
          toast('Événement supprimé');
        }));
      },
      async onSubmit(form) {
        const f = form.elements;
        const allDay = f.all_day.checked;
        const body = {
          title: f.title.value,
          all_day: allDay,
          start: `${f.start_date.value}T${allDay ? '00:00' : f.start_time.value || '00:00'}`,
          end: `${f.end_date.value || f.start_date.value}T${allDay ? '23:59' : f.end_time.value || f.start_time.value || '00:00'}`,
          alerts: $$('[name=alerts]:checked', form).map((i) => Number(i.value)),
          recurrence: f.recurrence.value,
          recurrence_until: f.recurrence_until.value || null,
          location: f.location.value,
          member_id: f.member_id.value ? Number(f.member_id.value) : null,
          color: f.color.value || COLORS[0],
          description: f.description.value,
        };
        await mutate(() => (isNew ? api('POST', '/api/events', body) : api('PUT', `/api/events/${ev.id}`, body)));
        if (isNew) {
          state.cal.selected = body.start.slice(0, 10);
          state.cal.month = firstOfMonth(parseYmd(body.start));
          await sync(true);
        }
        toast(isNew ? 'Événement ajouté' : 'Événement enregistré');
        if (body.alerts.length) maybeAskNotifications();
      },
    });
  }

  // ====================================================================
  // Tâches ménagères
  // ====================================================================
  const UNITS = { day: ['jour', 'jours'], week: ['semaine', 'semaines'], month: ['mois', 'mois'] };

  function freqLabel(c) {
    if (c.freq_unit === 'none') return 'Une fois';
    const n = c.freq_interval;
    if (n === 1) return { day: 'Chaque jour', week: 'Chaque semaine', month: 'Chaque mois' }[c.freq_unit];
    return `Tous les ${n} ${UNITS[c.freq_unit][1]}`;
  }

  function renderChores() {
    const { list, today, filter, showDone, stats } = state.chores;
    const visible = list.filter((c) => filter === 'all' || c.member_id === Number(filter));
    const active = visible.filter((c) => c.active);
    const weekEnd = ymd(addDays(parseYmd(today), 7));
    const groups = [
      ['En retard', (c) => c.due_date && c.due_date < today, 'late'],
      ["Aujourd'hui", (c) => c.due_date === today],
      ['Cette semaine', (c) => c.due_date > today && c.due_date <= weekEnd],
      ['Plus tard', (c) => c.due_date > weekEnd],
      ['Sans échéance', (c) => !c.due_date],
    ];
    let body = groups.map(([title, test, cls]) => {
      const items = active.filter(test);
      return items.length ? `<h3 class="section-title ${cls || ''}">${title} <span class="count">${items.length}</span></h3>
        <ul class="list card">${items.map((c) => choreRow(c, today)).join('')}</ul>` : '';
    }).join('');
    if (showDone) {
      const done = visible.filter((c) => !c.active);
      body += `<h3 class="section-title">Terminées <span class="count">${done.length}</span></h3>` +
        (done.length ? `<ul class="list card">${done.map((c) => choreRow(c, today)).join('')}</ul>` : '<p class="muted small">Aucune tâche terminée.</p>');
    }
    if (!active.length && !showDone) {
      body = `<div class="card empty"><span class="big">✨</span>${list.length ? 'Tout est fait, bravo !' : 'Aucune tâche pour le moment.'}<br>
        <button class="btn primary" style="margin-top:14px" data-action="new-chore">${icon('plus', 'sm')} Ajouter une tâche</button></div>`;
    }

    const max = Math.max(1, ...stats.map((s) => s.count));
    const statsHtml = stats.length ? stats.map((s) => {
      const m = state.members.find((x) => x.name === s.done_by);
      return `<div class="stat-row"><span style="min-width:90px;font-weight:600">${esc(s.done_by || 'Anonyme')}</span>
        <span class="stat-bar"><span style="width:${(s.count / max) * 100}%;--c:${m ? m.color : 'var(--accent)'}"></span></span><b>${s.count}</b></div>`;
    }).join('') : '<p class="muted small">Aucune tâche réalisée ces 30 derniers jours.</p>';

    return `<div class="toolbar">
        <div class="chips">
          <button class="chip ${filter === 'all' ? 'active' : ''}" data-action="chore-filter" data-id="all">Tout le monde</button>
          ${state.members.map((m) => `<button class="chip ${filter === String(m.id) ? 'active' : ''}" data-action="chore-filter" data-id="${m.id}"><i class="dot" style="background:${m.color}"></i>${esc(m.name)}</button>`).join('')}
        </div>
        <label class="toggle"><span>Terminées</span><span class="switch" style="margin:0;min-height:0"><input type="checkbox" data-action="chore-show-done" ${showDone ? 'checked' : ''}></span></label>
      </div>
      <div class="chores-layout">
        <div>${body}</div>
        <aside class="card card-pad"><h3 style="font-size:1rem;margin-bottom:6px">🏆 30 derniers jours</h3>${statsHtml}</aside>
      </div>`;
  }

  function choreRow(c, today) {
    let due = '';
    if (!c.active) due = `<span class="tag done">Fait ${c.last_done_at ? relDay(c.last_done_at, today) : ''}</span>`;
    else if (c.due_date) {
      const cls = c.due_date < today ? 'late' : c.due_date === today ? 'today' : '';
      due = `<span class="tag ${cls}">${relDay(c.due_date, today)}</span>`;
    }
    const meta = [
      due,
      c.freq_unit !== 'none' && `<span>${icon('repeat', 'sm')} ${freqLabel(c)}</span>`,
      c.room && `<span>${esc(c.room)}</span>`,
      memberTag(c.member_id),
      c.active && c.last_done_by && `<span class="small">· fait par ${esc(c.last_done_by)} ${relDay(c.last_done_at, today)}</span>`,
    ].filter(Boolean).join('');
    return `<li class="row ${c.active ? '' : 'is-done'}">
      <button class="check ${c.active ? '' : 'on'}" data-action="${c.active ? 'complete-chore' : 'undo-chore'}" data-id="${c.id}" aria-label="${c.active ? 'Marquer comme fait' : 'Annuler'}">${icon('check')}</button>
      <button class="row-main" data-action="edit-chore" data-id="${c.id}">
        <div class="row-title">${esc(c.title)}</div><div class="row-meta">${meta}</div>
      </button>
    </li>`;
  }

  function openChoreForm(c) {
    const isNew = !c;
    c = c || { title: '', notes: '', room: '', member_id: state.chores.filter !== 'all' ? Number(state.chores.filter) : null,
      freq_unit: 'week', freq_interval: 1, due_date: state.chores.today };
    openSheet({
      title: isNew ? 'Nouvelle tâche' : 'Modifier la tâche',
      body: `<form id="sheet-form" autocomplete="off">
        <label class="field"><span>Tâche</span><input name="title" required maxlength="200" value="${esc(c.title)}" placeholder="Passer l'aspirateur, sortir les poubelles…"></label>
        <div class="grid-2">
          <label class="field"><span>Pièce</span><input name="room" list="rooms" maxlength="60" value="${esc(c.room)}"><datalist id="rooms">${ROOMS.map((r) => `<option value="${r}">`).join('')}</datalist></label>
          <label class="field"><span>Qui</span><select name="member_id">${memberOptions(c.member_id)}</select></label>
        </div>
        <div class="field"><span>Répéter</span>
          <div class="grid-2">
            <select name="freq_unit" class="input"><option value="none">Ne pas répéter</option>${Object.entries(UNITS).map(([k, v]) => `<option value="${k}" ${k === c.freq_unit ? 'selected' : ''}>Tous les N ${v[1]}</option>`).join('')}</select>
            <label class="grid-2" id="interval" style="align-items:center" ${c.freq_unit === 'none' ? 'hidden' : ''}><span class="muted small" style="text-align:right">N =</span><input type="number" class="input" name="freq_interval" min="1" max="365" value="${c.freq_interval}"></label>
          </div>
        </div>
        <label class="field"><span id="due-label">${c.freq_unit === 'none' ? 'À faire pour le (facultatif)' : 'Prochaine fois'}</span><input type="date" name="due_date" value="${c.due_date || ''}"></label>
        <label class="field"><span>Notes</span><textarea name="notes" maxlength="1000">${esc(c.notes)}</textarea></label>
        <div class="form-actions">
          ${isNew ? '' : `<button type="button" class="btn danger" data-del>${icon('trash', 'sm')} Supprimer</button>`}
          <button class="btn primary">${isNew ? 'Ajouter' : 'Enregistrer'}</button>
        </div>
      </form>`,
      onMount(form) {
        const f = form.elements;
        f.freq_unit.addEventListener('change', () => {
          const none = f.freq_unit.value === 'none';
          $('#interval', form).hidden = none;
          $('#due-label', form).textContent = none ? 'À faire pour le (facultatif)' : 'Prochaine fois';
        });
        $('[data-del]', form)?.addEventListener('click', async () => {
          if (!confirm(`Supprimer la tâche « ${c.title} » ?`)) return;
          await mutate(() => api('DELETE', `/api/chores/${c.id}`));
          closeSheet();
          toast('Tâche supprimée');
        });
      },
      async onSubmit(form) {
        const f = form.elements;
        const body = {
          title: f.title.value, room: f.room.value, notes: f.notes.value,
          member_id: f.member_id.value ? Number(f.member_id.value) : null,
          freq_unit: f.freq_unit.value, freq_interval: Number(f.freq_interval.value) || 1,
          due_date: f.due_date.value || null,
        };
        await mutate(() => (isNew ? api('POST', '/api/chores', body) : api('PUT', `/api/chores/${c.id}`, body)));
        toast(isNew ? 'Tâche ajoutée' : 'Tâche enregistrée');
      },
    });
  }

  // ====================================================================
  // Courses
  // ====================================================================
  const currentList = () => state.shop.lists.find((l) => l.id === state.shop.current);
  // Liste vide marquée « en cours de chargement » (évite d'afficher « La liste est vide » à tort).
  const loadingItems = () => Object.assign([], { loading: true });

  function renderShopping() {
    const { lists, items, categories, frequent, draft } = state.shop;
    const list = currentList();
    const todo = items.filter((i) => !i.checked);
    const done = items.filter((i) => i.checked);
    const onList = new Set(todo.map((i) => i.name.toLowerCase()));

    const chips = lists.map((l) => `<button class="chip ${l.id === state.shop.current ? 'active' : ''}" data-action="pick-list" data-id="${l.id}">${esc(l.icon)} ${esc(l.name)} <span class="n">${l.remaining}</span></button>`).join('');

    const groups = categories.map((cat) => {
      const its = todo.filter((i) => i.category === cat);
      return its.length ? `<h3 class="section-title">${esc(cat)} <span class="count">${its.length}</span></h3><ul class="list card">${its.map(itemRow).join('')}</ul>` : '';
    }).join('');

    const quick = frequent.filter((f) => !onList.has(f.name.toLowerCase())).slice(0, 16);

    return `<div class="toolbar">
        <div class="chips">${chips}<button class="chip add" data-action="new-list">${icon('plus', 'sm')} Liste</button></div>
        ${list ? `<button class="icon-btn" data-action="edit-list" aria-label="Gérer la liste">${icon('more')}</button>` : ''}
      </div>
      <div class="shop-layout">
        <div>
          <form id="quick-add" class="card add-bar" data-form="quick-add" autocomplete="off">
            <input class="input" name="name" placeholder="Ajouter à « ${esc(list?.name || '')} »…" value="${esc(draft)}" maxlength="100" enterkeyhint="done" autocapitalize="sentences">
            <input class="input" name="quantity" placeholder="Qté" maxlength="40" value="${esc(state.shop.draftQty)}">
            <button class="btn primary" aria-label="Ajouter">${icon('plus', 'sm')}</button>
            <div class="suggest" id="suggest" hidden></div>
          </form>
          ${items.loading ? '<div class="empty">Chargement…</div>' : todo.length ? groups : `<div class="empty"><span class="big">🧺</span>La liste est vide.</div>`}
          ${done.length ? `<h3 class="section-title">Dans le panier <span class="count">${done.length}</span>
              <button class="btn ghost small" style="margin-left:auto;min-height:32px;text-transform:none;letter-spacing:0" data-action="clear-checked">${icon('trash', 'sm')} Retirer</button></h3>
            <ul class="list card">${done.map(itemRow).join('')}</ul>` : ''}
        </div>
        <aside>
          ${quick.length ? `<div class="card card-pad"><h3 style="font-size:1rem;margin-bottom:10px">⭐ Souvent achetés</h3>
            <div class="opt-group">${quick.map((f) => `<button class="chip" data-action="quick-item" data-name="${esc(f.name)}">${icon('plus', 'sm')} ${esc(f.name)}</button>`).join('')}</div></div>` : ''}
        </aside>
      </div>`;
  }

  function itemRow(i) {
    const meta = [i.note && esc(i.note), i.added_by && !i.checked && `<span class="small">ajouté par ${esc(i.added_by)}</span>`].filter(Boolean).join(' · ');
    return `<li class="row ${i.checked ? 'is-done' : ''}">
      <button class="check ${i.checked ? 'on' : ''}" data-action="toggle-item" data-id="${i.id}" aria-label="${i.checked ? 'Remettre dans la liste' : 'Acheté'}">${icon('check')}</button>
      <button class="row-main" data-action="edit-item" data-id="${i.id}">
        <div class="row-title">${esc(i.name)} ${i.quantity ? `<span class="item-qty">· ${esc(i.quantity)}</span>` : ''}</div>
        ${meta ? `<div class="row-meta">${meta}</div>` : ''}
      </button>
      <button class="icon-btn" data-action="delete-item" data-id="${i.id}" aria-label="Retirer">${icon('close', 'sm')}</button>
    </li>`;
  }

  async function addItem(body) {
    const created = await mutate(() => api('POST', '/api/items', body));
    return created;
  }

  function openItemForm(item) {
    const isNew = !item;
    item = item || { name: '', quantity: '', category: '', note: '', list_id: state.shop.current };
    const listsField = isNew
      ? `<div class="field"><span>Ajouter sur</span><div class="opt-group">${state.shop.lists.map((l) => `<label class="opt"><input type="checkbox" name="list_ids" value="${l.id}" ${l.id === item.list_id ? 'checked' : ''}><span>${esc(l.icon)} ${esc(l.name)}</span></label>`).join('')}</div></div>`
      : `<label class="field"><span>Liste</span><select name="list_id">${state.shop.lists.map((l) => `<option value="${l.id}" ${l.id === item.list_id ? 'selected' : ''}>${esc(l.icon)} ${esc(l.name)}</option>`).join('')}</select></label>`;
    openSheet({
      title: isNew ? 'Nouvel article' : "Modifier l'article",
      body: `<form id="sheet-form" autocomplete="off">
        <div class="grid-2" style="grid-template-columns:2fr 1fr">
          <label class="field"><span>Article</span><input name="name" required maxlength="100" value="${esc(item.name)}"></label>
          <label class="field"><span>Quantité</span><input name="quantity" maxlength="40" value="${esc(item.quantity)}" placeholder="2, 1 kg…"></label>
        </div>
        <label class="field"><span>Rayon</span><select name="category"><option value="">Automatique</option>${state.shop.categories.map((c) => `<option ${c === item.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
        <label class="field"><span>Note</span><input name="note" maxlength="200" value="${esc(item.note)}" placeholder="Marque, taille…"></label>
        ${listsField}
        <div class="form-actions">
          ${isNew ? '' : `<button type="button" class="btn danger" data-del>${icon('trash', 'sm')} Retirer</button>`}
          <button class="btn primary">${isNew ? 'Ajouter' : 'Enregistrer'}</button>
        </div>
      </form>`,
      onMount(form) {
        $('[data-del]', form)?.addEventListener('click', async () => {
          closeSheet();
          await deleteItem(item);
        });
      },
      async onSubmit(form) {
        const f = form.elements;
        const body = { name: f.name.value, quantity: f.quantity.value, category: f.category.value, note: f.note.value };
        if (isNew) {
          body.list_ids = $$('[name=list_ids]:checked', form).map((i) => Number(i.value));
          if (!body.list_ids.length) throw new Error('Choisissez au moins une liste.');
          await addItem(body);
          toast(body.list_ids.length > 1 ? `Ajouté sur ${body.list_ids.length} listes` : 'Article ajouté');
        } else {
          body.list_id = Number(f.list_id.value);
          await mutate(() => api('PUT', `/api/items/${item.id}`, body));
        }
      },
    });
  }

  async function deleteItem(item) {
    await mutate(() => api('DELETE', `/api/items/${item.id}`));
    toast(`« ${item.name} » retiré`, {
      undo: () => addItem({ name: item.name, quantity: item.quantity, category: item.category, note: item.note, list_id: item.list_id }),
    });
  }

  function openListForm(list) {
    const isNew = !list;
    list = list || { name: '', icon: LIST_ICONS[0] };
    openSheet({
      title: isNew ? 'Nouvelle liste' : 'Gérer la liste',
      body: `<form id="sheet-form" autocomplete="off">
        <label class="field"><span>Nom</span><input name="name" required maxlength="60" value="${esc(list.name)}" placeholder="Marché, pharmacie, bricolage…"></label>
        <div class="field"><span>Icône</span><div class="opt-group">${LIST_ICONS.map((i) => `<label class="opt"><input type="radio" name="icon" value="${i}" ${i === list.icon ? 'checked' : ''}><span style="font-size:1.3rem">${i}</span></label>`).join('')}</div></div>
        <div class="form-actions">
          ${isNew ? '' : `<button type="button" class="btn danger" data-del>${icon('trash', 'sm')} Supprimer la liste</button>`}
          <button class="btn primary">${isNew ? 'Créer' : 'Enregistrer'}</button>
        </div>
      </form>`,
      onMount(form) {
        $('[data-del]', form)?.addEventListener('click', async () => {
          if (!confirm(`Supprimer la liste « ${list.name} » et ses ${list.total} article(s) ?`)) return;
          await mutate(() => api('DELETE', `/api/lists/${list.id}`));
          closeSheet();
          toast('Liste supprimée');
        });
      },
      async onSubmit(form) {
        const body = { name: form.elements.name.value, icon: form.elements.icon.value };
        const saved = await mutate(() => (isNew ? api('POST', '/api/lists', body) : api('PUT', `/api/lists/${list.id}`, body)));
        if (isNew) { state.shop.current = saved.id; store.set('list', saved.id); await sync(true); }
      },
    });
  }

  // Suggestions pendant la saisie
  let suggestTimer;
  function onQuickInput(input) {
    state.shop.draft = input.value;
    clearTimeout(suggestTimer);
    const box = $('#suggest');
    const q = input.value.trim();
    if (q.length < 2) { box.hidden = true; return; }
    suggestTimer = setTimeout(async () => {
      try {
        const items = await api('GET', `/api/catalog?q=${encodeURIComponent(q)}&limit=6`);
        const matches = items.filter((i) => i.name.toLowerCase() !== q.toLowerCase());
        box.innerHTML = matches.map((i) => `<button type="button" data-action="pick-suggestion" data-name="${esc(i.name)}"><span>${esc(i.name)}</span><span class="muted small">${esc(i.category)}</span></button>`).join('');
        box.hidden = !matches.length || input.value.trim() !== q;
      } catch { box.hidden = true; }
    }, 150);
  }

  async function quickAdd(form) {
    const name = form.elements.name.value.trim();
    if (!name) return;
    const quantity = form.elements.quantity.value.trim();
    state.shop.draft = '';
    state.shop.draftQty = '';
    form.elements.name.value = '';
    form.elements.quantity.value = '';
    $('#suggest').hidden = true;
    try {
      await addItem({ name, quantity, list_id: state.shop.current });
    } catch {
      state.shop.draft = name;
      render();
    }
  }

  // ====================================================================
  // Historique
  // ====================================================================
  function renderHistory() {
    const { items, module, more } = state.hist;
    let lastDay = '';
    const rows = items.map((h) => {
      const day = h.ts.slice(0, 10);
      let head = '';
      if (day !== lastDay) {
        if (lastDay) head += '</ul>';
        head += `<h3 class="section-title">${fmtDayLong.format(parseYmd(day))}</h3><ul class="list card">`;
        lastDay = day;
      }
      const [emoji] = MODULES[h.module] || ['•'];
      return `${head}<li class="hist-row"><span class="hist-ico">${emoji}</span>
        <div style="min-width:0"><div style="font-weight:550;overflow-wrap:anywhere">${esc(h.summary)}</div>
        <div class="muted small">${h.actor ? `par ${esc(h.actor)}` : 'appareil non identifié'}</div></div>
        <span class="hist-time">${h.ts.slice(11, 16)}</span></li>`;
    }).join('') + (items.length ? '</ul>' : '');

    return `<div class="chips">
        <button class="chip ${module === '' ? 'active' : ''}" data-action="hist-filter" data-module="">Tout</button>
        ${Object.entries(MODULES).map(([k, [e, label]]) => `<button class="chip ${module === k ? 'active' : ''}" data-action="hist-filter" data-module="${k}">${e} ${label}</button>`).join('')}
      </div>
      ${items.length ? rows : '<div class="card empty"><span class="big">📜</span>Aucune activité pour le moment.</div>'}
      ${more ? '<div style="text-align:center;margin:16px"><button class="btn" data-action="hist-more">Afficher plus</button></div>' : ''}`;
  }

  // ====================================================================
  // Réglages
  // ====================================================================
  async function openSettings() {
    let settings = {};
    try { settings = await api('GET', '/api/settings'); } catch { /* affiché sans le lien iCal */ }
    const notif = 'Notification' in window ? Notification.permission : 'unsupported';
    const notifText = { granted: '✅ Activées sur cet appareil', denied: '⛔ Refusées — à réactiver dans les réglages du téléphone', default: 'Non activées', unsupported: "Non disponibles : sur iPhone, ajoutez d'abord l'app à l'écran d'accueil." }[notif];
    openSheet({
      title: 'Réglages',
      body: `<div>
        <section class="settings-block">
          <h3>${icon('user', 'sm')} Cet appareil est utilisé par</h3>
          <select class="input" style="width:100%" id="me-select"><option value="">— Appareil partagé —</option>
            ${state.members.map((m) => `<option ${m.name === state.me ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}</select>
          <p class="muted small">Ce nom apparaît dans l'historique et les tâches réalisées.</p>
        </section>
        <section class="settings-block">
          <h3>👨‍👩‍👧 Membres du foyer</h3>
          <div id="members">${state.members.map((m) => `<div class="member-row"><i class="dot" style="background:${m.color};width:14px;height:14px"></i><span class="name">${esc(m.name)}</span>
            <button class="icon-btn" data-action="delete-member" data-id="${m.id}" aria-label="Retirer ${esc(m.name)}">${icon('trash', 'sm')}</button></div>`).join('') || '<p class="muted small">Aucun membre.</p>'}</div>
          <form id="member-form" style="display:flex;gap:8px;margin-top:8px" autocomplete="off">
            <input class="input" name="name" placeholder="Prénom" required maxlength="60" style="flex:1;min-width:0">
            <select class="input" name="color" style="width:64px;padding:0 6px">${COLORS.map((c, i) => `<option value="${c}" style="background:${c}" ${i === state.members.length % COLORS.length ? 'selected' : ''}>●</option>`).join('')}</select>
            <button class="btn primary">${icon('plus', 'sm')}</button>
          </form>
        </section>
        <section class="settings-block">
          <h3>${icon('bell', 'sm')} Alertes</h3>
          <p class="small">Notifications : ${notifText}</p>
          ${notif === 'default' ? '<button class="btn" data-action="ask-notifications">Activer les notifications</button>' : ''}
          <p class="muted small">Les alertes s'affichent quand l'application est ouverte (idéal pour la tablette). Pour être prévenu même app fermée, abonnez votre iPhone au calendrier ci-dessous : les alertes deviennent natives.</p>
        </section>
        ${settings.ics_url ? `<section class="settings-block">
          <h3>📲 Abonnement calendrier (iPhone)</h3>
          <div class="code" id="ics-url">${esc(settings.ics_url)}</div>
          <div class="form-actions" style="margin-top:8px"><button class="btn" data-action="copy-ics">Copier le lien</button></div>
          <p class="muted small">Sur l'iPhone : Réglages › Apps › Calendrier › Comptes de calendrier › Ajouter un compte › Autre › <b>Ajouter un calendrier avec abonnement</b>, collez le lien, puis désactivez « Supprimer les alertes ». Gardez ce lien privé.</p>
        </section>` : ''}
        <section class="settings-block">
          <h3>📺 Tablette</h3>
          <label class="switch">Garder l'écran allumé <input type="checkbox" id="wake-toggle" ${store.get('wake', false) ? 'checked' : ''} ${'wakeLock' in navigator ? '' : 'disabled'}></label>
          <label class="switch">Revenir à l'accueil après 2 min d'inactivité <input type="checkbox" id="autohome-toggle" ${store.get('autoHome', false) ? 'checked' : ''}></label>
        </section>
        <section class="settings-block">
          ${state.authRequired ? `<button class="btn danger block" data-action="logout">Se déconnecter</button>` : ''}
          <p class="muted small" style="text-align:center">Home_Management ${esc(settings.version || '')} · fuseau ${esc(settings.timezone || '')}</p>
        </section>
      </div>`,
      onMount(root) {
        $('#me-select', root).addEventListener('change', (e) => {
          state.me = e.target.value;
          store.set('me', state.me);
          toast(state.me ? `Bonjour ${state.me} 👋` : 'Appareil partagé');
        });
        $('#member-form', root).addEventListener('submit', async (e) => {
          e.preventDefault();
          const f = e.target.elements;
          await mutate(() => api('POST', '/api/members', { name: f.name.value, color: f.color.value }));
          openSettings();
        });
        $('#wake-toggle', root).addEventListener('change', (e) => { store.set('wake', e.target.checked); applyWakeLock(); });
        $('#autohome-toggle', root).addEventListener('change', (e) => store.set('autoHome', e.target.checked));
      },
    });
  }

  // ====================================================================
  // Feuilles (modales) et toasts
  // ====================================================================
  let sheet = null;

  function openSheet({ title, body, onMount, onSubmit }) {
    sheet = { onSubmit };
    $('#sheet-title').textContent = title;
    const content = $('#sheet-body');
    content.innerHTML = body;
    $('#sheet-root').hidden = false;
    document.body.style.overflow = 'hidden';
    const form = $('#sheet-form', content);
    onMount?.(form || content);
    const first = $('input:not([type=checkbox]):not([type=radio]):not([type=date]):not([type=time])', content);
    if (first && !first.value && matchMedia('(pointer: fine)').matches) first.focus();
  }

  function closeSheet() {
    sheet = null;
    $('#sheet-root').hidden = true;
    $('#sheet-body').innerHTML = '';
    document.body.style.overflow = '';
  }

  async function submitSheet(form) {
    if (!sheet?.onSubmit) return;
    const button = $('button.primary', form);
    $('.form-error', form)?.remove();
    button.disabled = true;
    try {
      await sheet.onSubmit(form);
      closeSheet();
    } catch (err) {
      form.insertAdjacentHTML('afterbegin', `<div class="form-error">${esc(err.message)}</div>`);
      form.closest('.sheet-body').scrollTop = 0;
    } finally {
      button.disabled = false;
    }
  }

  function toast(message, { undo, error } = {}) {
    const el = document.createElement('div');
    el.className = 'toast' + (error ? ' error' : '');
    el.innerHTML = `<span>${esc(message)}</span>${undo ? '<button>Annuler</button>' : ''}`;
    $('#toast-root').append(el);
    $$('#toast-root .toast').slice(0, -2).forEach((old) => old.remove());
    const remove = () => el.remove();
    if (undo) $('button', el).addEventListener('click', () => { remove(); undo(); });
    setTimeout(remove, undo ? 6000 : 3000);
  }

  // ====================================================================
  // Actions (délégation des clics)
  // ====================================================================
  function go(view) {
    if (!VIEWS[view] || view === state.view) return;
    state.view = view;
    store.set('view', view);
    if (view === 'calendar') state.cal.events = [];
    if (view === 'shopping') state.shop.items = loadingItems();
    window.scrollTo(0, 0);
    render();
    sync(true);
  }

  const actions = {
    go: (el) => go(el.dataset.view),
    'open-settings': () => openSettings(),
    'close-sheet': () => closeSheet(),
    fab: () => ({ calendar: () => openEventForm(), chores: () => openChoreForm(), shopping: () => openItemForm() })[state.view]?.(),

    // Calendrier
    'pick-day': (el) => {
      const day = el.dataset.date;
      if (state.cal.selected === day && window.innerWidth >= 700) return openEventForm();
      state.cal.selected = day;
      const d = parseYmd(day);
      if (d.getMonth() !== state.cal.month.getMonth()) { state.cal.month = firstOfMonth(d); sync(true); }
      render();
    },
    'cal-nav': (el) => {
      const m = state.cal.month;
      state.cal.month = new Date(m.getFullYear(), m.getMonth() + Number(el.dataset.delta), 1);
      const today = new Date();
      state.cal.selected = state.cal.month.getMonth() === today.getMonth() && state.cal.month.getFullYear() === today.getFullYear() ? ymd(today) : ymd(state.cal.month);
      render();
      sync(true);
    },
    'cal-today': () => { state.cal.month = firstOfMonth(new Date()); state.cal.selected = ymd(new Date()); render(); sync(true); },
    'new-event': () => openEventForm(),
    'edit-event': async (el) => {
      try { openEventForm(await api('GET', `/api/events/${el.dataset.id}`), el.dataset.occ); } catch (err) { toast(err.message, { error: true }); }
    },

    // Tâches
    'new-chore': () => openChoreForm(),
    'edit-chore': (el) => openChoreForm(state.chores.list.find((c) => c.id === Number(el.dataset.id))),
    'complete-chore': async (el) => {
      const chore = state.chores.list.find((c) => c.id === Number(el.dataset.id));
      el.classList.add('on');
      try {
        await mutate(() => api('POST', `/api/chores/${chore.id}/complete`));
      } catch {
        el.classList.remove('on');
        return;
      }
      toast(`« ${chore.title} » fait ✓`, { undo: () => mutate(() => api('POST', `/api/chores/${chore.id}/undo`)) });
    },
    'undo-chore': (el) => mutate(() => api('POST', `/api/chores/${el.dataset.id}/undo`)),
    'chore-filter': (el) => { state.chores.filter = el.dataset.id; store.set('choresFilter', el.dataset.id); render(); },
    'chore-show-done': (el) => { state.chores.showDone = el.checked; sync(true); },

    // Courses
    'pick-list': (el) => { state.shop.current = Number(el.dataset.id); store.set('list', state.shop.current); state.shop.items = loadingItems(); render(); sync(true); },
    'new-list': () => openListForm(),
    'open-list': (el) => { state.shop.current = Number(el.dataset.id); store.set('list', state.shop.current); go('shopping'); },
    'home-new-event': () => { state.cal.selected = ymd(new Date()); openEventForm(); },
    'edit-list': () => openListForm(currentList()),
    'toggle-item': (el) => {
      const id = Number(el.dataset.id);
      const item = [state.shop.items, ...Object.values(state.home.items)].flat().find((i) => i.id === id);
      item.checked = item.checked ? 0 : 1;
      render();
      mutate(() => api('PUT', `/api/items/${item.id}`, { checked: !!item.checked }));
    },
    'edit-item': (el) => openItemForm(state.shop.items.find((i) => i.id === Number(el.dataset.id))),
    'delete-item': (el) => deleteItem(state.shop.items.find((i) => i.id === Number(el.dataset.id))),
    'clear-checked': () => mutate(() => api('POST', `/api/lists/${state.shop.current}/clear-checked`)).then((r) => toast(`${r.removed} article(s) retiré(s)`)),
    'quick-item': (el) => addItem({ name: el.dataset.name, list_id: state.shop.current }),
    'pick-suggestion': (el) => {
      const input = $('#quick-add input[name=name]');
      input.value = el.dataset.name;
      $('#suggest').hidden = true;
      $('#quick-add input[name=quantity]').focus();
    },

    // Historique
    'hist-filter': (el) => { state.hist.module = el.dataset.module; loadHistory().then(render); },
    'hist-more': () => loadHistory(true).then(render),

    // Réglages
    'delete-member': async (el) => {
      const m = memberById(Number(el.dataset.id));
      if (!confirm(`Retirer ${m.name} du foyer ? Ses tâches et événements seront conservés sans attribution.`)) return;
      await mutate(() => api('DELETE', `/api/members/${m.id}`));
      openSettings();
    },
    'ask-notifications': async () => { await Notification.requestPermission(); openSettings(); },
    'copy-ics': async () => {
      try { await navigator.clipboard.writeText($('#ics-url').textContent); toast('Lien copié'); } catch { toast('Sélectionnez le lien pour le copier'); }
    },
    logout: async () => { await api('POST', '/api/logout'); location.reload(); },
  };

  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    const fn = actions[el.dataset.action];
    if (!fn) return;
    if (el.tagName !== 'INPUT') e.preventDefault();
    unlockAudio();
    fn(el, e);
  });

  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (form.id === 'sheet-form') { e.preventDefault(); submitSheet(form); }
    if (form.dataset.form === 'quick-add') { e.preventDefault(); quickAdd(form); }
  });

  document.addEventListener('input', (e) => {
    if (e.target.matches('#quick-add input[name=name]')) onQuickInput(e.target);
    if (e.target.matches('#quick-add input[name=quantity]')) state.shop.draftQty = e.target.value;
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!$('#alert-root').hidden) dismissAlert();
      else if (sheet || !$('#sheet-root').hidden) closeSheet();
    }
  });

  // ====================================================================
  // Alertes des événements
  // ====================================================================
  const alertQueue = [];
  let audioCtx = null;

  function unlockAudio() {
    if (audioCtx) return;
    try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch { /* pas de son */ }
  }

  function chime() {
    if (!audioCtx) return;
    audioCtx.resume?.();
    [0, 0.25, 0.5].forEach((t, i) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.frequency.value = [880, 1175, 1568][i];
      gain.gain.setValueAtTime(0.0001, audioCtx.currentTime + t);
      gain.gain.exponentialRampToValueAtTime(0.25, audioCtx.currentTime + t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + t + 0.6);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(audioCtx.currentTime + t);
      osc.stop(audioCtx.currentTime + t + 0.65);
    });
  }

  async function checkAlerts() {
    const nowTs = Date.now();
    const today = new Date();
    let events;
    try { events = await api('GET', `/api/events?start=${ymd(addDays(today, -1))}&end=${ymd(addDays(today, 8))}`); } catch { return; }
    const fired = store.get('fired', {});
    for (const ev of events) {
      for (const minutes of ev.alerts) {
        const at = parseDT(ev.occurrence_start).getTime() - minutes * 60000;
        const key = `${ev.id}|${ev.occurrence_start}|${minutes}`;
        if (at <= nowTs && nowTs - at < 6 * 3600e3 && !fired[key]) {
          fired[key] = nowTs;
          alertQueue.push(ev);
          notify(ev, key);
        }
      }
    }
    for (const [k, ts] of Object.entries(fired)) if (nowTs - ts > 10 * 864e5) delete fired[k];
    store.set('fired', fired);
    if (alertQueue.length && $('#alert-root').hidden) showNextAlert();
  }

  function alertText(ev) {
    const start = parseDT(ev.occurrence_start);
    const mins = Math.round((start - Date.now()) / 60000);
    const when = ev.all_day ? (ymd(start) === ymd(new Date()) ? "Aujourd'hui" : fmtDayLong.format(start))
      : mins > 0 ? (mins < 60 ? `Dans ${mins} min` : mins < 1440 ? `Dans ${Math.round(mins / 60)} h` : fmtDayLong.format(start)) + ` · ${hm(start)}`
        : mins > -5 ? `Maintenant · ${hm(start)}` : `A commencé à ${hm(start)}`;
    return when + (ev.location ? ` · ${ev.location}` : '');
  }

  async function notify(ev, tag) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    try {
      const reg = await navigator.serviceWorker?.ready;
      const options = { body: alertText(ev), tag, icon: '/static/icons/icon-192.png', badge: '/static/icons/icon-192.png' };
      if (reg?.showNotification) await reg.showNotification(ev.title, options); else new Notification(ev.title, options);
    } catch { /* notification impossible : l'alerte à l'écran suffit */ }
  }

  function showNextAlert() {
    const ev = alertQueue.shift();
    if (!ev) return;
    const root = $('#alert-root');
    root.innerHTML = `<div class="alert-card" role="alertdialog" aria-labelledby="alert-title" style="border-top:6px solid ${ev.color}">
      <div class="bell">${icon('bell')}</div>
      <h2 id="alert-title">${esc(ev.title)}</h2>
      <p>${esc(alertText(ev))}</p>
      <button class="btn primary block" id="alert-ok">OK</button>
    </div>`;
    root.hidden = false;
    $('#alert-ok').addEventListener('click', dismissAlert);
    chime();
  }

  function dismissAlert() {
    $('#alert-root').hidden = true;
    showNextAlert();
  }

  let askedNotif = false;
  function maybeAskNotifications() {
    if (askedNotif || !('Notification' in window) || Notification.permission !== 'default') return;
    askedNotif = true;
    toast('Activer les notifications sur cet appareil ?', { undo: () => Notification.requestPermission() });
    const last = $('#toast-root .toast:last-child button');
    if (last) last.textContent = 'Activer';
  }

  // ====================================================================
  // Écran allumé (tablette), horloge, connexion
  // ====================================================================
  let wakeLock = null;
  async function applyWakeLock() {
    if (!('wakeLock' in navigator)) return;
    if (store.get('wake', false) && document.visibilityState === 'visible') {
      try { wakeLock = await navigator.wakeLock.request('screen'); } catch { /* refusé (batterie faible…) */ }
    } else if (wakeLock) {
      wakeLock.release();
      wakeLock = null;
    }
  }

  let lastMinute = '';
  let lastDay = ymd(new Date());
  let lastActivity = Date.now();
  ['pointerdown', 'keydown'].forEach((type) => document.addEventListener(type, () => { lastActivity = Date.now(); }, { passive: true }));

  function tickClock() {
    const n = new Date();
    const minute = hm(n);
    if (minute === lastMinute) return;
    lastMinute = minute;
    $('#clock').textContent = capitalize(fmtClock.format(n));
    if (ymd(n) !== lastDay) {
      // Minuit : nouvelle journée, on recharge tout (tâches du jour, agenda…).
      lastDay = ymd(n);
      state.cal.selected = lastDay;
      state.cal.month = firstOfMonth(n);
      sync(true);
    }
    const typing = $('#quick-add')?.contains(document.activeElement);
    if (state.view === 'home' && !typing) render(); // libellés « dans 5 min », alertes passées…
    // Tablette : retour automatique à l'accueil après 2 min sans interaction.
    if (store.get('autoHome', false) && state.view !== 'home' && !sheet && $('#alert-root').hidden && Date.now() - lastActivity > 120000) go('home');
  }

  function showLogin() {
    if ($('#login')) return;
    const el = document.createElement('div');
    el.id = 'login';
    el.className = 'login';
    el.style.cssText = 'position:fixed;inset:0;z-index:100;background:var(--bg)';
    el.innerHTML = `<div class="card">
      <img src="/static/icons/icon.svg" alt="">
      <h1>Maison</h1>
      <p class="muted">Calendrier, tâches et courses du foyer</p>
      <form autocomplete="on">
        <label class="field"><span>Mot de passe du foyer</span><input type="password" name="password" autocomplete="current-password" required></label>
        <button class="btn primary block">Entrer</button>
      </form>
    </div>`;
    document.body.append(el);
    const form = $('form', el);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      $('.form-error', form)?.remove();
      try {
        await api('POST', '/api/login', { password: form.elements.password.value });
        el.remove();
        start();
      } catch (err) {
        form.insertAdjacentHTML('afterbegin', `<div class="form-error">${esc(err.message)}</div>`);
      }
    });
    $('input', form).focus();
  }

  // ====================================================================
  // Démarrage
  // ====================================================================
  let started = false;
  async function start() {
    render();
    await sync(true);
    if (started) return;
    started = true;
    checkAlerts();
    setInterval(() => document.visibilityState === 'visible' && sync(), 15000);
    setInterval(checkAlerts, 30000);
    setInterval(tickClock, 1000);
  }

  async function boot() {
    tickClock();
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      tickClock();
      sync();
      checkAlerts();
      applyWakeLock();
    });
    applyWakeLock();
    try {
      const status = await api('GET', '/api/status');
      state.authRequired = status.auth_required;
      if (!status.authenticated) return showLogin();
    } catch (err) {
      $('#main').innerHTML = `<div class="card empty"><span class="big">📡</span>${esc(err.message)}<br><button class="btn" style="margin-top:12px" onclick="location.reload()">Réessayer</button></div>`;
      return;
    }
    start();
  }

  boot();
})();
