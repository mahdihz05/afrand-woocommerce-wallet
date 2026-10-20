"""Final staging acceptance of all panel sections; synthetic identities only."""

import argparse
import io
import json
import sys
import time
import uuid
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook
from playwright.sync_api import sync_playwright, expect
from run import app
from demo_access import local_test_client, authorize_local_browser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-staging', action='store_true')
    args = parser.parse_args()
    if not args.live_staging:
        raise SystemExit('Explicit --live-staging required; this creates synthetic customers/orders.')
    woo = app.extensions['woo']
    status = local_test_client(app).get('/api/status').get_json()
    if not status['connected'] or not status['writes_enabled']:
        raise SystemExit('Read-only connection gate failed. No test writes started.')
    run_id = uuid.uuid4().hex[:10]
    output = Path('instance/verification') / run_id
    output.mkdir(parents=True, exist_ok=True)
    report = {'run_id': run_id, 'checks': [], 'customer_ids': [], 'order_ids': [], 'browser_errors': []}
    started = time.monotonic()

    def passed(name, **details):
        report['checks'].append({'name': name, 'passed': True, **details})
        report['elapsed_seconds'] = round(time.monotonic() - started, 1)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('PASS:', name, flush=True)

    phones = ['091200' + f'{(int(run_id, 16) + offset) % 100000:05d}' for offset in range(4)]
    email = f'afrand.qa.{run_id}@example.test'
    for phone in phones:
        found, complete = woo.search_customers_by_phone(phone)
        assert complete and not found, 'Synthetic identity collided; stop before writes.'
    products = [woo.get_products_by_sku(sku)[0] for sku in ('AFRAND-DEMO-001', 'AFRAND-DEMO-002')]
    passed('connection_and_synthetic_identity_gate')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1366, 'height': 900})
        authorize_local_browser(app, page)
        page.on('pageerror', lambda error: report['browser_errors'].append(str(error)))
        page.on('dialog', lambda dialog: dialog.accept())
        page.goto('http://127.0.0.1:5055', wait_until='networkidle')
        expect(page.locator('#connection-state')).to_contain_text('ثبت فعال', timeout=20000)

        def search(query):
            page.locator('#customer-query').fill(query)
            with page.expect_response(lambda r: '/api/customers?' in r.url, timeout=20000) as pending:
                page.locator('#customer-search-form button').click()
            response = pending.value
            assert response.ok, response.json()
            expect(page.locator('#customer-search-form')).to_have_attribute('aria-busy', 'false')
            return response.json()

        search(phones[0])
        expect(page.locator('#new-customer-panel')).to_be_visible()
        form = page.locator('#new-customer-form')
        for field, value in {'first_name': 'آزمایشی', 'last_name': f'افرند {run_id}', 'phone': phones[0], 'email': email}.items():
            form.locator(f'[name={field}]').fill(value)
        with page.expect_response(lambda r: r.url.endswith('/api/customers') and r.request.method == 'POST', timeout=90000) as pending:
            page.locator('#save-customer-button').click()
            page.locator('#confirm-accept').click()
        created = pending.value.json()
        assert pending.value.ok, created
        customer_id = created['customer']['id']
        report['customer_ids'].append(customer_id)
        expect(page.locator('#customer-detail-name')).to_contain_text(run_id, timeout=20000)
        original = woo.request('GET', f'customers/{customer_id}')
        assert original['email'] == email
        passed('standalone_customer_creation_and_detail_readback', customer_id=customer_id)

        # Same normalized phone must replay instead of creating a second customer.
        replay = local_test_client(app).post('/api/customers', json={**created['customer'], 'confirm': True})
        assert replay.status_code == 200 and replay.get_json()['replayed']
        assert replay.get_json()['customer']['id'] == customer_id
        passed('duplicate_customer_prevention')

        page.locator('.edit-details summary').click()
        edit = page.locator('#customer-edit-form')
        edit.locator('[name=first_name]').fill('آزمایشی ویرایش‌شده')
        edit.locator('[name=phone]').fill(phones[1])
        with page.expect_response(lambda r: r.url.endswith(f'/api/customers/{customer_id}') and r.request.method == 'PUT', timeout=90000) as pending:
            page.locator('#update-customer-button').click()
            page.locator('#confirm-accept').click()
        updated = pending.value.json()
        assert pending.value.ok, updated
        expect(page.locator('#customer-detail-name')).to_contain_text('ویرایش‌شده', timeout=20000)
        raw = woo.request('GET', f'customers/{customer_id}')
        assert raw['billing'] == original['billing'] and raw['shipping'] == original['shipping']
        assert raw['first_name'] == 'آزمایشی ویرایش‌شده'
        assert woo.get_customer(customer_id)['phone'] == phones[1]
        passed('customer_edit_and_address_preservation')

        for term in (phones[1], phones[1].translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')), email, run_id):
            results = search(term)
            assert customer_id in [c['id'] for c in results['customers']]
        page.locator('#customer-results').get_by_role('button', name='انتخاب', exact=True).first.click()
        passed('customer_search_mobile_persian_digits_email_and_name')

        page.locator('#product-query').fill('AFRAND-DEMO-001')
        with page.expect_response('**/api/products?search=AFRAND-DEMO-001', timeout=20000) as pending:
            page.locator('#product-search').click()
        catalog = pending.value.json()['products']
        assert [product['id'] for product in catalog] == [products[0]['id']]
        assert Decimal(catalog[0]['price']) == Decimal(products[0]['price'])
        page.locator('#product-results').get_by_role('button', name='افزودن').click()
        page.locator('#cart-lines input').fill('2')
        page.locator('#cart-lines input').dispatch_event('change')
        page.locator('#review-purchase').click()
        page.locator('#order-status').select_option('on-hold')
        passed('exact_sku_catalog_price_and_cart_quantity')

        # Complete the real backend write, then lose only its browser response.
        dropped = {}
        def lose_response(route):
            dropped['payload'] = route.request.post_data_json
            response = route.fetch(timeout=90000)
            dropped['result'] = response.json()
            assert response.ok, dropped['result']
            report['order_ids'].append(dropped['result']['order_id'])
            route.abort('failed')
        page.route('**/api/orders', lose_response, times=1)
        page.locator('#submit-order').click()
        page.locator('#confirm-accept').click()
        expect(page.locator('#global-message')).to_contain_text('ارتباط', timeout=90000)
        expect(page.locator('#pending-order')).to_be_visible()
        page.reload(wait_until='networkidle')
        expect(page.locator('#connection-state')).to_contain_text('ثبت فعال', timeout=20000)
        with page.expect_response(lambda r: r.url.endswith('/api/orders') and r.request.method == 'POST', timeout=90000) as pending:
            page.locator('#retry-order').click()
        recovered = pending.value.json()
        assert recovered['replayed'] and recovered['order_id'] == dropped['result']['order_id']
        assert pending.value.request.post_data_json == dropped['payload']
        order = woo.request('GET', f"orders/{recovered['order_id']}")
        assert order['customer_id'] == customer_id and order['line_items'][0]['quantity'] == 2
        assert Decimal(order['total']) == Decimal(products[0]['price']) * 2
        page.locator('#order-result button').click()
        expect(page.locator('#customer-orders')).to_contain_text(str(order['id']), timeout=20000)
        expect(page.locator('#customer-orders')).to_contain_text('حضوری')
        page.screenshot(path=str(output / 'manual-recovered.png'), full_page=True)
        passed('manual_order_response_loss_reload_recovery_and_customer_history', order_id=order['id'])

        # New customer and order created together, rather than only standalone.
        search(phones[2])
        new_email = f'afrand.qa.inline.{run_id}@example.test'
        form = page.locator('#new-customer-form')
        for field, value in {'first_name': 'مشتری آزمایشی همراه سفارش', 'last_name': run_id, 'phone': phones[2], 'email': new_email}.items():
            form.locator(f'[name={field}]').fill(value)
        form.locator('button[type=submit]').click()
        expect(page.locator('#product-results')).to_contain_text('دفتر', timeout=20000)
        page.locator('#product-results').get_by_role('button', name='افزودن').first.click()
        page.locator('#review-purchase').click()
        page.locator('#order-status').select_option('pending')
        with page.expect_response(lambda r: r.url.endswith('/api/orders') and r.request.method == 'POST', timeout=90000) as pending:
            page.locator('#submit-order').click()
            page.locator('#confirm-accept').click()
        inline = pending.value.json()
        assert pending.value.ok, inline
        report['order_ids'].append(inline['order_id'])
        report['customer_ids'].append(inline['customer_id'])
        assert woo.get_customer(inline['customer_id'])['email'] == new_email
        passed('new_customer_and_order_together', customer_id=inline['customer_id'], order_id=inline['order_id'])

        # Unique references exercise actual fresh imports, not just old replays.
        template = local_test_client(app).get('/template.xlsx')
        assert template.status_code == 200
        workbook = Workbook()
        sheet = workbook.active
        # Explicit legacy-format fixtures also verify backward compatibility.
        sheet.append(['order_ref', 'customer_name', 'customer_phone', 'customer_email', 'product_sku', 'quantity'])
        for row in [
            [f'QA-{run_id}-A', 'آزمایشی ویرایش‌شده', phones[1], email, products[0]['sku'], 1],
            [f'QA-{run_id}-A', 'آزمایشی ویرایش‌شده', phones[1], email, products[1]['sku'], 2],
            [f'QA-{run_id}-B', 'آزمایشی اکسل', phones[3], f'afrand.qa.import.{run_id}@example.test', products[0]['sku'], 1],
            [f'QA-{run_id}-C', 'آزمایشی خطا', phones[3], '', 'SKU-NOT-FOUND', 1],
            [f'QA-{run_id}-D', 'آزمایشی خطا', phones[3], '', products[0]['sku'], 1.5],
            [f'QA-{run_id}-E', 'آزمایشی خطا', '123', '', products[0]['sku'], 1],
        ]:
            sheet.append(row)
        spreadsheet = output / 'acceptance.xlsx'
        workbook.save(spreadsheet)
        page.locator('#tab-import').click()

        def import_file():
            page.locator('#excel-file').set_input_files(str(spreadsheet))
            with page.expect_response('**/api/import/preview', timeout=90000) as pending:
                page.locator('#import-form button').click()
            preview = pending.value.json()
            assert pending.value.ok, preview
            assert (preview['valid_count'], preview['invalid_count'], preview['order_count']) == (3, 3, 2), preview
            page.locator('#import-status').select_option('on-hold')
            with page.expect_response('**/api/import/commit', timeout=180000) as pending:
                page.locator('#commit-import').click()
                page.locator('#confirm-accept').click()
            assert pending.value.ok, pending.value.json()
            return pending.value.json()

        imported = import_file()
        assert imported['created'] == 2 and imported['needs_review'] == 0 and imported['replayed'] == 0, imported
        for item in imported['results']:
            report['order_ids'].append(item['order_id'])
            if item['customer_id'] not in report['customer_ids']:
                report['customer_ids'].append(item['customer_id'])
        expect(page.locator('#import-result')).to_contain_text('ثبت نشد')
        with page.expect_download() as pending:
            page.locator('#import-result').get_by_role('button', name='دریافت گزارش ثبت').click()
        pending.value.save_as(str(output / 'results.tsv'))
        text = (output / 'results.tsv').read_text(encoding='utf-8-sig')
        assert 'invalid-row-5' in text and 'invalid-row-6' in text and 'invalid-row-7' in text
        page.screenshot(path=str(output / 'import-report.png'), full_page=True)
        passed('fresh_excel_import_grouping_partial_validation_and_download', orders=[item['order_id'] for item in imported['results']])

        repeated = import_file()
        assert (repeated['created'], repeated['replayed'], repeated['needs_review']) == (0, 2, 0), repeated
        assert {item['order_id'] for item in repeated['results']} == {item['order_id'] for item in imported['results']}
        passed('repeat_excel_import_creates_zero_duplicate_orders')

        for order_id in report['order_ids']:
            order = woo.request('GET', f'orders/{order_id}')
            assert order['status'] in {'pending', 'on-hold'} and not order['date_paid']
            assert any(m['key'] == 'afrand_order_source' and m['value'] == 'in_person' for m in order['meta_data'])
            history = woo.get_customer_orders(order['customer_id'])
            assert any(o['id'] == order_id and o['source'] == 'in_person' for o in history)
        assert len(woo.get_customer_orders(customer_id)) == 2, 'Duplicate manual or imported order appeared'
        passed('independent_readback_of_all_orders_and_unpaid_in_person_history')

        page.set_viewport_size({'width': 390, 'height': 844})
        page.goto('http://127.0.0.1:5055', wait_until='networkidle')
        expect(page.locator('#connection-state')).to_contain_text('ثبت فعال', timeout=20000)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        assert not report['browser_errors'], report['browser_errors']
        passed('mobile_layout_and_zero_browser_errors')
        browser.close()
    (Path('instance/verification') / 'latest-final-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Final report:', output / 'report.json', flush=True)


if __name__ == '__main__':
    main()
