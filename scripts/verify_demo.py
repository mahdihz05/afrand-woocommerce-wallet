"""Opt-in browser acceptance test; creates marked synthetic staging orders."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright, expect
from run import app
from demo_access import local_test_client, authorize_local_browser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-staging', action='store_true', help='Allow synthetic writes on configured staging')
    args = parser.parse_args()
    if not args.live_staging:
        raise SystemExit('Use --live-staging to explicitly enable this acceptance test.')
    status = local_test_client(app).get('/api/status').get_json()
    if not status['connected'] or not status['writes_enabled']:
        raise SystemExit('Configured staging is not connected or writable.')
    output = Path('instance/verification')
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    woo = app.extensions['woo']
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1366, 'height': 900})
        authorize_local_browser(app, page)
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.on('dialog', lambda dialog: dialog.accept())
        page.goto('http://127.0.0.1:5055', wait_until='networkidle')
        expect(page.locator('#connection-state')).to_contain_text('ثبت فعال', timeout=30000)
        page.screenshot(path=str(output / 'desktop.png'), full_page=True)

        page.locator('#customer-query').fill('09120000001')
        page.locator('#customer-search-form button').click()
        page.wait_for_function("document.querySelector('#customer-results .result-option') || !document.querySelector('#new-customer-panel').hidden")
        if page.locator('#customer-results .result-option').count():
            page.locator('#customer-results .result-option').first.get_by_role('button', name='انتخاب', exact=True).click()
        else:
            form = page.locator('#new-customer-form')
            form.locator('[name=first_name]').fill('مریم')
            form.locator('[name=last_name]').fill('آزمایشی افرند')
            form.locator('[name=phone]').fill('09120000001')
            form.locator('[name=email]').fill('afrand.demo.maryam@example.test')
            form.locator('button[type=submit]').click()
        page.locator('#product-query').fill('AFRAND-DEMO-001')
        with page.expect_response('**/api/products?search=AFRAND-DEMO-001', timeout=60000):
            page.locator('#product-search').click()
        expect(page.locator('#product-results')).to_contain_text('دفتر یادداشت', timeout=30000)
        page.locator('#product-results').get_by_role('button', name='افزودن').first.click()
        page.locator('#review-purchase').click()
        page.locator('#order-status').select_option('on-hold')
        with page.expect_response(lambda r: r.url.endswith('/api/orders') and r.request.method == 'POST', timeout=60000) as response:
            page.locator('#submit-order').click()
            page.locator('#confirm-accept').click()
        manual = response.value.json()
        assert response.value.ok, manual
        expect(page.locator('#order-result')).to_contain_text(str(manual['order_id']))
        page.locator('#order-result button').click()
        expect(page.locator('#customer-orders')).to_contain_text(str(manual['order_id']), timeout=30000)
        expect(page.locator('#customer-orders')).to_contain_text('حضوری')
        page.screenshot(path=str(output / 'manual-success.png'), full_page=True)

        page.locator('#tab-import').click()
        page.locator('#excel-file').set_input_files('demo/afrand-demo.xlsx')
        with page.expect_response('**/api/import/preview', timeout=60000) as response:
            page.locator('#import-form button').click()
        preview = response.value.json()
        assert response.value.ok, preview
        assert preview['valid_count'] == 3 and preview['invalid_count'] == 3, preview
        assert preview['order_count'] == 2, preview
        page.screenshot(path=str(output / 'import-preview.png'), full_page=True)
        page.locator('#import-status').select_option('on-hold')
        with page.expect_response('**/api/import/commit', timeout=60000) as response:
            page.locator('#commit-import').click()
            page.locator('#confirm-accept').click()
        imported = response.value.json()
        assert imported['needs_review'] == 0, imported
        assert imported['created'] + imported['replayed'] == 2, imported
        expect(page.locator('#import-result')).to_be_visible()
        with page.expect_download() as download:
            page.locator('#import-result').get_by_role('button', name='دریافت گزارش ثبت').click()
        download.value.save_as(str(output / 'import-results.tsv'))
        page.screenshot(path=str(output / 'import-success.png'), full_page=True)

        page.locator('#excel-file').set_input_files('demo/afrand-demo.xlsx')
        with page.expect_response('**/api/import/preview', timeout=60000):
            page.locator('#import-form button').click()
        page.locator('#import-status').select_option('on-hold')
        with page.expect_response('**/api/import/commit', timeout=60000) as response:
            page.locator('#commit-import').click()
            page.locator('#confirm-accept').click()
        repeated = response.value.json()
        assert repeated['created'] == 0 and repeated['replayed'] == 2, repeated

        verified = []
        for result in [manual, *imported['results']]:
            order = woo.request('GET', f"orders/{result['order_id']}")
            assert order['customer_id'] == result['customer_id']
            assert order['status'] == 'on-hold' and not order['date_paid']
            assert any(m['key'] == 'afrand_order_source' and m['value'] == 'in_person' for m in order['meta_data'])
            history = woo.get_customer_orders(result['customer_id'])
            assert any(o['id'] == order['id'] and o['source'] == 'in_person' for o in history)
            verified.append({'order_id': order['id'], 'customer_id': order['customer_id'], 'total': order['total'], 'currency': order['currency'], 'source': 'in_person'})

        page.set_viewport_size({'width': 390, 'height': 844})
        page.goto('http://127.0.0.1:5055', wait_until='networkidle')
        page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
        assert not errors, errors
        browser.close()
    report = {'verified_orders': verified, 'repeat_import': repeated, 'browser_errors': errors, 'preview': {'valid_rows': 3, 'invalid_rows': 3, 'orders': 2}}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
