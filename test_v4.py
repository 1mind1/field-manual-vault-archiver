"""V4 cache, concurrency and safety regression tests; network is mocked."""
import io
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import trex_vault_archiver as m

HTML = b'''<!doctype html><html><head><title>FM 4-25.11: First Aid | T.REX ARMS Field Manual Vault</title></head>
<body><a href="https://files.trex-arms.com/pdf/test.pdf">PDF</a>
<a href="https://files.trex-arms.com/epub/test.epub">EPUB</a>
<div>Edition</div><div>Dec 2002</div><div>Function</div><div>Medical TCCC</div></body></html>'''
URL = 'https://www.trex-arms.com/field-manual-vault/fm-4-25-11-2002-12'
PDF_URL = 'https://files.trex-arms.com/pdf/test.pdf'


def entry(url=URL):
    return m._fetch_manual_entry(url)


class FakeErrorResponse(io.BytesIO):
    status = 200
    headers = {}


class V4Tests(unittest.TestCase):
    def test_old_manifest_import_and_cross_selection_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            saved = root/'trex_manifest_selective_legacy.json'
            with patch.object(m, 'html', return_value=m.parse_page(HTML)):
                data = [entry()]
            data[0]['category'] = 'Medical & First Aid'
            data[0]['site_function'] = 'Medical TCCC'
            saved.write_text(json.dumps(data), encoding='utf8')
            db = m.CatalogCache(root/'trex_cache.sqlite3')
            try:
                self.assertEqual(db.import_old_manifests(root), 1)
                self.assertEqual(db.import_old_manifests(root), 0)
                with patch.object(m, 'html', side_effect=AssertionError('unexpected web request')):
                    entries = m.build_manifest({URL: 'Medical TCCC'}, root/'check.json',
                                               organized=True, cache=db)
                    self.assertEqual(len(entries), 1)
                    self.assertEqual(entries[0]['category'], 'Medical & First Aid')
                    # A NEW category combination has a new checkpoint but must
                    # reuse the same per-manual metadata from the SQLite cache.
                    entries2 = m.build_manifest({URL: 'Fieldcraft Survival'}, root/'other.json',
                                                organized=True, cache=db)
                    self.assertEqual(len(entries2), 1)
                    self.assertEqual(entries2[0]['site_function'], 'Medical TCCC')
            finally:
                db.close()

    def test_listing_cache_avoids_recrawl(self):
        with tempfile.TemporaryDirectory() as td:
            db = m.CatalogCache(Path(td)/'cache.sqlite3')
            try:
                with patch.object(m, 'crawl_listing', return_value=[(URL, 'First Aid')]) as fake:
                    self.assertIn(URL, m.crawl_functions(['Medical TCCC'], cache=db))
                    self.assertIn(URL, m.crawl_functions(['Medical TCCC'], cache=db))
                    self.assertEqual(fake.call_count, 1)
                    m.crawl_functions(['Medical TCCC'], cache=db, refresh=True)
                    self.assertEqual(fake.call_count, 2)
            finally:
                db.close()

    def test_shared_request_pacing_across_workers(self):
        starts = []
        lock = threading.Lock()
        payload = b'<html><a href="/one">A</a><a href="/two">B</a><a href="/three">C</a></html>'
        def fake_request(url, limit=None, extra_headers=None):
            with lock:
                starts.append(time.monotonic())
            return payload
        with patch.object(m,'PAUSE_BETWEEN_HTML',0.5), \
             patch.object(m,'_html_next_time',0.0), \
             patch.object(m,'request',side_effect=fake_request):
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(m.html,[URL]*3))
        intervals = [y-x for x,y in zip(sorted(starts),sorted(starts)[1:])]
        self.assertEqual(len(intervals),2)
        self.assertTrue(all(n >= 0.48 for n in intervals),intervals)

    def test_cache_respects_listing_expiry(self):
        with tempfile.TemporaryDirectory() as td:
            db = m.CatalogCache(Path(td)/'cache.sqlite3')
            try:
                db.put_listing('fn:medical-tccc', [(URL, 'First Aid')])
                self.assertIsNotNone(db.get_listing('fn:medical-tccc'))
                db.db.execute('UPDATE listings SET updated_at=1')
                db.db.commit()
                self.assertIsNone(db.get_listing('fn:medical-tccc'))
            finally:
                db.close()

    def test_parallel_metadata_cache_and_deterministic_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            urls = [URL + '-' + str(i) for i in range(11)]
            lock = threading.Lock()
            count = [0]
            def fake_html(url):
                with lock:
                    count[0] += 1
                time.sleep(0.002 * (int(url.rsplit('-', 1)[1]) % 3))
                return m.parse_page(HTML)
            db = m.CatalogCache(root/'cache.sqlite3')
            try:
                with patch.object(m,'html',side_effect=fake_html):
                    result = m.build_manifest({u:'Medical TCCC' for u in urls},
                                              root/'checkpoint.json', True, cache=db, workers=3)
                self.assertEqual([r['source_url'] for r in result], sorted(urls))
                self.assertEqual(count[0], 11)
                with patch.object(m,'html', side_effect=AssertionError('should be cached')):
                    again = m.build_manifest({u:'Medical TCCC' for u in urls},
                                             root/'fresh_checkpoint.json', True, cache=db, workers=3)
                self.assertEqual(len(again),11)
            finally:
                db.close()

    def test_download_404_is_permanent_no_retries(self):
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)/'FirstAid.pdf'
            def fail(url, extra_headers=None):
                raise HTTPError(url, 404, 'Not found', {}, None)
            with patch.object(m,'request',side_effect=fail) as fake:
                with self.assertRaises(m.PermanentMissingError):
                    m.download_with_resume(PDF_URL,target,'.pdf')
            self.assertEqual(fake.call_count,1)
            self.assertFalse(target.exists())

    def test_url_validation_rejects_host_or_scheme_swap(self):
        self.assertTrue(m.valid_public_download_url(PDF_URL,'.pdf'))
        for url in ('http://files.trex-arms.com/pdf/test.pdf',
                    'https://malicious.example/pdf/test.pdf',
                    'file:///etc/passwd',
                    'https://files.trex-arms.com/epub/test.pdf'):
            self.assertFalse(m.valid_public_download_url(url,'.pdf'),url)
        with tempfile.TemporaryDirectory() as td:
            with patch.object(m,'request',side_effect=AssertionError('network should be blocked')):
                with self.assertRaises(ValueError):
                    m.download_with_resume('file:///etc/passwd',Path(td)/'test.pdf','.pdf')

    def test_404_negative_expiration(self):
        with tempfile.TemporaryDirectory() as td:
            db = m.CatalogCache(Path(td)/'cache.sqlite3')
            try:
                db.put_negative(PDF_URL,404)
                self.assertEqual(db.negative_code(PDF_URL),404)
                db.db.execute('UPDATE unavailable SET updated_at=1')
                db.db.commit()
                self.assertIsNone(db.negative_code(PDF_URL))
            finally:
                db.close()

    def test_existing_unrecognized_file_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)/'FirstAid.pdf'
            target.write_bytes(b'SOMEONE HAND-WROTE IMPORTANT NOTES HERE')
            pdf = b'%PDF-1.7\n' + b'test' * 100
            r=FakeErrorResponse(pdf)
            r.headers={'Content-Length':str(len(pdf))}
            with patch.object(m,'request',return_value=r):
                self.assertEqual(m.download_with_resume(PDF_URL,target,'.pdf'),'downloaded')
            self.assertEqual((Path(td)/'FirstAid.pdf.invalid-1').read_bytes(),
                             b'SOMEONE HAND-WROTE IMPORTANT NOTES HERE')
            self.assertEqual(target.read_bytes(),pdf)

    def test_hash_cache_avoids_rehashing_same_files(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'one.pdf').write_bytes(b'%PDF-1.7\n' + b'0'*1024)
            db=m.CatalogCache(root/'cache.sqlite3')
            try:
                m.inventory_existing(root, root/'inventory.csv',root/'dups.csv',cache=db)
                with patch.object(m,'sha256_file',side_effect=AssertionError('unneeded hashing')):
                    m.inventory_existing(root, root/'inventory.csv',root/'dups.csv',cache=db)
            finally:
                db.close()

    def test_offline_index_from_cache_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            db=m.CatalogCache(root/'trex_cache.sqlite3')
            try:
                with patch.object(m,'html',return_value=m.parse_page(HTML)):
                    db.put_metadata(entry())
            finally:
                db.close()
            with patch.object(m.sys,'argv',['prog','--dest',str(root),'--index-only']), \
                 patch.object(m,'request',side_effect=AssertionError('No network')):
                self.assertEqual(m.main(),0)
            index=root/'Field_Manual_Catalog.html'
            self.assertIn('First Aid',index.read_text(encoding='utf8'))


if __name__=='__main__':
    unittest.main(verbosity=2)
