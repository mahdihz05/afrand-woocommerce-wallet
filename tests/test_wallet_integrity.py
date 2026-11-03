"""Independent amount reconciliation and a real panel-process crash.

The remote wallet is synthetic and survives the panel worker process.
"""
import concurrent.futures
import multiprocessing
from multiprocessing.managers import BaseManager
import os
from pathlib import Path
import tempfile
import unittest

from panel.app import create_app
import test_wallet
from test_wallet import FakeWallet, workbook


class RemoteWallet(FakeWallet):
    def audit(self):
        return {'transactions': self.transactions, 'calls': self.calls}


class WalletManager(BaseManager):
    pass


WalletManager.register('wallet', RemoteWallet)


class WalletProxy:
    configured = True
    host_matches_staging = True
    base_url = 'https://www.wprokad.ir'

    def __init__(self, remote, crash=False):
        self.remote = remote
        self.crash = crash

    def request(self, method, path, params=None, payload=None):
        result = self.remote.request(method, path, params, payload)
        if self.crash and method == 'POST' and path == 'afrand-faraz-wallet/credits':
            # Credit exists remotely, but no panel result or HTTP response exists.
            os._exit(73)
        return result


def crash_after_credit(config, remote, draft):
    client = create_app(config, woo_client=WalletProxy(remote, crash=True)).test_client()
    client.post('/api/import/commit', json={'draft_id': draft, 'confirm': True})


def concurrent_commit(config, remote, draft, ready, gate, results):
    client = create_app(config, woo_client=WalletProxy(remote)).test_client()
    ready.put(True)
    if not gate.wait(15):
        raise RuntimeError('start gate timed out')
    response = client.post('/api/import/commit', json={'draft_id': draft, 'confirm': True})
    results.put((response.status_code, response.json))


class IntegrityTests(unittest.TestCase):
    setUp = test_wallet.WalletTests.setUp
    tearDown = test_wallet.WalletTests.tearDown

    def test_two_processes_different_imports_same_document_credit_once(self):
        context = multiprocessing.get_context('spawn')
        with WalletManager(ctx=context) as manager:
            remote = manager.wallet()
            client = create_app(self.config, woo_client=WalletProxy(remote)).test_client()
            drafts = [client.post('/api/import/preview', data={'file': (
                workbook([['PROCESS-SHARED', 'Existing', '09121234567', 1001, 'IRT']]),
                f'user-{i}.xlsx')}).json['draft_id'] for i in range(2)]
            ready, results, gate = context.Queue(), context.Queue(), context.Event()
            workers = [context.Process(target=concurrent_commit,
                       args=(self.config, remote, draft, ready, gate, results)) for draft in drafts]
            for worker in workers:
                worker.start()
            try:
                for _ in workers:
                    self.assertTrue(ready.get(timeout=20))
                gate.set()
                responses = [results.get(timeout=20) for _ in workers]
                self.assertEqual([r[0] for r in responses], [200, 200])
                self.assertEqual(sum(r[1]['created'] for r in responses), 1)
                self.assertEqual(sum(r[1]['replayed'] for r in responses), 1)
                self.assertEqual(sum(r[1]['needs_review'] for r in responses), 0)
            finally:
                gate.set()
                for worker in workers:
                    worker.join(5)
                    if worker.is_alive():
                        worker.terminate(); worker.join(5)
                ready.close(); ready.join_thread()
                results.close(); results.join_thread()
            self.assertEqual([w.exitcode for w in workers], [0, 0])
            self.assertEqual(len(remote.audit()['transactions']), 1)
            self.assertEqual(remote.audit()['transactions'][0]['amount'], '50')
            self.assertEqual(remote.audit()['calls'].count(('POST', 'afrand-faraz-wallet/credits')), 1)

    def test_parallel_manual_retry_and_customer_conflict(self):
        other = self.client.post('/api/customers', json={
            'first_name': 'Second', 'phone': '09123334455', 'confirm': True}).json['customer']['id']
        payload = {'request_id': 'c'*32, 'customer_id': 7, 'purchase_amount': '1001',
                   'cashback_percent': '7.5', 'currency': 'IRT', 'note': 'Sale', 'confirm': True}
        def submit(_):
            return self.app.test_client().post('/api/wallet/credits', json=payload)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(8)))
        self.assertEqual([r.status_code for r in results], [200]*8)
        self.assertEqual(sum(not r.json['replayed'] for r in results), 1)
        conflict = self.client.post('/api/wallet/credits', json={**payload, 'customer_id': other})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(len(self.woo.transactions), 1)
        self.assertEqual(self.woo.transactions[0]['amount'], '75')
        self.assertEqual(self.client.get('/api/customers/7').json['wallet']['balance'], '75')
        self.assertEqual(self.client.get(f'/api/customers/{other}').json['wallet']['balance'], '0')
        new_credit = self.client.post('/api/wallet/credits', json={
            **payload, 'request_id': 'd'*32, 'customer_id': other, 'purchase_amount': '2039'})
        self.assertEqual(new_credit.status_code, 200)
        self.assertEqual(self.client.get(f'/api/customers/{other}').json['wallet']['balance'], '152')
        self.assertEqual(len(self.woo.transactions), 2)
        self.assertEqual(sum(int(t['amount']) for t in self.woo.transactions), 227)

    def test_varied_rows_reordered_file_and_independent_balances_in_both_units(self):
        for unit in ('IRT', 'IRR'):
            with self.subTest(unit=unit):
                self.woo = FakeWallet()
                self.woo.currency = unit
                self.client = create_app({**self.config, 'DATABASE': str(Path(self.temp.name)/(unit+'.db'))},
                                         woo_client=self.woo).test_client()
                rows = [[unit+'-A', 'Existing', '۰۹۱۲۱۲۳۴۵۶۷', '۱۰۰۱', unit],
                        [unit+'-B', 'New', '09123334455', 2039, unit],
                        [unit+'-C', 'Existing', '09121234567', 99, unit],
                        [unit+'-BAD', 'Invalid', '123', 100, unit]]
                before = len(self.woo.transactions)
                p = self.client.post('/api/import/preview', data={
                    'file': (workbook(rows), 'original.xlsx'), 'cashback_percent': '7.5'}).json
                self.assertEqual((p['valid_count'], p['invalid_count']), (3, 1))
                self.assertEqual(len(self.woo.transactions), before)
                for offset in (0, 2):
                    r = self.client.post('/api/import/commit', json={
                        'draft_id': p['draft_id'], 'confirm': True, 'offset': offset})
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(r.json['needs_review'], 0)
                saved = self.client.get('/api/import/'+p['draft_id']).json
                self.assertTrue(saved['done'])
                self.assertEqual(len(saved['results']), 3)
                # Explicit reference amounts, independent of preview and UI totals.
                credited = self.woo.transactions[before:]
                self.assertEqual(sorted(int(t['amount']) for t in credited), [7, 75, 152])
                self.assertEqual(sum(int(t['amount']) for t in credited), 234)
                replay = self.client.post('/api/import/preview', data={
                    'file': (workbook(list(reversed(rows))), 'renamed.xlsx'), 'cashback_percent': '7.5'}).json
                for offset in (0, 2):
                    r = self.client.post('/api/import/commit', json={
                        'draft_id': replay['draft_id'], 'confirm': True, 'offset': offset}).json
                    self.assertEqual(r['created'], 0)
                    self.assertEqual(r['needs_review'], 0)
                self.assertEqual(len(self.woo.transactions), before+3)
                self.assertEqual(len({t['transaction_id'] for t in self.woo.transactions}), 3)
                self.assertEqual(self.client.get('/api/customers/7').json['wallet']['balance'], '82')
                new_id = self.woo.customers[1]['id']
                self.assertEqual(self.client.get(f'/api/customers/{new_id}').json['wallet']['balance'], '152')

    def test_process_dies_after_remote_credit_restart_recovers_without_second_post(self):
        context = multiprocessing.get_context('spawn')
        with WalletManager(ctx=context) as manager, tempfile.TemporaryDirectory() as folder:
            remote = manager.wallet()
            config = {**self.config, 'DATABASE': str(Path(folder)/'crash.sqlite3')}
            client = create_app(config, woo_client=WalletProxy(remote)).test_client()
            p = client.post('/api/import/preview', data={
                'file': (workbook([['CRASH', 'Existing', '09121234567', 2469, 'IRT']]), 'crash.xlsx')}).json
            worker = context.Process(target=crash_after_credit, args=(config, remote, p['draft_id']))
            worker.start()
            worker.join(20)
            if worker.is_alive():
                worker.terminate(); worker.join(5)
                self.fail('crash worker timed out')
            self.assertEqual(worker.exitcode, 73)
            self.assertEqual(len(remote.audit()['transactions']), 1)
            reopened = create_app(config, woo_client=WalletProxy(remote)).test_client()
            self.assertFalse(reopened.get('/api/import/'+p['draft_id']).json['done'])
            for _ in range(2):
                recovered = reopened.post('/api/import/commit', json={'draft_id': p['draft_id'], 'confirm': True}).json
                self.assertEqual((recovered['created'], recovered['replayed'], recovered['needs_review']), (0, 1, 0))
            self.assertTrue(reopened.get('/api/import/'+p['draft_id']).json['done'])
            audit = remote.audit()
            self.assertEqual(audit['calls'].count(('POST', 'afrand-faraz-wallet/credits')), 1)
            self.assertEqual([t['amount'] for t in audit['transactions']], ['123'])
            self.assertEqual(reopened.get('/api/customers/7').json['wallet']['balance'], '123')
