"""Browser checks for recovery and stale previews, without WooCommerce writes."""

import json
import sys
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run import app
from scripts.demo_access import authorize_local_browser


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    authorize_local_browser(app, page)
    requests = []
    errors = []
    preview_count = [0]
    customer = {'id': 7, 'first_name': 'مریم', 'last_name': 'نمونه', 'phone': '09120000001', 'email': 'demo@example.test'}
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('dialog', lambda dialog: dialog.accept())

    def mock_api(route):
        path = urlparse(route.request.url).path
        data = {}
        status = 200
        if path == '/api/status':
            data = {'connected': True, 'writes_enabled': True}
        elif path == '/api/customers':
            data = {'customers': [customer], 'page': 1, 'has_more': False}
        elif path == '/api/products':
            data = {'products': [{'id': 27, 'name': 'دفتر آزمایشی', 'sku': 'DEMO', 'price': '120000'}]}
        elif path == '/api/orders':
            requests.append(route.request.post_data_json)
            if len(requests) == 1:
                route.abort('failed')
                return
            data = {'order_id': 99, 'customer_id': 7, 'status': 'on-hold', 'replayed': True}
        elif path == '/api/import/preview':
            preview_count[0] += 1
            if preview_count[0] == 1:
                data = {'draft_id': 'draft', 'valid_count': 1, 'invalid_count': 0, 'order_count': 1,
                        'valid_rows': [{'row_number': 2, 'order_ref': 'REF', 'customer_name': 'مریم', 'customer_phone': '09120000001', 'product_sku': 'DEMO'}], 'invalid_rows': []}
            else:
                data = {'category': 'validation', 'error': 'ستون‌های فایل معتبر نیست'}
                status = 400
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data))

    page.route('**/api/**', mock_api)
    page.goto('http://127.0.0.1:5055', wait_until='networkidle')
    page.locator('#customer-query').fill('مریم')
    page.locator('#customer-search-form button').click()
    page.locator('#customer-results').get_by_role('button', name='انتخاب', exact=True).click()
    page.locator('#product-results').get_by_role('button', name='افزودن').click()
    page.locator('#review-purchase').click()
    page.locator('#order-status').select_option('on-hold')
    page.locator('#submit-order').click()
    page.locator('#confirm-accept').click()
    expect(page.locator('#pending-order')).to_be_visible()
    expect(page.locator('#global-message')).to_contain_text('ارتباط')
    assert page.locator('#customer-card').evaluate('(el) => el.inert')
    page.reload(wait_until='networkidle')
    expect(page.locator('#pending-order')).to_be_visible()
    page.locator('#retry-order').click()
    expect(page.locator('#order-result')).to_contain_text('دوباره ساخته نشد')
    assert len(requests) == 2 and requests[0] == requests[1], 'Retry changed the request identity/payload'
    expect(page.locator('#pending-order')).to_be_hidden()

    page.locator('#tab-import').click()
    for attempt in range(2):
        page.locator('#excel-file').set_input_files('demo/afrand-demo.xlsx')
        page.locator('#import-form button').click()
        if attempt == 0:
            expect(page.locator('#import-preview')).to_be_visible()
            page.locator('#import-status').select_option('on-hold')
            expect(page.locator('#commit-import')).to_be_enabled()
        else:
            expect(page.locator('#global-message')).to_contain_text('ستون‌های فایل')
            expect(page.locator('#import-preview')).to_be_hidden()
            expect(page.locator('#commit-import')).to_be_disabled()

    # A server that never answers must leave the search recoverable.
    page.locator('#tab-manual').click()
    page.clock.install()
    page.evaluate("""() => {
      window.originalFetch = window.fetch;
      window.fetch = (url, options) => String(url).startsWith('/api/customers?')
        ? new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))
        : window.originalFetch(url, options);
    }""")
    page.locator('#customer-query').fill('09120000001')
    page.locator('#customer-search-form button').click()
    expect(page.locator('#customer-search-form')).to_have_attribute('aria-busy', 'true')
    expect(page.locator('#request-progress')).to_be_visible()
    page.clock.fast_forward(12001)
    expect(page.locator('#customer-results')).to_contain_text('جست‌وجو انجام نشد')
    expect(page.locator('#global-message')).to_contain_text('مهلت بررسی')
    expect(page.locator('#customer-search-form button')).to_have_text('جست‌وجو')
    expect(page.locator('#customer-search-form')).to_have_attribute('aria-busy', 'false')
    expect(page.locator('#request-progress')).to_be_hidden()
    expect(page.locator('#customer-search-form button')).not_to_have_attribute('aria-busy', 'true')
    expect(page.locator('#connection-state')).to_contain_text('اتصال API ناموفق')
    expect(page.locator('#save-customer-button')).to_be_disabled()
    page.evaluate('window.fetch = window.originalFetch')
    page.locator('#refresh-connection').click()
    expect(page.locator('#connection-state')).to_contain_text('ثبت فعال')
    page.locator('#customer-search-form button').click()
    expect(page.locator('#customer-results')).to_contain_text('مریم')
    assert not errors, errors
    browser.close()
print('PASS: request recovery after reload; stale import blocked; bounded customer search timeout and successful retry; no real API writes.')
