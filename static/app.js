(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = { connected: false, writesEnabled: false, customer: null, detailCustomer: null, customerWriteBusy: false, customerUpdateBusy: false, customerSearch: { query: '', page: 1, hasMore: false }, products: [], cart: [], draftId: null, validCount: 0 };
  const statusLabels = { pending: 'در انتظار پرداخت', 'on-hold': 'در انتظار بررسی', processing: 'در حال انجام', completed: 'تکمیل‌شده' };
  const errorLabels = { connection: 'اتصال', unauthorized: 'احراز هویت', forbidden: 'مجوز', api: 'API', validation: 'اعتبارسنجی' };
  let pendingOrder = null;
  let orderBusy = false;
  let importBusy = false;
  let productSearchVersion = 0;
  let previewInvalidRows = [];
  let customerSearchVersion = 0;
  let customerSearchController = null;
  let manualStage = 'customer';
  let mappingInfo = null;
  let lastPreview = null;
  let previewBusy = false;
  try { pendingOrder = JSON.parse(sessionStorage.getItem('afrand.pendingOrder') || 'null'); } catch { /* no stored request */ }

  const busyControls = new Map();
  let activeRequests = 0;
  function requestProgress(url, method) {
    const selector = url.startsWith('/api/status') ? '#refresh-connection'
      : url.startsWith('/api/products') ? '#product-search'
      : url.startsWith('/api/import/preview') ? '#import-form button[type=submit], #mapping-form button[type=submit]'
      : url.startsWith('/api/import/commit') ? '#commit-import'
      : url.startsWith('/api/orders') ? '#submit-order, #retry-order'
      : method === 'PUT' ? '#update-customer-button'
      : method === 'POST' ? '#save-customer-button'
      : /^\/api\/customers\//.test(url) ? '#customer-orders'
      : '#customer-search-form button, #load-more-customers';
    const controls = $$(selector);
    controls.forEach(control => {
      busyControls.set(control, (busyControls.get(control) || 0) + 1);
      control.classList.add('is-loading'); control.setAttribute('aria-busy', 'true');
    });
    activeRequests += 1; $('#request-progress').hidden = false;
    return () => {
      controls.forEach(control => {
        const remaining = busyControls.get(control) - 1;
        if (remaining) busyControls.set(control, remaining);
        else { busyControls.delete(control); control.classList.remove('is-loading'); control.removeAttribute('aria-busy'); }
      });
      activeRequests -= 1; $('#request-progress').hidden = activeRequests === 0;
    };
  }
  function confirmAction(title, description, action) {
    const dialog = $('#confirm-dialog');
    if (dialog.open) return Promise.resolve(false);
    $('#confirm-title').textContent = title;
    $('#confirm-description').textContent = description;
    $('#confirm-accept').textContent = action;
    dialog.returnValue = '';
    return new Promise(resolve => {
      dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
      dialog.showModal();
    });
  }

  async function api(url, options = {}) {
    const controller = new AbortController();
    const externalSignal = options.signal;
    const relayAbort = () => controller.abort();
    externalSignal?.addEventListener('abort', relayAbort, { once: true });
    if (externalSignal?.aborted) controller.abort();
    let timedOut = false;
    const isRead = !options.method || options.method === 'GET';
    const timer = isRead ? setTimeout(() => { timedOut = true; controller.abort(); }, 12000) : null;
    let response;
    let data;
    const finishProgress = requestProgress(url, options.method || 'GET');
    try {
      response = await fetch(url, { ...options, signal: controller.signal });
      data = await response.json();
    } catch (cause) {
      if (externalSignal?.aborted && !timedOut) throw cause;
      const error = new Error(timedOut ? 'ووکامرس در مهلت بررسی پاسخ نداد. اتصال سایت را بررسی کن و دوباره تلاش کن.' : 'ارتباط با سرور یا دریافت پاسخ کامل نشد.');
      error.category = 'connection'; throw error;
    } finally {
      finishProgress();
      if (timer) clearTimeout(timer);
      externalSignal?.removeEventListener('abort', relayAbort);
    }
    if (!response.ok) {
      if (response.status === 401 && data.category === 'authentication') window.location.assign('/login');
      const error = new Error(data.error || `درخواست ناموفق بود (${response.status})`);
      error.category = data.category || 'api';
      error.status = response.status;
      error.state = data.state;
      throw error;
    }
    return data;
  }
  function errorText(error) {
    const label = errorLabels[error.category];
    return label ? `${label}: ${error.message}` : error.message;
  }
  function message(text, kind = 'info') {
    const box = $('#global-message');
    box.textContent = text;
    box.className = `message ${kind}`;
    box.hidden = false;
  }
  function hideMessage() { $('#global-message').hidden = true; }
  function escapeText(value) { return String(value ?? ''); }
  function updateConnection(result = {}) {
    const badge = $('#connection-state');
    if (state.connected && state.writesEnabled) {
      badge.textContent = 'ووکامرس متصل · ثبت فعال'; badge.className = 'connection ok';
      $('#setup-notice').hidden = true;
    } else if (state.connected) {
      badge.textContent = 'ووکامرس متصل · فقط خواندنی'; badge.className = 'connection';
      $('#setup-notice').textContent = 'اتصال برقرار است؛ ثبت خرید فعلاً غیرفعال است. برای فعال‌سازی با مسئول پنل هماهنگ کنید.';
      $('#setup-notice').hidden = false;
    } else if (result.credentials_present) {
      badge.textContent = 'پیکربندی هست · اتصال API ناموفق'; badge.className = 'connection bad';
      const label = errorLabels[result.api_error_category];
      $('#setup-notice').textContent = `${label ? `${label}: ` : ''}${result.api_error || 'ارتباط REST API ووکامرس تأیید نشد؛ کلید، مجوزها و دسترسی شبکه را بررسی کن.'}`;
      $('#setup-notice').hidden = false;
    } else {
      badge.textContent = 'اتصال تنظیم نشده'; badge.className = 'connection bad';
      $('#setup-notice').textContent = 'اتصال فروشگاه هنوز تنظیم نشده است. با مسئول پنل هماهنگ کنید.';
      $('#setup-notice').hidden = false;
    }
    refreshSubmitState();
  }
  function connectionFailed(error) {
    if (['connection', 'unauthorized', 'forbidden'].includes(error.category)) {
      state.connected = false; state.writesEnabled = false;
      updateConnection({ credentials_present: true, api_error: error.message, api_error_category: error.category });
    }
  }
  async function loadStatus() {
    const refresh = $('#refresh-connection');
    if (refresh.disabled) return;
    refresh.disabled = true;
    state.connected = false; state.writesEnabled = false; refreshSubmitState();
    $('#connection-state').textContent = 'در حال بررسی اتصال…';
    try {
      const result = await api('/api/status');
      state.connected = result.connected;
      state.writesEnabled = result.writes_enabled;
      updateConnection(result);
    } catch (error) {
      state.connected = false; state.writesEnabled = false; refreshSubmitState();
      $('#connection-state').textContent = 'سرور در دسترس نیست';
      $('#connection-state').className = 'connection bad';
      $('#setup-notice').textContent = errorText(error);
      $('#setup-notice').hidden = false;
    }
    finally { refresh.disabled = false; }
  }
  $('#refresh-connection').addEventListener('click', loadStatus);
  function refreshSubmitState() {
    const manualReady = state.customer && state.cart.length && $('#order-status').value && state.writesEnabled;
    $('#submit-order').disabled = !manualReady || orderBusy || Boolean(pendingOrder);
    const importReady = state.draftId && state.validCount > 0 && $('#import-status').value && state.writesEnabled;
    $('#commit-import').disabled = !importReady || importBusy;
    $('#import-form').inert = importBusy;
    $('#import-status').disabled = importBusy;
    $('#pending-order').hidden = !pendingOrder;
    $('#retry-order').disabled = !state.writesEnabled || orderBusy;
    for (const id of ['customer-card', 'products-card', 'review-card']) $(`#${id}`).inert = Boolean(pendingOrder);
    $('#save-customer-button').disabled = !state.writesEnabled || state.customerWriteBusy;
    $('#update-customer-button').disabled = !state.writesEnabled || !state.detailCustomer || state.customerUpdateBusy;
    $('#review-purchase').disabled = !state.customer || !state.cart.length || Boolean(pendingOrder);
    $('#step-products').disabled = !state.customer || Boolean(pendingOrder);
    $('#step-review').disabled = !state.customer || !state.cart.length || Boolean(pendingOrder);
    $('#step-customer').disabled = Boolean(pendingOrder);
  }
  function showStage(stage) {
    if (pendingOrder || (stage !== 'customer' && !state.customer) || (stage === 'review' && !state.cart.length)) return;
    manualStage = stage;
    ['customer', 'products', 'review'].forEach(name => {
      $(`#${name}-card`).hidden = stage !== name;
      const step = $(`#step-${name}`);
      step.classList.toggle('current', stage === name);
      if (stage === name) step.setAttribute('aria-current', 'step'); else step.removeAttribute('aria-current');
    });
    refreshSubmitState();
  }
  $$('[data-stage]').forEach(button => button.addEventListener('click', () => showStage(button.dataset.stage)));
  $('#back-customer').addEventListener('click', () => showStage('customer'));
  $('#back-products').addEventListener('click', () => showStage('products'));
  $('#review-purchase').addEventListener('click', () => showStage('review'));
  $('#show-new-customer').addEventListener('click', () => {
    $('#new-customer-panel').hidden = false;
    $('#new-customer-form [name="first_name"]').focus();
  });
  function renderReceipt() {
    const box = $('#purchase-receipt'); box.replaceChildren();
    const customer = document.createElement('p'); customer.className = 'receipt-customer';
    customer.textContent = state.customer ? nameOf(state.customer) : 'با انتخاب مشتری، خرید را شروع کنید.'; box.append(customer);
    if (state.customer) {
      const phone = document.createElement('small'); phone.textContent = state.customer.phone || ''; box.append(phone);
      state.cart.forEach(item => {
        const line = document.createElement('div'); line.className = 'summary-line';
        const name = document.createElement('span'); name.textContent = item.name;
        const qty = document.createElement('b'); qty.textContent = `${item.quantity.toLocaleString('fa-IR')} عدد`; line.append(name, qty); box.append(line);
      });
      const total = document.createElement('div'); total.className = 'receipt-total';
      total.textContent = state.cart.length ? `${state.cart.reduce((sum, item) => sum + Number(item.price || 0) * item.quantity, 0).toLocaleString('fa-IR')} · واحد فروشگاه` : 'کالاها را در مرحله بعد انتخاب کنید.'; box.append(total);
      if (state.cart.length) { const note = document.createElement('small'); note.textContent = 'جمع قیمت فروشگاه، پیش از مالیات نهایی'; box.append(note); }
    }
  }
  function nameOf(person) { return [person.first_name, person.last_name].filter(Boolean).join(' ') || person.name || 'مشتری'; }
  function renderCustomerResults(customers, append = false) {
    const box = $('#customer-results');
    if (!append) box.replaceChildren();
    if (!customers.length) {
      if (append) return;
      box.textContent = 'مشتری‌ای با این جست‌وجو پیدا نشد. اگر مشتری جدید است، اطلاعاتش را وارد کن.';
      box.className = 'results empty-cart'; $('#new-customer-panel').hidden = false;
      $('#new-customer-form [name="phone"]').value = $('#customer-query').value.trim();
      return;
    }
    box.className = 'results'; $('#new-customer-panel').hidden = true;
    customers.forEach(customer => {
      const row = document.createElement('div'); row.className = 'result-option';
      const info = document.createElement('div');
      const name = document.createElement('b'); name.textContent = nameOf(customer);
      const detail = document.createElement('small'); detail.textContent = [customer.phone, customer.email].filter(Boolean).join(' · ');
      info.append(name, detail);
      const actions = document.createElement('div'); actions.className = 'result-actions';
      const button = document.createElement('button'); button.className = 'btn btn-secondary'; button.type = 'button'; button.textContent = 'انتخاب';
      button.addEventListener('click', () => selectCustomer(customer, false));
      const details = document.createElement('button'); details.className = 'link-button'; details.type = 'button'; details.textContent = 'جزئیات';
      details.addEventListener('click', () => loadCustomerDetails(customer.id));
      actions.append(button, details); row.append(info, actions); box.append(row);
    });
  }
  function selectCustomer(customer, isNew) {
    $('#order-result').hidden = true;
    state.customer = { ...customer, isNew };
    const selected = $('#selected-customer'); selected.replaceChildren(); selected.hidden = false;
    const info = document.createElement('div');
    const name = document.createElement('b'); name.textContent = nameOf(customer);
    const detail = document.createElement('small'); detail.textContent = `${isNew ? 'حساب جدید همراه سفارش ساخته می‌شود' : 'مشتری موجود در ووکامرس'} · ${customer.phone || ''}`;
    info.append(name, detail);
    const actions = document.createElement('div'); actions.className = 'selected-actions';
    const badge = document.createElement('span'); badge.className = 'status ok'; badge.textContent = 'انتخاب شد';
    const change = document.createElement('button'); change.className = 'link-button'; change.type = 'button'; change.textContent = 'تغییر';
    change.addEventListener('click', () => { state.customer = null; selected.hidden = true; showStage('customer'); refreshReview(); });
    actions.append(badge, change); selected.append(info, actions);
    $('#new-customer-panel').hidden = true; $('#customer-results').replaceChildren();
    $('#load-more-customers').hidden = true;
    $('#customer-details').hidden = true;
    refreshReview(); showStage('products'); loadProducts();
  }
  function renderCustomerOrders(orders) {
    const box = $('#customer-orders'); box.replaceChildren();
    if (!orders.length) { box.textContent = 'سفارشی برای این مشتری پیدا نشد.'; box.className = 'customer-orders empty-cart'; return; }
    box.className = 'customer-orders';
    orders.forEach(order => {
      const row = document.createElement('div'); row.className = 'customer-order';
      const heading = document.createElement('b'); heading.textContent = `سفارش ${order.number || order.id} · ${order.source === 'in_person' ? 'حضوری' : 'آنلاین / بدون برچسب حضوری'} · ${statusLabels[order.status] || order.status || 'نامشخص'}`;
      const summary = document.createElement('small'); summary.textContent = [order.date_created, order.total ? `${order.total} ${order.currency || ''}`.trim() : ''].filter(Boolean).join(' · ');
      const items = document.createElement('small');
      items.textContent = (order.line_items || []).map(item => `${item.name} × ${item.quantity}`).join('، ');
      row.append(heading, summary); if (items.textContent) row.append(items); box.append(row);
    });
  }
  async function loadCustomerDetails(customerId) {
    showStage('customer');
    const panel = $('#customer-details'); panel.hidden = false;
    state.detailCustomer = null; refreshSubmitState();
    $('#customer-detail-name').textContent = 'جزئیات مشتری';
    $('#customer-detail-contact').textContent = '';
    $('#customer-orders').textContent = 'در حال دریافت اطلاعات مشتری…';
    try {
      const [detail, orders] = await Promise.all([
        api(`/api/customers/${encodeURIComponent(customerId)}`),
        api(`/api/customers/${encodeURIComponent(customerId)}/orders`)
      ]);
      const customer = detail.customer; state.detailCustomer = customer;
      $('#customer-detail-name').textContent = nameOf(customer);
      $('#customer-detail-contact').textContent = [customer.phone, customer.email].filter(Boolean).join(' · ');
      const form = $('#customer-edit-form');
      for (const key of ['first_name', 'last_name', 'phone', 'email']) form.elements[key].value = customer[key] || '';
      renderCustomerOrders(orders.orders || []); refreshSubmitState();
      return true;
    } catch (error) {
      state.detailCustomer = null; refreshSubmitState();
      $('#customer-orders').textContent = ''; message(errorText(error), 'error');
      return false;
    }
  }
  $('#customer-search-form').addEventListener('submit', async event => {
    event.preventDefault(); hideMessage();
    customerSearchController?.abort();
    customerSearchController = new AbortController();
    const controller = customerSearchController;
    const version = ++customerSearchVersion;
    const query = $('#customer-query').value.trim();
    $('#customer-details').hidden = true; state.detailCustomer = null; refreshSubmitState();
    if (query.length < 2) return message('برای جست‌وجو حداقل دو نویسه وارد کن.', 'error');
    const box = $('#customer-results'); box.textContent = 'در حال جست‌وجوی مشتری…';
    $('#new-customer-panel').hidden = true;
    const button = event.currentTarget.querySelector('button'); button.textContent = 'در حال جست‌وجو…';
    event.currentTarget.setAttribute('aria-busy', 'true');
    $('#load-more-customers').hidden = true;
    state.customerSearch = { query, page: 1, hasMore: false };
    try {
      const result = await api(`/api/customers?q=${encodeURIComponent(query)}&page=1`, { signal: controller.signal });
      if (version !== customerSearchVersion) return;
      state.customerSearch = { query, page: result.page || 1, hasMore: Boolean(result.has_more) };
      renderCustomerResults(result.customers || []);
      $('#load-more-customers').hidden = !state.customerSearch.hasMore;
    } catch (error) {
      if (version !== customerSearchVersion || error.name === 'AbortError') return;
      box.textContent = 'جست‌وجو انجام نشد؛ پس از بررسی اتصال، دوباره جست‌وجو کن.';
      connectionFailed(error); message(errorText(error), 'error');
    } finally {
      if (version === customerSearchVersion) {
        button.textContent = 'جست‌وجو';
        $('#customer-search-form').setAttribute('aria-busy', 'false');
      }
    }
  });
  $('#load-more-customers').addEventListener('click', async () => {
    const search = state.customerSearch;
    if (!search.hasMore) return;
    const button = $('#load-more-customers'); button.disabled = true; button.textContent = 'در حال دریافت…';
    try {
      const page = search.page + 1;
      const result = await api(`/api/customers?q=${encodeURIComponent(search.query)}&page=${page}`);
      state.customerSearch = { query: search.query, page: result.page || page, hasMore: Boolean(result.has_more) };
      renderCustomerResults(result.customers || [], true);
      button.hidden = !state.customerSearch.hasMore;
    } catch (error) { message(errorText(error), 'error'); }
    finally { button.disabled = false; button.textContent = 'نمایش نتایج بیشتر'; }
  });
  $('#new-customer-form').addEventListener('submit', event => {
    event.preventDefault(); hideMessage();
    const form = new FormData(event.currentTarget);
    const customer = Object.fromEntries(form.entries());
    if (!customer.phone.trim() || !customer.first_name.trim() || !customer.email.trim()) return message('نام، موبایل و ایمیل برای مشتری جدید لازم است.', 'error');
    selectCustomer(customer, true);
  });
  $('#save-customer-button').addEventListener('click', async () => {
    if (!state.writesEnabled) return;
    const customer = Object.fromEntries(new FormData($('#new-customer-form')).entries());
    if (!customer.first_name.trim() || !customer.phone.trim() || !customer.email.trim()) return message('نام، موبایل و ایمیل مشتری لازم است.', 'error');
    if (!await confirmAction('ثبت مشتری جدید', `مشتری «${nameOf(customer)}» در فروشگاه ساخته می‌شود.`, 'ثبت مشتری')) return;
    const button = $('#save-customer-button'); state.customerWriteBusy = true; button.disabled = true; button.textContent = 'در حال ثبت…';
    try {
      const result = await api('/api/customers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...customer, confirm: true }) });
      $('#new-customer-form').reset(); $('#new-customer-panel').hidden = true;
      selectCustomer(result.customer, false);
      if (await loadCustomerDetails(result.customer.id)) message('مشتری در ووکامرس ساخته شد و برای سفارش انتخاب شد.', 'success');
    } catch (error) { message(errorText(error), 'error'); }
    finally { state.customerWriteBusy = false; button.textContent = 'ثبت جداگانهٔ مشتری'; refreshSubmitState(); }
  });
  $('#customer-edit-form').addEventListener('submit', async event => {
    event.preventDefault();
    const customer = Object.fromEntries(new FormData(event.currentTarget).entries());
    if (!state.detailCustomer || !state.writesEnabled) return;
    if (!await confirmAction('ذخیرهٔ اطلاعات مشتری', `تغییرات اطلاعات «${nameOf(state.detailCustomer)}» در فروشگاه ذخیره می‌شود.`, 'ذخیرهٔ تغییرات')) return;
    const button = $('#update-customer-button'); state.customerUpdateBusy = true; button.disabled = true; button.textContent = 'در حال ذخیره…';
    try {
      const result = await api(`/api/customers/${encodeURIComponent(state.detailCustomer.id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...customer, confirm: true }) });
      if (state.customer && String(state.customer.id) === String(result.customer.id)) selectCustomer(result.customer, false);
      if (await loadCustomerDetails(result.customer.id)) message('اطلاعات مشتری به‌روزرسانی شد.', 'success');
    } catch (error) { message(errorText(error), 'error'); }
    finally { state.customerUpdateBusy = false; button.textContent = 'ذخیرهٔ تغییرات مشتری'; refreshSubmitState(); }
  });
  async function loadProducts() {
    const version = ++productSearchVersion;
    const box = $('#product-results'); box.textContent = 'در حال دریافت محصولات…';
    try { const data = await api(`/api/products?search=${encodeURIComponent($('#product-query').value.trim())}`); if (version === productSearchVersion) renderProducts(data.products || []); }
    catch (error) { if (version === productSearchVersion) { box.textContent = ''; message(errorText(error), 'error'); } }
  }
  function renderProducts(products) {
    const box = $('#product-results'); box.replaceChildren();
    if (!products.length) { box.textContent = 'محصولی پیدا نشد. بررسی کن محصول در ووکامرس منتشر شده باشد.'; box.className = 'product-results empty-cart'; return; }
    box.className = 'product-results';
    products.forEach(product => {
      const row = document.createElement('div'); row.className = 'product-option';
      const info = document.createElement('div'); const name = document.createElement('b'); name.textContent = product.name;
      const sku = document.createElement('small'); sku.textContent = product.sku ? `کد کالا: ${product.sku}` : `شناسه: ${product.id}`; info.append(name, sku);
      const price = document.createElement('span'); price.className = 'price'; price.textContent = product.price ? `${product.price} · قیمت فروشگاه` : 'قیمت نامشخص';
      const add = document.createElement('button'); add.className = 'btn btn-secondary'; add.type = 'button'; add.textContent = 'افزودن';
      add.addEventListener('click', () => addProduct(product)); row.append(info, price, add); box.append(row);
    });
  }
  $('#product-search').addEventListener('click', loadProducts);
  $('#product-query').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); loadProducts(); } });
  function addProduct(product) {
    const existing = state.cart.find(item => item.id === product.id);
    if (existing) existing.quantity += 1; else state.cart.push({ ...product, quantity: 1 });
    renderCart(); refreshReview();
  }
  function renderCart() {
    const box = $('#cart-lines'); box.replaceChildren();
    $('#empty-cart').hidden = state.cart.length > 0;
    state.cart.forEach(item => {
      const row = document.createElement('div'); row.className = 'cart-line';
      const info = document.createElement('div'); const name = document.createElement('b'); name.textContent = item.name;
      const price = document.createElement('small'); price.textContent = item.price ? `${item.price} · قیمت هر واحد` : 'قیمت از ووکامرس'; info.append(name, price);
      const qty = document.createElement('input'); qty.className = 'qty-control'; qty.type = 'number'; qty.min = '1'; qty.value = String(item.quantity); qty.setAttribute('aria-label', `تعداد ${item.name}`);
      qty.step = '1';
      qty.addEventListener('change', () => { item.quantity = Math.max(1, Math.floor(Number(qty.value) || 1)); qty.value = String(item.quantity); refreshReview(); });
      const remove = document.createElement('button'); remove.className = 'remove-line'; remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label', `حذف ${item.name}`);
      remove.addEventListener('click', () => { state.cart = state.cart.filter(x => x.id !== item.id); renderCart(); refreshReview(); });
      row.append(info, qty, remove); box.append(row);
    });
  }
  function refreshReview() {
    const show = Boolean(state.customer && state.cart.length);
    $('#review-card').hidden = !show || manualStage !== 'review';
    if (show) {
      const summary = $('#review-summary'); summary.replaceChildren();
      const line1 = document.createElement('div'); line1.className = 'summary-line'; line1.innerHTML = '<span>مشتری</span>';
      const name = document.createElement('span'); name.textContent = nameOf(state.customer); line1.append(name);
      const line2 = document.createElement('div'); line2.className = 'summary-line'; line2.innerHTML = '<span>تعداد قلم</span>';
      const count = document.createElement('span'); count.textContent = String(state.cart.length); line2.append(count);
      const line3 = document.createElement('div'); line3.className = 'summary-line'; line3.innerHTML = '<span>منبع سفارش</span>';
      const source = document.createElement('span'); source.textContent = 'حضوری'; line3.append(source); summary.append(line1,line2,line3);
      state.cart.forEach(item => {
        const line = document.createElement('div'); line.className = 'summary-line';
        const product = document.createElement('span'); product.textContent = item.name;
        const qty = document.createElement('span'); qty.textContent = `${item.quantity.toLocaleString('fa-IR')} عدد`;
        line.append(product, qty); summary.append(line);
      });
      const total = state.cart.reduce((sum, item) => sum + Number(item.price || 0) * item.quantity, 0);
      const line4 = document.createElement('div'); line4.className = 'summary-line';
      const label = document.createElement('span'); label.textContent = 'جمع قیمت کاتالوگ';
      const amount = document.createElement('span'); amount.textContent = `${total.toLocaleString('fa-IR')} · واحد فروشگاه`;
      line4.append(label, amount); summary.append(line4);
      const note = document.createElement('small'); note.textContent = 'مبلغ نهایی و مالیات پس از ثبت توسط ووکامرس محاسبه می‌شود.'; summary.append(note);
    }
    renderReceipt();
    refreshSubmitState();
  }
  $('#order-status').addEventListener('change', refreshSubmitState);
  $('#submit-order').addEventListener('click', async () => {
    if (!state.writesEnabled || !state.customer || !state.cart.length || orderBusy || pendingOrder) return;
    const status = $('#order-status').value;
    if (!status) return message('وضعیت سفارش را انتخاب کن.', 'error');
    const accepted = await confirmAction('تأیید ثبت خرید حضوری', `خرید «${nameOf(state.customer)}» با ${state.cart.length.toLocaleString('fa-IR')} قلم کالا و وضعیت «${statusLabels[status]}» در فروشگاه ثبت می‌شود.`, 'تأیید و ثبت خرید');
    if (!accepted) return;
    pendingOrder = { request_id: crypto.randomUUID().replaceAll('-', ''), confirm: true, customer: state.customer, items: state.cart.map(item => ({ product_id: item.id, quantity: item.quantity })), status };
    try { sessionStorage.setItem('afrand.pendingOrder', JSON.stringify(pendingOrder)); } catch { /* in-memory fallback */ }
    await sendPendingOrder();
  });
  async function sendPendingOrder() {
    if (!pendingOrder || orderBusy || !state.writesEnabled) return;
    orderBusy = true;
    const button = $('#submit-order'); button.textContent = 'در حال ثبت…'; hideMessage(); refreshSubmitState();
    try {
      const result = await api('/api/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(pendingOrder) });
      pendingOrder = null;
      try { sessionStorage.removeItem('afrand.pendingOrder'); } catch { /* in-memory fallback */ }
      const box = $('#order-result'); box.className = 'result-box success'; box.hidden = false;
      box.replaceChildren();
      const text = document.createElement('p'); text.textContent = `سفارش با شناسهٔ ${result.order_id} در ووکامرس ${result.replayed ? 'تأیید شد؛ دوباره ساخته نشد' : 'ثبت شد'}. برچسب سفارش: حضوری. وضعیت: ${statusLabels[result.status] || result.status}.`;
      const history = document.createElement('button'); history.type = 'button'; history.className = 'btn btn-secondary'; history.textContent = 'مشاهدهٔ سابقهٔ مشتری'; history.addEventListener('click', () => loadCustomerDetails(result.customer_id)); box.append(text, history);
      state.customer = null; state.cart = []; $('#selected-customer').hidden = true; $('#customer-details').hidden = true; $('#customer-search-form').reset(); $('#order-status').value = 'on-hold'; renderCart(); showStage('customer'); refreshReview();
    } catch (error) {
      if (([400, 403].includes(error.status) && !error.state) || error.state === 'rejected') {
        pendingOrder = null; try { sessionStorage.removeItem('afrand.pendingOrder'); } catch { /* in-memory fallback */ }
      }
      message(`${errorText(error)} اگر پاسخ نامشخص بود، از «بررسی نتیجهٔ سفارش قبلی» استفاده کن.`, 'error');
    }
    finally { orderBusy = false; button.textContent = 'تأیید و ثبت خرید'; refreshSubmitState(); }
  }
  $('#retry-order').addEventListener('click', sendPendingOrder);
  $$('.tab').forEach(tab => tab.addEventListener('click', () => {
    $$('.tab').forEach(item => { item.classList.toggle('active', item === tab); item.setAttribute('aria-selected', String(item === tab)); item.tabIndex = item === tab ? 0 : -1; });
    $$('.view').forEach(view => { const active = view.id === `view-${tab.dataset.view}`; view.classList.toggle('active', active); view.hidden = !active; });
    hideMessage();
  }));
  $$('.tab').forEach((tab, index) => {
    tab.tabIndex = index === 0 ? 0 : -1;
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const tabs = $$('.tab');
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowLeft' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].click(); tabs[next].focus();
    });
  });
  $('#import-form').addEventListener('submit', event => { event.preventDefault(); previewFile(); });
  $('#excel-file').addEventListener('change', () => {
    mappingInfo = null; lastPreview = null; state.draftId = null; state.validCount = 0;
    $('#import-preview').hidden = true; $('#column-mapping').hidden = true; $('#import-result').hidden = true; refreshSubmitState();
  });
  async function previewFile(mapping, headerRow) {
    if (previewBusy) return;
    hideMessage();
    const file = $('#excel-file').files[0]; if (!file) return message('فایل اکسل را انتخاب کن.', 'error');
    state.draftId = null; state.validCount = 0; $('#import-preview').hidden = true; refreshSubmitState();
    const data = new FormData(); data.append('file', file);
    if (mapping) { data.append('mapping', JSON.stringify(mapping)); data.append('header_row', String(headerRow)); }
    const button = $('#import-form button[type=submit]'); button.disabled = true; button.textContent = 'در حال بررسی…';
    previewBusy = true; $('#excel-file').disabled = true; $('#mapping-form').inert = true;
    try {
      const result = await api('/api/import/preview', { method: 'POST', body: data });
      mappingInfo = result; $('#import-result').hidden = true;
      if (result.mapping_required) { renderMapping(result); return; }
      $('#column-mapping').hidden = true;
      state.draftId = result.draft_id; state.validCount = result.valid_count; lastPreview = result;
      $('#only-errors').checked = false; renderPreview(result); $('#import-preview').hidden = false;
      $('#preview-file-name').textContent = file.name;
      $('#commit-import').textContent = `ثبت ${result.order_count.toLocaleString('fa-IR')} فاکتور آماده`;
    } catch (error) { message(errorText(error), 'error'); }
    finally { previewBusy = false; $('#excel-file').disabled = false; $('#mapping-form').inert = false; button.disabled = false; button.textContent = 'نمایش و بررسی فایل'; refreshSubmitState(); }
  }
  function renderMapping(info) {
    $('#column-mapping').hidden = false;
    const header = $('#header-row'); header.replaceChildren();
    info.header_candidates.forEach(candidate => {
      const option = document.createElement('option'); option.value = candidate.row;
      option.textContent = `ردیف ${candidate.row}: ${candidate.values.filter(Boolean).slice(0, 4).join(' / ')}`; header.append(option);
    });
    header.value = String(info.header_row);
    renderMappingFields(info.headers, info.mapping);
  }
  function renderMappingFields(headers, mapping = {}) {
    const box = $('#mapping-fields'); box.replaceChildren();
    mappingInfo.fields.forEach(field => {
      const label = document.createElement('label'); label.textContent = `${field.label}${field.required ? ' *' : ' (اختیاری)'}`;
      const select = document.createElement('select'); select.className = 'control'; select.name = field.key; select.required = field.required;
      const empty = document.createElement('option'); empty.value = ''; empty.textContent = field.required ? 'انتخاب ستون…' : 'در فایل نیست'; select.append(empty);
      headers.forEach((title, index) => { const option = document.createElement('option'); option.value = String(index); option.textContent = `${index + 1}. ${title || 'بدون عنوان'}`; select.append(option); });
      select.value = mapping[field.key] === undefined ? '' : String(mapping[field.key]); label.append(select); box.append(label);
    });
  }
  $('#header-row').addEventListener('change', () => {
    const candidate = mappingInfo.header_candidates.find(row => String(row.row) === $('#header-row').value);
    renderMappingFields(candidate.values, {});
  });
  $('#edit-mapping').addEventListener('click', () => {
    state.draftId = null; state.validCount = 0; $('#import-preview').hidden = true; refreshSubmitState(); renderMapping(mappingInfo);
  });
  $('#mapping-form').addEventListener('submit', event => {
    event.preventDefault(); const mapping = {};
    for (const [key, value] of new FormData(event.currentTarget)) if (value !== '') mapping[key] = Number(value);
    if (new Set(Object.values(mapping)).size !== Object.values(mapping).length) return message('هر ستون فایل را فقط به یک فیلد اختصاص دهید.', 'error');
    previewFile(mapping, Number($('#header-row').value));
  });
  $('#only-errors').addEventListener('change', () => { if (lastPreview) renderPreview(lastPreview); });
  function renderPreview(result) {
    previewInvalidRows = result.invalid_rows || [];
    const summary = $('#preview-summary'); summary.replaceChildren();
    [[`${result.order_count.toLocaleString('fa-IR')} فاکتور آماده ثبت`, 'ok'], [`${result.valid_count.toLocaleString('fa-IR')} ردیف معتبر`, 'ok'], [`${result.invalid_count.toLocaleString('fa-IR')} ردیف نیازمند اصلاح`, 'bad']].forEach(([text, cls]) => { const chip = document.createElement('span'); chip.className = `summary-chip ${cls}`; chip.textContent = text; summary.append(chip); });
    const body = $('#preview-rows'); body.replaceChildren();
    [...(result.valid_rows || []).map(row => ({ ...row, valid: true })), ...(result.invalid_rows || []).map(row => ({ ...row, valid: false }))].filter(row => !$('#only-errors').checked || !row.valid).sort((a,b) => a.row_number - b.row_number).forEach(row => {
      const tr = document.createElement('tr');
      const amount = row.line_total !== undefined && row.line_total !== '' ? `${row.line_total} ${row.currency || '(واحد مشخص نشده)'}` : '—';
      [row.row_number, [row.order_ref, row.invoice_date].filter(Boolean).join(' / '), `${row.customer_name} · ${row.customer_phone}`, `${row.product_name || row.product_sku} · ${row.quantity} ${row.unit || 'عدد'}`, amount].forEach(value => { const td = document.createElement('td'); td.textContent = escapeText(value); tr.append(td); });
      const td = document.createElement('td'); const badge = document.createElement('span'); badge.className = `status ${row.valid ? 'ok' : 'bad'}`; badge.textContent = row.valid ? 'قابل ثبت' : (row.errors || []).join('، '); td.append(badge); tr.append(td); body.append(tr);
    });
  }
  $('#import-status').addEventListener('change', refreshSubmitState);
  $('#commit-import').addEventListener('click', async () => {
    if (!state.draftId || !state.writesEnabled || !state.validCount || importBusy) return;
    const status = $('#import-status').value;
    if (!status) return message('وضعیت سفارش‌ها را انتخاب کن.', 'error');
    if (!await confirmAction('تأیید ثبت فاکتورها', `${lastPreview.order_count.toLocaleString('fa-IR')} فاکتور شامل ${state.validCount.toLocaleString('fa-IR')} ردیف معتبر با وضعیت «${statusLabels[status]}» ثبت می‌شود. فاکتورهای خطادار ثبت نمی‌شوند.`, 'ثبت فاکتورهای آماده')) return;
    const button = $('#commit-import'); button.disabled = true; button.textContent = 'در حال ثبت…'; hideMessage();
    importBusy = true; refreshSubmitState();
    try {
      const result = await api('/api/import/commit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ draft_id: state.draftId, status, confirm: true }) });
      const invalidRows = [...previewInvalidRows];
      const box = $('#import-result'); box.hidden = false; box.className = 'card result-box success';
      box.replaceChildren();
      const summary = document.createElement('p'); summary.textContent = `ثبت‌شده: ${result.created} سفارش · نیازمند بررسی: ${result.needs_review} · تکراری و بدون ثبت دوباره: ${result.replayed}`; box.append(summary);
      result.results.forEach(row => {
        const item = document.createElement('div'); item.className = 'customer-order';
        item.textContent = `${row.order_ref}: ${row.error || `سفارش ${row.order_id} · ${row.replayed ? 'قبلاً ثبت شده' : 'ثبت شد'}`}`; box.append(item);
      });
      invalidRows.forEach(row => {
        const item = document.createElement('div'); item.className = 'customer-order';
        item.textContent = `ردیف ${row.row_number} · ${row.order_ref}: ثبت نشد — ${(row.errors || []).join('، ')}`; box.append(item);
      });
      const download = document.createElement('button'); download.type = 'button'; download.className = 'btn btn-secondary'; download.textContent = 'دریافت گزارش ثبت';
      download.addEventListener('click', () => {
        const safe = value => { const text = String(value ?? '').replace(/[\t\r\n]/g, ' '); return /^[=+@-]/.test(text) ? `'${text}` : text; };
        const lines = [['order_ref','order_id','customer_id','result','error'], ...result.results.map(row => [row.order_ref, row.order_id, row.customer_id, row.replayed ? 'duplicate' : row.state, row.error]), ...invalidRows.map(row => [row.order_ref, '', '', `invalid-row-${row.row_number}`, (row.errors || []).join('، ')])];
        const url = URL.createObjectURL(new Blob(['\uFEFF' + lines.map(row => row.map(safe).join('\t')).join('\n')], { type: 'text/tab-separated-values;charset=utf-8' }));
        const a = document.createElement('a'); a.href = url; a.download = 'afrand-import-results.tsv'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      }); box.append(download);
      state.draftId = null; state.validCount = 0; $('#import-preview').hidden = true; $('#import-form').reset(); refreshSubmitState();
    } catch (error) { message(`${errorText(error)} پیش از تلاش دوباره، نتیجهٔ سفارش‌ها را در ووکامرس بررسی کن.`, 'error'); }
    finally { importBusy = false; button.textContent = 'ثبت فاکتورهای آماده'; refreshSubmitState(); }
  });
  loadStatus();
})();
