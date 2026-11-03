"""Bounded acceptance against the running preview and native staging wallets.

Requires explicit opt-in. Creates two marked test users and at most 278 IRT.
Receipts are checkpointed: do not retry a failed run blindly.
"""
import argparse
import json
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook
from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-staging', action='store_true')
    parser.add_argument('--resume-run', help='Resume an interrupted run after verifying its exact 80 IRT balance.')
    args = parser.parse_args()
    if not args.live_staging:
        parser.error('Explicit --live-staging required; at most 278 IRT on two test users.')
    if not os.environ.get('PANEL_TEST_PASSWORD'):
        parser.error('Set PANEL_TEST_PASSWORD to the preview panel password before running.')
    from run import app
    woo = app.extensions['woo']
    assert woo.base_url == 'https://www.afrandacademy.ir' and woo.host_matches_staging
    token = uuid.UUID(args.resume_run).hex if args.resume_run else uuid.uuid4().hex
    out = Path('instance/verification/faraz-browser') / token
    out.mkdir(parents=True, exist_ok=bool(args.resume_run))
    report = {'run': token, 'base': 'http://127.0.0.1:5056', 'checks': [], 'customers': [], 'errors': []}

    if args.resume_run:
        report = json.loads((out/'report.json').read_text('utf8'))

    def save():
        (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf8')

    def check(name, **evidence):
        report['checks'].append({'name': name, **evidence})
        save()
        print('PASS ' + name, flush=True)

    phones = ['0999'+str((int(token[:10], 16)+i) % 10000000).zfill(7) for i in (0, 1)]
    for phone in ([] if args.resume_run else phones):
        assert not woo.request('GET', 'afrand-faraz-wallet/customers', params={'q': phone})['customers']
    save()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(60000)
        expect.set_options(timeout=60000)
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        base = report['base']
        page.goto(base, wait_until='networkidle')
        if page.locator('#username').count():
            page.locator('#username').fill(os.environ.get('PANEL_TEST_USERNAME', 'admin'))
            page.locator('#password').fill(os.environ['PANEL_TEST_PASSWORD'])
            page.locator('button[type=submit]').click()
        expect(page.locator('#connection-state')).to_contain_text('واریز فعال')
        status = page.request.get(base+'/api/status').json()
        assert status['wallet'] == 'FarazSMS' and status['currency'] == 'IRT', status
        check('running_preview_faraz_currency')
        if not args.resume_run:
            page.locator('#show-new-customer').click()
            for key, value in {'first_name': 'آزمایشی تحویل', 'last_name': token[:8], 'phone': phones[0], 'company': 'شرکت آزمایش اصلی'}.items():
                page.locator(f'#new-customer-form [name={key}]').fill(value)
            page.locator('#save-customer-button').click()
            page.locator('#confirm-accept').click()
            expect(page.locator('#wallet-balance')).to_contain_text('۰ تومان')
            customers = page.request.get(base+'/api/customers', params={'q': phones[0]}).json()['customers']
            cid = customers[0]['id']
            report['customers'].append({'id': cid, 'phone': phones[0]})
            check('new_customer_via_browser', customer_id=cid)
            page.locator('#credit-amount').fill('۱٬۰۰۱')
            expect(page.locator('#credit-amount')).to_have_value('1,001')
            page.locator('#cashback-percent').fill('7.5')
            page.locator('#credit-note').fill('آزمایش تحویل '+token[:8])
            page.locator('#submit-credit').click()
            page.locator('#confirm-cancel').click()
            assert Decimal(woo.request('GET', f'afrand-faraz-wallet/customers/{cid}')['wallet']['balance']) == 0
            check('manual_cancel_no_credit')
            with page.expect_response('**/api/wallet/credits') as response:
                page.locator('#submit-credit').click()
                page.locator('#confirm-accept').click()
            result = response.value.json()
            assert response.value.ok, result
            expect(page.locator('#wallet-balance')).to_contain_text('۷۵ تومان')
            payload = response.value.request.post_data_json
            replay = page.request.post(base+'/api/wallet/credits', data=payload)
            assert replay.ok and replay.json()['replayed'], replay.text()
            conflict = page.request.post(base+'/api/wallet/credits', data={**payload, 'purchase_amount': '2000'})
            assert conflict.status == 409, conflict.text()
            check('manual_persian_grouped_amount_fraction_floor_replay_conflict', amount=75, request_id=payload['request_id'])
            for values in ({'purchase_amount': '0'}, {'purchase_amount': '-100'}, {'cashback_percent': '101'}, {'confirm': False}):
                r = page.request.post(base+'/api/wallet/credits', data={**payload, 'request_id': uuid.uuid4().hex, **values})
                assert r.status in (400, 422), (values, r.status, r.text())
            check('manual_invalid_amount_percent_confirmation_rejected')
        else:
            cid = report['customers'][0]['id']
            prior = woo.request('GET', f'afrand-faraz-wallet/customers/{cid}')['wallet']
            assert Decimal(prior['balance']) == 80 and len(prior['transactions']) == 2, prior
            page.locator('#customer-query').fill(phones[0])
            page.locator('#customer-search-form button').click()
            page.locator('#customer-results button').click()
            expect(page.locator('#wallet-balance')).to_contain_text('80'.translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')))
        page.locator('#credit-amount').fill('100')
        page.locator('#cashback-percent').fill('5')
        lost = []
        def lose(route):
            result = route.fetch()
            assert result.ok, result.text()
            lost.append(route.request.post_data_json['request_id'])
            report['lost_request_id'] = lost[-1]
            save()
            route.abort()
        page.route('**/api/wallet/credits', lose)
        page.locator('#submit-credit').click()
        page.locator('#confirm-accept').click()
        expect(page.locator('#global-message')).to_contain_text('\u0627\u0631\u062a\u0628\u0627\u0637')
        expect(page.locator('#retry-credit')).to_be_enabled()
        page.unroute('**/api/wallet/credits')
        page.reload(wait_until='networkidle')
        page.locator('#retry-credit').click()
        expect(page.locator('#pending-credit')).to_be_hidden()
        assert len(lost) == 1
        assert Decimal(woo.request('GET', f'afrand-faraz-wallet/customers/{cid}')['wallet']['balance']) == (85 if args.resume_run else 80)
        check('real_manual_credit_response_lost_reload_reconciles_once', request_id=lost[0], balance=85 if args.resume_run else 80)

        def preview(mode, rows, filename):
            book = Workbook()
            book.active.append(['نام', 'شماره', 'شرکت', 'مبلغ خرید', 'شماره سند'])
            for row in rows:
                book.active.append(row)
            path = out/(filename+'.xlsx')
            book.save(path)
            page.goto(base, wait_until='networkidle')
            expect(page.locator('#connection-state')).to_contain_text('واریز فعال')
            page.locator('#tab-import').click()
            page.locator('#amount-mode').select_option(mode)
            page.locator('#fixed-amount' if mode == 'fixed' else '#import-cashback-percent').fill('37' if mode == 'fixed' else '10')
            page.locator('#excel-file').set_input_files(str(path.resolve()))
            with page.expect_response('**/api/import/preview') as response:
                page.locator('#preview-file').click()
            data = response.value.json()
            assert response.value.ok, data
            report['last_draft'] = data.get('draft_id')
            save()
            return data

        def commit(preview, screenshot):
            page.locator('#commit-import').click()
            page.locator('#confirm-accept').click()
            expect(page.locator('#import-result')).to_be_visible()
            expect(page.locator('#import-result')).to_contain_text('نیازمند بررسی: 0')
            result = page.request.get(base+'/api/import/'+preview['draft_id']).json()
            assert result['done'], result
            page.screenshot(path=str(out/(screenshot+'.png')), full_page=True)
            return result

        fixed = [['آزمایشی تحویل '+token[:8], phones[i], 'شرکت فایل', None, None] for i in (0, 0, 1)]
        fixed += [['موبایل نامعتبر', '123', '', None, None]]
        for run in range(2):
            data = preview('fixed', fixed, 'fixed')
            assert (data['valid_count'], data['skipped_count'], data['invalid_count']) == (2, 1, 1), data
            expect(page.locator('#preview-rows tr')).to_have_count(4)
            result = commit(data, 'fixed-'+str(run))
            assert len(result['results']) == 2, result
            check('fixed_upload_'+str(run+1), valid=2, skipped=1, invalid=1, draft_id=data['draft_id'])
        customer2 = page.request.get(base+'/api/customers', params={'q': phones[1]}).json()['customers'][0]
        report['customers'].append({'id': customer2['id'], 'phone': phones[1]})
        rows = [['آزمایشی تحویل', phones[0], 'شرکت فایل', 100, doc] for doc in
                (f'1405/08/12-{token}', f'2026-11-03-{token}', f'{token}-خرید۳', f'{token}-4', f'{token}-5')]
        invalid = [['سند خالی', phones[0], '', 100, ''], ['مبلغ منفی', phones[0], '', -100, token+'-bad']]
        data = preview('excel', rows+invalid, 'cashback')
        assert (data['valid_count'], data['invalid_count']) == (5, 2), data
        expect(page.locator('#preview-rows tr')).to_have_count(7)
        commit(data, 'cashback')
        check('cashback_five_distinct_purchases_date_and_text_docs', valid=5, invalid=2, draft_id=data['draft_id'])
        data = preview('excel', list(reversed(rows)), 'cashback-replay')
        commit(data, 'cashback-replay')
        expect(page.locator('#import-result')).to_contain_text('واریز تازه: 0')
        check('cashback_reordered_file_replay_no_new_credit', draft_id=data['draft_id'])
        changed = [list(rows[0])]
        changed[0][3] = 200
        data = preview('excel', changed, 'cashback-conflict')
        # A changed payload for an existing document must never become a new credit.
        if data['valid_count']:
            page.locator('#commit-import').click()
            page.locator('#confirm-accept').click()
            expect(page.locator('#import-result')).to_be_visible()
            result = page.request.get(base+'/api/import/'+data['draft_id']).json()
            report['document_conflict_result'] = result
            assert any(row.get('state') == 'needs_review' for row in result['results']), result
        else:
            report['document_conflict_preview'] = data
        check('cashback_document_payload_conflict_processed', draft_id=data['draft_id'])
        wallets = []
        for customer, expected, count in zip(report['customers'], (209 if args.resume_run else 204, 74), (10 if args.resume_run else 9, 2)):
            wid = customer['id']
            detail = woo.request('GET', f'afrand-faraz-wallet/customers/{wid}')
            wallet = detail['wallet']
            assert Decimal(wallet['balance']) == expected, wallet
            assert len(wallet['transactions']) == count, wallet
            assert sum(Decimal(t['amount']) for t in wallet['transactions']) == expected
            assert all(Decimal(t['used_amount']) == 0 and t['status'] == 'active' for t in wallet['transactions'])
            assert Decimal(woo.request('GET', f'afrand-wallet/customers/{wid}')['wallet']['balance']) == 0
            if wid == cid:
                assert detail['customer']['company'] == 'شرکت آزمایش اصلی', detail
            wallets.append({'customer_id': wid, **wallet})
        report['wallets'] = wallets
        report['credits_total_irt'] = 283 if args.resume_run else 278
        assert not report['errors'], report['errors']
        check('native_wordpress_exact_balances_ledger_companies_terawallet_unchanged', balances=[209 if args.resume_run else 204, 74], transactions=12 if args.resume_run else 11)
        browser.close()
    print(str(out/'report.json'), flush=True)


if __name__ == '__main__':
    main()
