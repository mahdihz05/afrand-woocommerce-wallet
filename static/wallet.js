(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const state = { connected:false, writes:false, currency:'IRT', customer:null, balance:null, page:1, query:'', hasMore:false, draft:null, preview:null, mapping:null, previewBusy:false, importBusy:false, customerBusy:false, creditBusy:false };
  let pending = null, searchVersion = 0, detailVersion = 0, searchController = null;
  let active = 0;
  const busy = new Map();
  try { pending = JSON.parse(sessionStorage.getItem('afrand.pendingWallet') || 'null'); } catch { /* storage unavailable */ }
  if (pending && (!pending.payload || !pending.customer || !/^[a-f0-9]{32}$/.test(pending.payload.request_id))) pending = null;
  const unit = currency => currency === 'IRR' ? 'ریال' : 'تومان';
  const money = (amount,currency=state.currency) => `${Number(amount).toLocaleString('fa-IR')} ${unit(currency)}`;
  const name = customer => customer ? [customer.first_name,customer.last_name].filter(Boolean).join(' ') || customer.name || 'مشتری' : '';
  function message(text,kind='info') { const box=$('#global-message'); box.textContent=text; box.className=`message ${kind}`; box.hidden=false; }
  function progress(controls) {
    active++; $('#request-progress').hidden=false;
    controls.filter(Boolean).forEach(control => { busy.set(control,(busy.get(control)||0)+1); control.classList.add('is-loading'); control.setAttribute('aria-busy','true'); });
    return () => { active--; $('#request-progress').hidden=active===0; controls.filter(Boolean).forEach(control=> { const count=busy.get(control)-1; if(count) busy.set(control,count); else { busy.delete(control); control.classList.remove('is-loading'); control.removeAttribute('aria-busy'); } }); };
  }
  async function api(url,options={},controls=[]) {
    const controller=new AbortController(); const external=options.signal;
    const abort=()=>controller.abort(); external?.addEventListener('abort',abort,{once:true}); if(external?.aborted) controller.abort();
    let timedOut=false;
    const timer=setTimeout(()=>{ timedOut=true; controller.abort(); }, options.method ? 40000 : 12000);
    const done=progress(controls);
    try {
      const response=await fetch(url,{...options,signal:controller.signal});
      const data=await response.json();
      if(!response.ok) { if(response.status===401 && data.category==='authentication') location.assign('/login'); const error=new Error(data.error||'درخواست انجام نشد.'); error.status=response.status; error.state=data.state; throw error; }
      return data;
    } catch(error) {
      if(error.name==='AbortError' && external?.aborted && !timedOut) throw error;
      if(error.status) throw error;
      throw new Error(timedOut ? 'مهلت دریافت پاسخ تمام شد. اتصال را بررسی کنید؛ نتیجهٔ واریز قبلی را پیش از واریز تازه بررسی کنید.' : 'ارتباط یا دریافت پاسخ کامل نشد. دوباره اتصال را بررسی کنید.');
    } finally { clearTimeout(timer); external?.removeEventListener('abort',abort); done(); }
  }
  const post=(url,data,controls)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)},controls);
  function confirmAction(title,description,action) {
    const dialog=$('#confirm-dialog'); if(dialog.open) return Promise.resolve(false);
    $('#confirm-title').textContent=title; $('#confirm-description').textContent=description; $('#confirm-accept').textContent=action; dialog.returnValue='';
    return new Promise(resolve=>{ dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true}); dialog.showModal(); });
  }
  function refresh() {
    $('#save-customer-button').disabled=!state.writes||state.customerBusy||Boolean(pending);
    $('#submit-credit').disabled=!state.writes||!state.customer||state.creditBusy||Boolean(pending);
    $('#commit-import').disabled=!state.writes||!state.draft||state.previewBusy||state.importBusy||Boolean(pending);
    $('#manual-controls').inert=Boolean(pending)||state.importBusy||state.customerBusy;
    $('#import-form').inert=state.importBusy||Boolean(pending);
    $('#mapping-form').inert=state.importBusy||state.previewBusy||Boolean(pending);
    $('#edit-mapping').disabled=state.importBusy;
    $('#import-cashback-percent').disabled=state.importBusy||state.previewBusy||Boolean(pending);
    $('#pending-credit').hidden=!pending;
    $('#retry-credit').disabled=!state.writes||state.creditBusy;
  }
  async function loadStatus() {
    const button=$('#refresh-connection'); if(button.disabled) return; button.disabled=true;
    state.connected=false; state.writes=false; refresh();
    const badge=$('#connection-state'); badge.textContent='در حال بررسی اتصال…'; badge.className='connection';
    try {
      const data=await api('/api/status',{},[button]); state.connected=data.connected; state.writes=data.writes_enabled; state.currency=data.currency;
      badge.textContent=state.writes?'کیف پول متصل · واریز فعال':'کیف پول متصل · فقط خواندنی'; badge.className='connection ok';
      $$('.currency-label').forEach(label=>label.textContent=unit(state.currency));
    } catch(error) { badge.textContent='اتصال کیف پول ناموفق'; badge.className='connection bad'; message(error.message,'error'); }
    finally { button.disabled=false; refresh(); }
  }
  $('#refresh-connection').addEventListener('click',loadStatus);
  function renderCustomers(customers,append=false) {
    const box=$('#customer-results'); if(!append) box.replaceChildren();
    if(!customers.length && !append) { box.textContent='مشتری پیدا نشد؛ می‌توانید مشتری جدید بسازید.'; return; }
    customers.forEach(customer=>{ const row=document.createElement('div'); row.className='result-option'; const info=document.createElement('div'); const title=document.createElement('b'); title.textContent=name(customer); const phone=document.createElement('small'); phone.textContent=customer.phone||`شناسهٔ وردپرس: ${customer.id}`; info.append(title,phone); const button=document.createElement('button'); button.type='button'; button.className='btn btn-secondary'; button.textContent='انتخاب'; button.addEventListener('click',()=>selectCustomer(customer)); row.append(info,button); box.append(row); });
  }
  $('#customer-search-form').addEventListener('submit',async event=>{
    event.preventDefault(); searchController?.abort(); searchController=new AbortController(); const controller=searchController; const version=++searchVersion;
    const q=$('#customer-query').value.trim(); if(q.length<2) return message('نام یا موبایل مشتری را وارد کنید.','error');
    const button=event.currentTarget.querySelector('button'); $('#customer-results').textContent='در حال جست‌وجو…'; $('#load-more-customers').hidden=true; $('#global-message').hidden=true;
    try { const data=await api(`/api/customers?q=${encodeURIComponent(q)}`,{signal:controller.signal},[button]); if(version!==searchVersion) return; state.query=q; state.page=data.page; state.hasMore=data.has_more; renderCustomers(data.customers); $('#load-more-customers').hidden=!state.hasMore; }
    catch(error) { if(version===searchVersion && error.name!=='AbortError') { $('#customer-results').textContent='جست‌وجو انجام نشد؛ دوباره تلاش کنید.'; message(error.message,'error'); } }
  });
  $('#load-more-customers').addEventListener('click',async()=>{
    const button=$('#load-more-customers'); if(button.disabled||!state.hasMore) return; button.disabled=true; const version=searchVersion;
    try { const data=await api(`/api/customers?q=${encodeURIComponent(state.query)}&page=${state.page+1}`,{},[button]); if(version!==searchVersion) return; state.page=data.page; state.hasMore=data.has_more; renderCustomers(data.customers,true); button.hidden=!state.hasMore; }
    catch(error) { message(error.message,'error'); } finally { button.disabled=false; }
  });
  async function selectCustomer(customer) {
    if(pending||state.importBusy) return;
    state.customer=customer; state.balance=null; const version=++detailVersion;
    $('#credit-customer').textContent=`واریز برای ${name(customer)} · ${customer.phone||`شناسهٔ ${customer.id}`}`;
    $('#new-customer-panel').hidden=true; $('#credit-card').hidden=false; $('#credit-result').hidden=true;
    const box=$('#selected-customer'); box.replaceChildren(); const title=document.createElement('h2'); title.textContent=name(customer); const phone=document.createElement('p'); phone.className='customer-contact'; phone.textContent=customer.phone||`شناسهٔ ${customer.id}`; box.append(title,phone);
    $('#wallet-balance').textContent='در حال دریافت…'; $('#balance-caption').textContent='موجودی از وردپرس خوانده می‌شود.'; $('#wallet-history').hidden=true; refresh();
    try { const data=await api(`/api/customers/${customer.id}`,{},[$('#wallet-balance')]); if(version!==detailVersion) return; state.customer=data.customer; state.balance=data.wallet.balance; $('#wallet-balance').textContent=money(data.wallet.balance,data.wallet.currency); if(data.wallet.history_error) $('#wallet-transactions').textContent='خواندن سابقه انجام نشد؛ مشتری را دوباره انتخاب کنید.'; else renderTransactions(data.wallet.transactions); $('#wallet-history').hidden=false; }
    catch(error) { if(version===detailVersion) { $('#wallet-balance').textContent='—'; $('#balance-caption').textContent='موجودی دریافت نشد؛ مشتری را دوباره انتخاب کنید.'; message(error.message,'error'); } }
  }
  function renderTransactions(rows) {
    const box=$('#wallet-transactions'); box.replaceChildren(); if(!rows.length) { box.textContent='هنوز تراکنشی ثبت نشده است.'; return; }
    rows.forEach(row=>{ const item=document.createElement('div'); item.className='wallet-transaction'; const amount=document.createElement('b'); amount.className=row.type; amount.textContent=`${row.type==='credit'?'＋ واریز':'− برداشت'} ${money(row.amount,row.currency)}`; const info=document.createElement('small'); info.textContent=`تراکنش ${row.transaction_id} · ${row.date}`; const note=document.createElement('small'); note.textContent=(row.details||'').replace(/\s*\[AFRAND:[a-f0-9]{64}\]/g,''); item.append(amount,info,note); box.append(item); });
  }
  $('#show-new-customer').addEventListener('click',()=>{ $('#new-customer-panel').hidden=false; $('#new-customer-form [name=first_name]').focus(); });
  $('#cancel-new-customer').addEventListener('click',()=>$('#new-customer-panel').hidden=true);
  $('#new-customer-form').addEventListener('submit',async event=>{
    event.preventDefault(); if(!state.writes||state.customerBusy||pending) return;
    const customer=Object.fromEntries(new FormData(event.currentTarget));
    if(!await confirmAction('ثبت مشتری',`حساب «${name(customer)}» با موبایل ${customer.phone} در وردپرس ثبت یا انتخاب می‌شود. ایمیل لازم نیست.`, 'ثبت مشتری')) return;
    state.customerBusy=true; refresh();
    try { const result=await post('/api/customers',{...customer,confirm:true},[$('#save-customer-button')]); await selectCustomer(result.customer); $('#new-customer-form').reset(); message(result.replayed?'حساب موجود انتخاب شد؛ مشتری تکراری ساخته نشد.':'مشتری در وردپرس ساخته شد. حالا می‌توانید مبلغ خرید را وارد کنید.','success'); }
    catch(error) { message(`${error.message} در صورت قطع پاسخ، همان موبایل را دوباره ثبت یا جست‌وجو کنید.`, 'error'); }
    finally { state.customerBusy=false; refresh(); }
  });
  function parseAmount(text) {
    let value=text.replace(/[۰-۹٠-٩]/g,digit=>'۰۱۲۳۴۵۶۷۸۹'.includes(digit)?'۰۱۲۳۴۵۶۷۸۹'.indexOf(digit):'٠١٢٣٤٥٦٧٨٩'.indexOf(digit)).trim().replaceAll('٬',',');
    if(value.includes(',')) { if(!/^\d{1,3}(,\d{3})+$/.test(value)) throw new Error('مبلغ را با جداکنندهٔ صحیح وارد کنید؛ مثلاً ۱۰۰٬۰۰۰.'); value=value.replaceAll(',',''); }
    if(!/^\d+$/.test(value)||Number(value)<=0||Number(value)>100000000) throw new Error('مبلغ باید عدد صحیح مثبت و حداکثر ۱۰۰ میلیون باشد.');
    return String(Number(value));
  }
  function parsePercent(text) {
    const value=String(text).replace(/[۰-۹٠-٩]/g,digit=>'۰۱۲۳۴۵۶۷۸۹'.includes(digit)?'۰۱۲۳۴۵۶۷۸۹'.indexOf(digit):'٠١٢٣٤٥٦٧٨٩'.indexOf(digit)).trim().replaceAll('٫','.');
    if(!/^\d{1,3}(\.\d{1,2})?$/.test(value)||Number(value)<=0||Number(value)>100) throw new Error('درصد کش‌بک باید بیشتر از صفر و حداکثر ۱۰۰، با حداکثر دو رقم اعشار باشد.');
    return String(Number(value));
  }
  function cashbackValues() {
    const purchase=parseAmount($('#credit-amount').value),percent=parsePercent($('#cashback-percent').value);
    const basisPoints=Math.round(Number(percent)*100);
    const amount=Math.floor(Number(purchase)*basisPoints/10000);
    if(amount<1) throw new Error('کش‌بک این خرید کمتر از یک واحد پول است؛ مبلغ خرید یا درصد را افزایش دهید.');
    return {purchase,percent,amount};
  }
  function updateCashback() {
    try { const v=cashbackValues(); $('#cashback-summary').textContent=`خرید ${money(v.purchase)} × ${v.percent}٪ = ${money(v.amount)} کش‌بک واریزی`; }
    catch(error) { $('#cashback-summary').textContent=$('#credit-amount').value?error.message:'مبلغ خرید را وارد کنید تا کش‌بک محاسبه شود.'; }
  }
  ['credit-amount','cashback-percent'].forEach(id=>$('#'+id).addEventListener('input',updateCashback));
  $('#credit-form').addEventListener('submit',async event=>{
    event.preventDefault(); if(!state.writes||!state.customer||pending||state.creditBusy) return;
    let values; try { values=cashbackValues(); } catch(error) { return message(error.message,'error'); }
    const customer=state.customer;
    const requestId=Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,'0')).join('');
    const payload={request_id:requestId,customer_id:customer.id,purchase_amount:values.purchase,cashback_percent:values.percent,currency:state.currency,note:$('#credit-note').value.trim(),confirm:true};
    if(!await confirmAction('تأیید مبلغ خرید کاربر',`مبلغ خرید: ${money(values.purchase)}؛ کش‌بک: ${values.percent}٪؛ فقط ${money(values.amount)} به کیف پول «${name(customer)}» با موبایل ${customer.phone||'ثبت‌نشده'} واریز می‌شود.`, 'تأیید و ثبت مبلغ خرید')) return;
    pending={payload,customer};
    if (!savePending()) { pending=null; refresh(); return message('ذخیرهٔ شناسهٔ واریز در مرورگر ممکن نیست. دسترسی ذخیره‌سازی مرورگر را فعال کنید و دوباره تلاش کنید؛ مبلغ واریز نشد.','error'); }
    await sendCredit(false);
  });
  function savePending() { try { if(pending) sessionStorage.setItem('afrand.pendingWallet',JSON.stringify(pending)); else sessionStorage.removeItem('afrand.pendingWallet'); } catch { refresh(); return false; } refresh(); return true; }
  async function sendCredit(recover=true) {
    if(!pending||state.creditBusy||!state.writes) return;
    state.creditBusy=true; refresh(); $('#global-message').hidden=true;
    try {
      let result;
      if(recover) { try { result=await api(`/api/wallet/operations/${pending.payload.request_id}`,{},[$('#retry-credit')]); } catch(error) { if(error.status!==404) throw error; } }
      if(!result) result=await post('/api/wallet/credits',pending.payload,[$('#submit-credit'),$('#retry-credit')]);
      const customer=pending.customer; pending=null; savePending();
      const box=$('#credit-result'); box.replaceChildren(); const text=document.createElement('p'); text.textContent=`${money(result.amount,result.currency)} ${result.replayed?'مبلغ خرید قبلاً ثبت شده است؛ دوباره واریز نشد':'کش‌بک خرید به کیف پول واریز شد'}. شناسهٔ تراکنش وردپرس: ${result.transaction_id}`; box.append(text);
      $('#credit-form').reset(); updateCashback(); await selectCustomer(customer); box.hidden=false;
    } catch(error) { if(error.state==='rejected'||error.status===400) { pending=null; savePending(); } message(error.message,'error'); }
    finally { state.creditBusy=false; refresh(); }
  }
  $('#retry-credit').addEventListener('click',()=>sendCredit(true));
  $$('.tab').forEach((tab,index)=>{ tab.addEventListener('click',()=>{ $$('.tab').forEach(item=>{ const selected=item===tab; item.classList.toggle('active',selected); item.setAttribute('aria-selected',String(selected)); item.tabIndex=selected?0:-1; }); $$('.view').forEach(view=>{ const selected=view.id===`view-${tab.dataset.view}`; view.classList.toggle('active',selected); view.hidden=!selected; }); }); tab.addEventListener('keydown',event=>{ if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return; event.preventDefault(); const tabs=$$('.tab'); const next=event.key==='Home'?0:event.key==='End'?1:1-index; tabs[next].click(); tabs[next].focus(); }); });
  $('#excel-file').addEventListener('change',()=>{ state.draft=null; state.preview=null; state.mapping=null; $('#import-preview').hidden=true; $('#column-mapping').hidden=true; $('#import-result').hidden=true; refresh(); });
  $('#import-cashback-percent').addEventListener('input',()=>{ state.draft=null; state.preview=null; $('#import-preview').hidden=true; refresh(); });
  $('#import-form').addEventListener('submit',event=>{ event.preventDefault(); previewFile(); });
  async function previewFile(mapping,header) {
    if(state.previewBusy||state.importBusy||pending) return;
    const file=$('#excel-file').files[0]; if(!file) return message('فایل اکسل را انتخاب کنید.','error');
    let percent; try { percent=parsePercent($('#import-cashback-percent').value); } catch(error) { return message(error.message,'error'); }
    state.previewBusy=true; state.draft=null; state.preview=null; $('#import-preview').hidden=true; $('#excel-file').disabled=true; refresh();
    const form=new FormData(); form.append('file',file); form.append('cashback_percent',percent); if(mapping) { form.append('mapping',JSON.stringify(mapping)); form.append('header_row',String(header)); }
    try { const data=await api('/api/import/preview',{method:'POST',body:form},[$('#preview-file'),$('#mapping-form button')]); state.mapping=data; $('#import-result').hidden=true; if(data.mapping_required) { renderMapping(data); return; } state.preview=data; state.draft=data.valid_count?data.draft_id:null; $('#column-mapping').hidden=true; $('#only-errors').checked=false; renderPreview(); $('#preview-file-name').textContent=file.name; $('#import-preview').hidden=false; }
    catch(error) { message(error.message,'error'); }
    finally { state.previewBusy=false; $('#excel-file').disabled=false; refresh(); }
  }
  function renderMapping(data) {
    $('#column-mapping').hidden=false; const select=$('#header-row'); select.replaceChildren(); data.header_candidates.forEach(row=>{ const option=new Option(`ردیف ${row.row}: ${row.values.filter(Boolean).slice(0,4).join(' / ')}`,row.row); select.append(option); }); select.value=String(data.header_row); mappingFields(data.headers,data.mapping);
  }
  function mappingFields(headers,mapping={}) { const box=$('#mapping-fields'); box.replaceChildren(); state.mapping.fields.forEach(field=>{ const label=document.createElement('label'); label.textContent=field.label+(field.required?' *':' (اختیاری)'); const select=document.createElement('select'); select.className='control'; select.name=field.key; select.required=field.required; select.append(new Option('انتخاب ستون…','')); headers.forEach((title,index)=>select.append(new Option(`${index+1}. ${title||'بدون عنوان'}`,index))); select.value=mapping[field.key]===undefined?'':String(mapping[field.key]); label.append(select); box.append(label); }); }
  $('#header-row').addEventListener('change',()=>mappingFields(state.mapping.header_candidates.find(row=>String(row.row)===$('#header-row').value).values));
  $('#mapping-form').addEventListener('submit',event=>{ event.preventDefault(); const mapping={}; for(const [key,value] of new FormData(event.currentTarget)) if(value!=='') mapping[key]=Number(value); if(new Set(Object.values(mapping)).size!==Object.values(mapping).length) return message('هر ستون را فقط به یک مورد اختصاص دهید.','error'); previewFile(mapping,Number($('#header-row').value)); });
  $('#edit-mapping').addEventListener('click',()=>{ state.draft=null; $('#import-preview').hidden=true; renderMapping(state.mapping); refresh(); });
  $('#only-errors').addEventListener('change',renderPreview);
  async function commitDraft(draft,total) {
    const combined={results:[],created:0,replayed:0,needs_review:0};
    let offset=0;
    while(offset<total) {
      $('#commit-import').textContent=`در حال واریز ${Math.min(offset+1,total).toLocaleString('fa-IR')} از ${total.toLocaleString('fa-IR')}…`;
      const batch=await post('/api/import/commit',{draft_id:draft,offset,confirm:true},[$('#commit-import')]);
      combined.results.push(...batch.results); combined.created+=batch.created; combined.replayed+=batch.replayed; combined.needs_review+=batch.needs_review;
      if(batch.done) return combined;
      if(!Number.isInteger(batch.next_offset)||batch.next_offset<=offset) throw new Error('نتیجهٔ فایل کامل دریافت نشد؛ فایل را با همان شماره سندها دوباره بررسی کنید.');
      offset=batch.next_offset;
    }
    return combined;
  }
  function renderPreview() {
    const data=state.preview; if(!data) return;
    const summary=$('#preview-summary'); summary.replaceChildren(); [[`${data.valid_count.toLocaleString('fa-IR')} واریز معتبر`,'ok'],[`${data.invalid_count.toLocaleString('fa-IR')} ردیف نیازمند اصلاح`,'bad'],[`جمع خرید: ${money(data.purchase_total,data.currency)}`,'ok'],[`${data.cashback_percent}٪ کش‌بک؛ جمع واریز: ${money(data.total,data.currency)}`,'ok']].forEach(([text,kind])=>{ const chip=document.createElement('span'); chip.className=`summary-chip ${kind}`; chip.textContent=text; summary.append(chip); });
    const body=$('#preview-rows'); body.replaceChildren(); [...data.valid_rows,...data.invalid_rows].sort((a,b)=>a.row_number-b.row_number).filter(row=>!$('#only-errors').checked||row.errors.length).forEach(row=>{ const tr=document.createElement('tr'); [row.row_number,[row.reference,row.date].filter(Boolean).join(' / '),`${row.customer_name} · ${row.phone}`,row.amount?money(row.amount,row.currency):'—',row.errors.length?row.errors.join('، '):row.customer_action].forEach(text=>{ const cell=document.createElement('td'); cell.textContent=String(text??''); tr.append(cell); }); body.append(tr); });
  }
  $('#commit-import').addEventListener('click',async()=>{
    if(!state.draft||!state.writes||state.importBusy||pending) return;
    const data=state.preview, draft=state.draft;
    if(!await confirmAction('تأیید واریزهای اکسل',`${data.valid_count.toLocaleString('fa-IR')} خرید با جمع ${money(data.purchase_total,data.currency)} و کش‌بک ${data.cashback_percent}٪؛ فقط جمع ${money(data.total,data.currency)} ثبت می‌شود. مشتریان جدید ساخته می‌شوند؛ ردیف‌های خطادار واریز نمی‌شوند.`, 'ثبت واریزهای معتبر')) return;
    state.importBusy=true; refresh();
    try { const result=await commitDraft(draft,data.valid_count); const box=$('#import-result'); box.replaceChildren(); const summary=document.createElement('p'); summary.textContent=`واریز تازه: ${result.created} · قبلاً واریز شده: ${result.replayed} · نیازمند بررسی: ${result.needs_review}`; box.append(summary); result.results.forEach(row=>{ const item=document.createElement('p'); item.textContent=`سند ${row.reference}: ${row.error||`تراکنش ${row.transaction_id} · ${row.replayed?'بدون واریز دوباره':'واریز شد'}`}`; box.append(item); }); data.invalid_rows.forEach(row=>{ const item=document.createElement('p'); item.textContent=`ردیف ${row.row_number} · واریز نشد: ${row.errors.join('، ')}`; box.append(item); }); const download=document.createElement('button'); download.className='btn btn-secondary'; download.type='button'; download.textContent='دریافت گزارش واریز'; download.addEventListener('click',()=>downloadReport(result,data.invalid_rows)); box.append(download); box.hidden=false; box.className=`card result-box ${result.needs_review?'':'success'}`; if(!result.needs_review) { state.draft=null; $('#import-preview').hidden=true; } }
    catch(error) { message(`${error.message} فایل و شماره سندها را تغییر ندهید؛ بررسی دوباره با همان سندها از واریز تکراری جلوگیری می‌کند.`,'error'); }
    finally { state.importBusy=false; $('#commit-import').textContent='تأیید واریزهای معتبر'; refresh(); }
  });
  function downloadReport(result,invalid) { const safe=value=>{ const text=String(value??'').replace(/[\t\r\n]/g,' '); return /^[=+@-]/.test(text)?`'${text}`:text; }; const rows=[['شماره سند','شناسه تراکنش','شناسه مشتری','مبلغ خرید','درصد کش‌بک','کش‌بک واریزی','واحد پول','نتیجه'],...result.results.map(row=>[row.reference,row.transaction_id,row.customer_id,row.purchase_amount,row.cashback_percent,row.amount,row.currency,row.error||(row.replayed?'تکراری؛ بدون واریز دوباره':'واریز شد')]),...invalid.map(row=>[row.reference,'','',row.purchase_amount,row.cashback_percent,'',row.currency,row.errors.join('، ')])]; const url=URL.createObjectURL(new Blob(['\uFEFF'+rows.map(row=>row.map(safe).join('\t')).join('\n')],{type:'text/tab-separated-values;charset=utf-8'})); const a=document.createElement('a'); a.href=url; a.download='afrand-wallet-results.tsv'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); }
  refresh(); loadStatus();
})();
