"""OS-process crash recovery of the panel ledger; no remote wallet access."""
import multiprocessing
import tempfile
import unittest
from pathlib import Path

from panel.ledger import Ledger


def hold_document(path, ready):
    ledger=Ledger(path)
    with ledger.wallet_document_lock('invoice'):
        ledger.bind_wallet_document('invoice','fingerprint')
        ledger.save_wallet_document('invoice',payload={'request_id':'protected'})
        ledger.save_wallet_import({'draft_id':'saved','valid_count':1,'valid_rows':[{'reference':'invoice'}]})
        ledger.confirm_wallet_import('saved')
        ledger.save_wallet_result('saved',0,{'reference':'invoice','state':'needs_review'})
        ready.put(True)
        # Parent terminates this process while it owns the OS lock.
        multiprocessing.Event().wait(30)


class LedgerProcessTests(unittest.TestCase):
    def test_process_termination_releases_lock_preserves_uncertain_payload_and_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'ledger.sqlite3')
            context=multiprocessing.get_context('spawn'); ready=context.Queue()
            worker=context.Process(target=hold_document,args=(path,ready))
            worker.start()
            try:
                self.assertTrue(ready.get(timeout=15))
            finally:
                worker.terminate(); worker.join(10)
                ready.close(); ready.join_thread()
            self.assertFalse(worker.is_alive())
            reopened=Ledger(path)
            with reopened.wallet_document_lock('invoice'):
                saved=reopened.bind_wallet_document('invoice','fingerprint')
                self.assertEqual(saved['payload_json'],{'request_id':'protected'})
                self.assertIsNone(saved['result_json'])
            status=reopened.wallet_import('saved')
            self.assertTrue(status['confirmed']); self.assertFalse(status['done'])
            self.assertEqual(status['next_offset'],0)
            self.assertEqual(status['results'][0]['state'],'needs_review')
