"""Read-only hosted wallet checks: preview/cancel only; zero external writes."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
base = os.environ.get('AFRAND_DEMO_URL', 'https://wp-woo-demo.mahdihz.ir').rstrip('/')
out = root / 'instance/verification/wallet-deployment'
out.mkdir(parents=True, exist_ok=True)
suffix = 'ip' if '141.11.250.83' in base else 'domain'
errors, native_dialogs = [], []
ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(user_agent=ua, viewport={'width':1366,'height':950}, reduced_motion='reduce')
    response = context.request.get(base+'/api/status')
    assert response.status == 401 and 'www-authenticate' not in response.headers
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('dialog', lambda dialog: (native_dialogs.append(dialog.type), dialog.dismiss()))
    page.goto(base, wait_until='networkidle')
    page.locator('#username').fill('demo')
    page.locator('#password').fill('wrong-password')
    page.get_by_role('button',name='ورود',exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('درست نیست')
    page.locator('#username').fill('demo')
    page.locator('#password').fill((root/'instance/demo-password').read_text().strip())
    page.get_by_role('button',name='ورود',exact=True).click()
    page.wait_for_url(base+'/')
    expect(page.locator('#connection-state')).to_contain_text('واریز فعال',timeout=25000)
    status = context.request.get(base+'/api/status').json()
    assert status['connected'] and status['writes_enabled']
    assert page.locator('input[type=email]').count() == 0
    assert context.request.get(base+'/api/products').status == 404
    assert context.request.post(base+'/api/orders',headers={'Origin':base},data={}).status == 404
    assert context.request.post(base+'/api/wallet/credits',headers={'Origin':'https://example.invalid'},data={}).status == 403
    page.locator('#customer-query').fill('09120000001')
    page.locator('#customer-search-form button').click()
    page.locator('#customer-results').get_by_role('button',name='انتخاب',exact=True).first.click(timeout=25000)
    expect(page.locator('#wallet-balance')).to_contain_text('تومان',timeout=25000)
    expect(page.locator('label[for=credit-amount]')).to_contain_text('مبلغ خرید کاربر')
    expect(page.locator('#submit-credit')).to_have_text('بررسی و تأیید مبلغ خرید')
    expect(page.locator('#cashback-percent')).to_have_value('5')
    page.locator('#credit-amount').fill('100000')
    expect(page.locator('#cashback-summary')).to_contain_text('۵٬۰۰۰ تومان')
    page.locator('#cashback-percent').fill('7.5')
    expect(page.locator('#cashback-summary')).to_contain_text('۷٬۵۰۰ تومان')
    page.locator('#submit-credit').click()
    expect(page.locator('#confirm-dialog')).to_be_visible()
    expect(page.locator('#confirm-description')).to_contain_text('۷٬۵۰۰ تومان')
    expect(page.locator('#confirm-dialog')).to_contain_text('تأیید مبلغ خرید کاربر')
    page.screenshot(path=str(out/f'confirmation-{suffix}.png'),full_page=True)
    page.locator('#confirm-cancel').click()
    expect(page.locator('#confirm-dialog')).to_be_hidden()
    sample = context.request.get(base+'/demo.xlsx')
    assert sample.ok and sample.body().startswith(b'PK')
    page.locator('#tab-import').click()
    page.locator('#excel-file').set_input_files(str(root/'demo/afrand-wallet-sample.xlsx'))
    with page.expect_response('**/api/import/preview',timeout=45000) as preview:
        page.locator('#preview-file').click()
    assert preview.value.ok, preview.value.text()
    data = preview.value.json()
    assert data['valid_count'] == 2 and data['invalid_count'] == 1
    assert data['purchase_total']=='150000' and data['total']=='7500' and data['cashback_percent']=='5'
    expect(page.locator('#commit-import')).to_be_enabled(timeout=25000)
    page.locator('#commit-import').click()
    expect(page.locator('#confirm-dialog')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#confirm-dialog')).to_be_hidden()
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(out/f'mobile-{suffix}.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.reload(wait_until='networkidle')
    expect(page.locator('#connection-state')).to_contain_text('واریز فعال',timeout=25000)
    page.get_by_role('button',name='خروج',exact=True).click()
    expect(page.locator('h1')).to_have_text('ورود به پنل')
    assert context.request.get(base+'/api/status').status == 401
    assert not errors and not native_dialogs, (errors,native_dialogs)
    report = {'url':base,'wallet_connected':True,'writes_enabled':True,'no_email':True,
              'orders_disabled':True,'wrong_password_rejected':True,'session_reload_logout':True,
              'customer_search_balance':True,'cashback_default_and_custom_preview':True,'cashback_excel_total':'7500','sample_download':True,'preview_valid_rows':2,
              'preview_invalid_rows':1,'custom_confirmations_cancelled':True,
              'cross_origin_blocked':True,'mobile_overflow':False,'external_writes':0,
              'javascript_errors':errors,'native_dialogs':native_dialogs}
    (out/f'report-{suffix}.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
    browser.close()
