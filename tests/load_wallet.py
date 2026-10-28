"""Opt-in local load evidence. FakeWallet is NOT WordPress integration.

Run: python tests/load_wallet.py
"""
import concurrent.futures
import json
import sys
import tempfile
import threading
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from panel.app import create_app
from test_wallet import FakeWallet, workbook


def run():
    started=time.monotonic(); wallet=FakeWallet(); gate=threading.Barrier(5)
    with tempfile.TemporaryDirectory() as folder:
        config={'TESTING':True,'WALLET_MODE':True,'DATABASE':str(Path(folder)/'load.sqlite3'),'WC_WRITES_ENABLED':True}
        apps=[create_app(config,woo_client=wallet) for _ in range(5)]
        wallet.lose_credit_response=True
        def worker(index):
            client=apps[index].test_client()
            # 180 shared references; 800 unique references; 20 duplicate rows.
            rows=[[f'SHARED{i}' if i<180 else f'U{index}-{i}','Customer','09121234567',100,'IRT'] for i in range(980)]
            rows += [row.copy() for row in rows[:20]]
            gate.wait()
            response=client.post('/api/import/preview',data={'file':(workbook(rows),f'user{index}.xlsx')})
            assert response.status_code==200,response.json
            p=response.json; assert (p['valid_count'],p['invalid_count'])==(960,40),p
            draft=p['draft_id']; offset=0; calls=0
            while offset<p['valid_count']:
                result=client.post('/api/import/commit',json={'draft_id':draft,'confirm':True,'offset':offset})
                assert result.status_code==200,result.json
                offset=result.json['next_offset']; calls+=1
                if calls==100: client=create_app(config,woo_client=wallet).test_client()
            # Reopen from disk. Recover an ambiguous response and verify all rows.
            client=create_app(config,woo_client=wallet).test_client()
            status=client.get('/api/import/'+draft).json
            for _ in range(3):
                if status['done']: break
                client.post('/api/import/commit',json={'draft_id':draft,'confirm':True,'offset':status['next_offset']})
                status=client.get('/api/import/'+draft).json
            assert status['done'],status
            # Entire file is explicitly re-run: must add no native credit.
            for offset in range(0,960,2):
                result=client.post('/api/import/commit',json={'draft_id':draft,'confirm':True,'offset':offset})
                assert result.status_code==200 and result.json['needs_review']==0,result.json
            return {'rows':1000,'valid':960,'invalid_duplicate_rows':40,'saved_results':len(status['results']),'batch_requests':calls}
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(worker,range(5)))
        # Conflicting information for a credited shared invoice must be visible.
        client=apps[0].test_client()
        p=client.post('/api/import/preview',data={'file':(workbook([['SHARED100','Customer','09121234567',200,'IRT']]),'conflict.xlsx')}).json
        conflict=client.post('/api/import/commit',json={'draft_id':p['draft_id'],'confirm':True}).json
        assert conflict['needs_review']==1
        expected=4160; total=sum(Decimal(t['amount']) for t in wallet.transactions)
        assert len(wallet.transactions)==expected,(len(wallet.transactions),expected)
        assert len(wallet.operations)==expected
        assert total==Decimal(expected*5),total
        report={'backend':'FakeWallet (not WordPress/TeraWallet)','workers':5,'rows':5000,'workers_results':results,
                'unique_transactions':len(wallet.transactions),'expected_unique_transactions':expected,
                'credit_total':str(total),'expected_total':str(expected*5),'conflicts_rejected':1,
                'lost_response_injected':True,'apps_recreated_during_and_after_import':True,'full_rerun':True,
                'elapsed_seconds':round(time.monotonic()-started,3),'passed':True}
        out=Path('outputs/delivery'); out.mkdir(parents=True,exist_ok=True)
        (out/'load-fake.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report))

if __name__=='__main__': run()
