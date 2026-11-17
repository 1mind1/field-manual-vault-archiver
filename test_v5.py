"""V5 offline regressions: incremental scans, bounded refresh, preview and safety."""
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import trex_vault_archiver as m

URL1 = 'https://www.trex-arms.com/field-manual-vault/fm-4-25-11-2002-12'
URL2 = 'https://www.trex-arms.com/field-manual-vault/atp-4-02-11-2026'
PDF1 = 'https://files.trex-arms.com/pdf/library/first-aid.pdf'
EPUB1 = 'https://files.trex-arms.com/epub/library/first-aid.epub'
PARAMS = {'fn': 'medical-tccc'}
PAGE1 = m.VAULT + '?fn=medical-tccc'
PAGE2 = m.VAULT + '?fn=medical-tccc&page=2'


def detail(title='First Aid'):
    return m.parse_page(f'''<!doctype html><html><head>
    <title>FM 4-25.11: {title} | T.REX ARMS Field Manual Vault</title></head><body>
    <a href="{PDF1}">PDF</a><a href="{EPUB1}">EPUB</a>
    <div>Function</div><div>Medical TCCC</div>
    <div>Edition</div><div>Dec 2002</div></body></html>'''.encode())


def listing(links, next_url=''):
    items = ''.join(f'<a href="{url}">{label}</a>' for url, label in links)
    next_link = f'<a href="{next_url}">Next</a>' if next_url else ''
    return m.parse_page(f'''<!doctype html><html><body>{items}{next_link}
        <a href="/one">A</a><a href="/two">B</a><a href="/three">C</a>
        </body></html>'''.encode())


def basic_entry(url=URL1, title='First Aid'):
    return {'source_url': url, 'designation': 'FM 4-25.11', 'title': title,
            'site_function': 'Medical TCCC', 'edition': 'Dec 2002',
            'collection': '', 'status': '', 'branch': '',
            'category': 'Medical & First Aid', 'subtopic': 'First Aid', 'tags': '',
            'folder': f'FM 4-25.11 - {title}',
            'file_stem': f'FM 4-25.11 - {title} (Dec 2002)',
            'pdf_url': PDF1, 'epub_url': EPUB1}


class V5Tests(unittest.TestCase):
    def test_listing_page_checkpoint_survives_and_resumes_from_page_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = m.CatalogCache(Path(tmp) / 'cache.db')
            urls = []
            def first_round(url):
                urls.append(url)
                if url == PAGE1:
                    return listing([(URL1, 'First Aid')], PAGE2)
                raise RuntimeError('Connection reset on page 2')
            try:
                with patch.object(m, 'html', side_effect=first_round):
                    with self.assertRaises(RuntimeError):
                        m.crawl_listing(PARAMS, 'Medical TCCC', cache, 'fn:medical-tccc')
                state = cache.get_scan_state('fn:medical-tccc', PARAMS)
                self.assertEqual(state['pages'], 1)
                self.assertEqual(state['next_url'], PAGE2)
                self.assertEqual(urls, [PAGE1, PAGE2])
                resumed = []
                def second_round(url):
                    resumed.append(url)
                    return listing([(URL2, 'New First Aid')])
                with patch.object(m, 'html', side_effect=second_round):
                    results = m.crawl_listing(PARAMS, 'Medical TCCC', cache, 'fn:medical-tccc')
                self.assertEqual(resumed, [PAGE2])
                self.assertEqual({url for url, _ in results}, {URL1, URL2})
                self.assertIsNone(cache.get_scan_state('fn:medical-tccc', PARAMS))
            finally:
                cache.close()

    def test_listing_checkpoint_rejects_tampered_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = m.CatalogCache(Path(tmp) / 'cache.db')
            try:
                cache.put_scan_state('fn:medical-tccc', {
                    'params': PARAMS, 'next_url': 'https://example.net/private',
                    'pages': 42, 'found': {URL1: 'First Aid'}, 'visited': [PAGE1]})
                urls = []
                def fetch(url):
                    urls.append(url)
                    return listing([(URL1, 'First Aid')])
                with patch.object(m, 'html', side_effect=fetch):
                    m.crawl_listing(PARAMS, 'Medical TCCC', cache, 'fn:medical-tccc')
                self.assertEqual(urls, [PAGE1])
            finally:
                cache.close()

    def test_stale_metadata_batch_recheck_detects_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                cache.put_metadata(basic_entry())
                cache.db.execute('UPDATE metadata SET updated_at=?', (int(time.time()) - 40 * 86400,))
                cache.db.commit()
                stats = {}
                with patch.object(m, 'html', return_value=detail('First Aid Updated')) as web:
                    rows = m.build_manifest({URL1: 'Medical TCCC'}, root / 'checkpoint.json',
                                            organized=True, cache=cache, recheck_limit=1,
                                            recheck_age=30 * 86400, stats=stats)
                self.assertEqual(web.call_count, 1)
                self.assertEqual(rows[0]['title'], 'First Aid Updated')
                self.assertEqual(stats['metadata_new_fetched'], 0)
                self.assertEqual(stats['metadata_rechecked'], 1)
                self.assertEqual(stats['metadata_changed'], 1)
                self.assertEqual(cache.metadata(URL1)['title'], 'First Aid Updated')
                # The next run should be entirely offline; recheck aged out.
                with patch.object(m, 'html', side_effect=AssertionError('unexpected new request')):
                    again = m.build_manifest({URL1: 'Medical TCCC'}, root / 'other.json',
                                             organized=True, cache=cache, recheck_limit=1,
                                             recheck_age=30 * 86400)
                self.assertEqual(again[0]['title'], 'First Aid Updated')
            finally:
                cache.close()

    def test_failed_metadata_recheck_never_erases_cached_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                cache.put_metadata(basic_entry())
                stats = {}
                with patch.object(m, 'html', side_effect=RuntimeError('timeout')):
                    rows = m.build_manifest({URL1: 'Medical TCCC'}, root / 'checkpoint.json',
                                            organized=True, cache=cache, recheck_limit=1,
                                            recheck_age=0, stats=stats)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['title'], 'First Aid')
                self.assertEqual(cache.metadata(URL1)['title'], 'First Aid')
                self.assertEqual(stats['metadata_failed'], 1)
            finally:
                cache.close()

    def test_update_only_does_not_download_binaries_or_move_legacy_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_folder = root / 'First Aid' / 'my important archive'
            old_folder.mkdir(parents=True)
            original = old_folder / 'notes.txt'
            original.write_text('DO NOT MOVE OR DELETE', encoding='utf-8')
            args = ['prog', '--dest', str(root), '--mode', 'selective',
                    '--site-functions', 'medical-tccc', '--update-only',
                    '--metadata-ttl-days', '30']
            with patch.object(m.sys, 'argv', args), \
                 patch.object(m, 'crawl_listing', return_value=[(URL1, 'First Aid')]), \
                 patch.object(m, 'html', return_value=detail()), \
                 patch.object(m, 'download_with_resume', side_effect=AssertionError('binary download')):
                self.assertEqual(m.main(), 0)
            self.assertEqual(original.read_text(encoding='utf-8'), 'DO NOT MOVE OR DELETE')
            self.assertEqual(len(list(root.rglob('*.pdf'))), 0)
            self.assertEqual(len(list(root.rglob('*.epub'))), 0)
            self.assertTrue((root / 'Field_Manual_Coverage_Report.json').is_file())

    def test_storage_estimator_one_head_per_selected_sample_and_no_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                rows = [basic_entry(url=URL1), basic_entry(url=URL2)]
                with patch.object(m, 'remote_content_length',
                                  side_effect=lambda u, ext: 20_000_000 if ext == '.pdf' else 2_000_000) as heads:
                    result = m.estimate_storage(rows, root, 'both', '', cache, head_samples=2)
                self.assertEqual(heads.call_count, 2)
                self.assertEqual(result['files_still_needed'], 4)
                self.assertEqual(result['sample_attempts'], 2)
                self.assertEqual(result['unknown_files_without_basis'], 0)
                self.assertIsNotNone(result['projected_remaining_bytes'])
                self.assertTrue((root / 'Field_Manual_Storage_Estimate.json').exists())
                with patch.object(m, 'remote_content_length',
                                  side_effect=AssertionError('sizes should be cached')):
                    cache_result = m.estimate_storage(rows, root, 'both', '', cache, head_samples=0)
                self.assertEqual(cache_result['sample_attempts'], 0)
            finally:
                cache.close()

    def test_storage_estimator_no_fake_total_if_head_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                with patch.object(m, 'remote_content_length', return_value=None):
                    result = m.estimate_storage([basic_entry()], root, 'both', '', cache, head_samples=2)
                self.assertIsNone(result['projected_remaining_bytes'])
                self.assertEqual(result['unknown_files_without_basis'], 2)
            finally:
                cache.close()

    def test_head_probes_stop_after_two_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                many = [basic_entry(url=URL1 + '-' + str(i)) for i in range(20)]
                with patch.object(m, 'remote_content_length', return_value=None) as probes:
                    m.estimate_storage(many, root, 'pdf', '', cache, head_samples=8)
                self.assertEqual(probes.call_count, 2)
            finally:
                cache.close()

    def test_storage_does_not_count_already_valid_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                entry = m._entry_with_category(basic_entry(), 'Medical TCCC', True)
                pdf = m.destination_file(root, entry, '.pdf', '')
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(b'%PDF-1.7\n' + b'0' * 2048)
                result = m.estimate_storage([entry], root, 'pdf', '', cache, 0)
                self.assertEqual(result['files_still_needed'], 0)
                self.assertEqual(result['projected_remaining_bytes'], 0)
                self.assertEqual(cache.get_file_size(PDF1), pdf.stat().st_size)
            finally:
                cache.close()

    def test_coverage_does_not_compare_subset_to_entire_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                row = basic_entry()
                cache.put_metadata(row)
                subset = m.write_coverage_report(root, cache, 'selective',
                                                  ['Medical TCCC'], {URL1: 'Medical TCCC'}, [row])
                self.assertFalse(subset['site_wide_comparison_valid'])
                self.assertNotIn('difference_from_reference_count', subset)
                full = m.write_coverage_report(root, cache, 'functions', [],
                                                {URL1: 'Medical TCCC', URL2: 'Medical TCCC'}, [row],
                                                {'listings_failed': [{'category':'Other','error':'timeout'}]})
                self.assertFalse(full['site_wide_comparison_valid'])
                self.assertEqual(full['known_unparsed_urls'], [URL2])
                self.assertIn('difference_from_reference_count', full)
            finally:
                cache.close()

    def test_incremental_category_preserves_old_manifest_not_in_new_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = m.hashlib.sha256('Medical TCCC'.encode()).hexdigest()[:10]
            manifest = root / f'trex_manifest_selective_{key}.json'
            manifest.write_text(json.dumps([basic_entry()]), encoding='utf8')
            args = ['prog', '--dest', str(root), '--mode', 'selective',
                    '--site-functions', 'medical-tccc', '--update-only']
            def fake_html(url):
                return detail('New Medical Edition')
            with patch.object(m.sys, 'argv', args), \
                 patch.object(m, 'crawl_listing', return_value=[(URL2, 'New Medical Edition')]), \
                 patch.object(m, 'html', side_effect=fake_html):
                self.assertEqual(m.main(), 0)
            new = json.loads(manifest.read_text(encoding='utf8'))
            self.assertEqual({e['source_url'] for e in new}, {URL1, URL2})

    def test_failed_listing_scan_forces_retry_despite_recent_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = m.hashlib.sha256('Medical TCCC'.encode()).hexdigest()[:10]
            manifest = root / f'trex_manifest_selective_{key}.json'
            manifest.write_text(json.dumps([basic_entry()]), encoding='utf8')
            args = ['prog', '--dest', str(root), '--mode', 'selective',
                    '--site-functions', 'medical-tccc', '--update-only']
            with patch.object(m.sys, 'argv', args), \
                 patch.object(m, 'crawl_listing', side_effect=RuntimeError('site temporarily down')):
                self.assertEqual(m.main(), 1)
            incomplete = root / f'trex_incomplete_selective_{key}.json'
            self.assertTrue(incomplete.exists())
            retry_args = ['prog', '--dest', str(root), '--mode', 'selective',
                          '--site-functions', 'medical-tccc', '--dry-run']
            with patch.object(m.sys, 'argv', retry_args), \
                 patch.object(m, 'crawl_listing', return_value=[(URL1, 'First Aid')]) as fetched:
                self.assertEqual(m.main(), 0)
            self.assertEqual(fetched.call_count, 1)
            self.assertFalse(incomplete.exists())

    def test_non_manual_citations_page_excluded(self):
        self.assertIsNone(m.normalized_detail('/field-manual-vault/citations', m.VAULT))
        page = listing([(URL1, 'First Aid'), ('/field-manual-vault/citations', 'Citations')])
        entries, _next = m.listing_page_links(m.VAULT + '?fn=medical-tccc', page)
        self.assertEqual([url for url, _ in entries], [URL1])

    def test_numeric_metadata_badges_are_cleaned(self):
        page = m.parse_page(f"""<!doctype html><html><head><title>FM 34-7: Signal Test | T.REX ARMS</title></head><body>
            <div>Function</div><span>18</span><div>Collection</div><span>95</span>
            <a href="{PDF1}">PDF</a><a href="{EPUB1}">EPUB</a>
            </body></html>""".encode())
        fields = m.detail_metadata(page)
        self.assertEqual(fields[3], '')
        self.assertEqual(fields[4], '')
        repaired = m.repair_cached_metadata({'site_function': '71', 'collection': '35'}, 'Medical TCCC')
        self.assertEqual(repaired['site_function'], 'Medical TCCC')
        self.assertEqual(repaired['collection'], '')
        unknown = m.repair_cached_metadata({'site_function': 'New Function', 'collection': 'Recent Archive'}, 'Medical TCCC')
        self.assertEqual(unknown['site_function'], 'New Function')

    def test_bad_cached_field_repaired_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                row = basic_entry()
                row['site_function'] = '18'
                row['collection'] = '35'
                cache.put_metadata(row)
                with patch.object(m, 'html', side_effect=AssertionError('No web request expected')):
                    entries = m.build_manifest({URL1: 'Medical TCCC'}, root / 'progress.json',
                                               organized=True, cache=cache)
                self.assertEqual(entries[0]['site_function'], 'Medical TCCC')
                self.assertEqual(entries[0]['collection'], '')
                self.assertEqual(cache.metadata(URL1)['site_function'], 'Medical TCCC')
                self.assertEqual(cache.metadata(URL1)['collection'], '')
            finally:
                cache.close()

    def test_successful_metadata_retry_removes_old_error_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            err = root / 'trex_metadata_errors.csv'
            err.write_text('stale failure', encoding='utf-8')
            with patch.object(m, 'html', return_value=detail()):
                entries = m.build_manifest({URL1: 'Medical TCCC'}, root / 'progress.json',
                                            organized=True)
            self.assertEqual(len(entries), 1)
            self.assertFalse(err.exists())

    def test_report_flags_malformed_cached_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                entry = basic_entry()
                entry['site_function'] = '71'
                entry['collection'] = '95'
                cache.put_metadata(entry)
                report = m.write_coverage_report(root, cache, 'selective', ['Medical TCCC'],
                                                 {URL1: 'Medical TCCC'}, [entry])
                self.assertEqual(report['suspect_cached_metadata_count'], 1)
                self.assertEqual(report['catalog_function_counts'], {'Unknown': 1})
                self.assertIn('unrecognized Function', report['suspect_cached_metadata'][0]['issues'][0])
            finally:
                cache.close()

    def test_speed_inputs_validate_and_invalid_pages_rejected(self):
        self.assertTrue(m.safe_listing_page(PAGE1, PARAMS))
        self.assertTrue(m.safe_listing_page(PAGE2, PARAMS))
        for invalid in [
            'http://www.trex-arms.com/field-manual-vault?fn=medical-tccc',
            'https://evil.trex-arms.com/field-manual-vault?fn=medical-tccc',
            m.VAULT + '?fn=medical-tccc&other=private',
            m.VAULT + '?fn=medical-tccc&page=-1',
        ]:
            self.assertFalse(m.safe_listing_page(invalid, PARAMS), invalid)
        with patch.object(m.sys, 'argv', ['prog', '--workers', '9']):
            with self.assertRaises(SystemExit):
                m.main()


if __name__ == '__main__':
    unittest.main(verbosity=2)


class MetadataCleanupTests(unittest.TestCase):
    """Offline safety regressions for malformed values found in the Windows cache."""

    def test_repair_from_single_cached_official_function_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                bad = basic_entry()
                bad.update(site_function='18', collection='95', status='9')
                cache.put_metadata(bad)
                cache.put_listing('fn:medical-tccc', [(URL1, 'First Aid')])
                before = cache.db.execute('SELECT updated_at FROM metadata WHERE url=?', (URL1,)).fetchone()[0]
                fixes = cache.repair_legacy_metadata()
                self.assertEqual({f['field'] for f in fixes}, {'site_function', 'collection', 'status'})
                self.assertEqual(cache.repair_legacy_metadata(), [])  # idempotent
                corrected = cache.metadata(URL1)
                self.assertEqual(corrected['site_function'], 'Medical TCCC')
                self.assertEqual(corrected['collection'], '')
                self.assertEqual(corrected['status'], '')
                self.assertEqual(corrected['pdf_url'], PDF1)
                self.assertEqual(corrected['epub_url'], EPUB1)
                after = cache.db.execute('SELECT updated_at FROM metadata WHERE url=?', (URL1,)).fetchone()[0]
                self.assertEqual(before, after)  # do not falsely freshen a source check
                report = m.write_coverage_report(root, cache, 'offline', [], {}, [])
                self.assertEqual(report['suspect_cached_metadata_count'], 0)
                self.assertEqual(report['run_urls_discovered'], 0)
                self.assertEqual(report['scope'], 'offline cache review (no site scan)')
            finally:
                cache.close()

    def test_unknown_or_ambiguous_function_is_not_invented(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                bad = basic_entry()
                bad['site_function'] = '71'
                cache.put_metadata(bad)
                cache.put_listing('fn:medical-tccc', [(URL1, 'First Aid')])
                cache.put_listing('fn:comms-signal', [(URL1, 'Some Listing')])
                changed = cache.repair_legacy_metadata()
                self.assertEqual(len(changed), 1)
                self.assertEqual(cache.metadata(URL1)['site_function'], '')
                report = m.write_coverage_report(root, cache, 'offline', [], {}, [])
                self.assertEqual(report['suspect_cached_metadata_count'], 1)
                self.assertIn("unrecognized Function", report['suspect_cached_metadata'][0]['issues'][0])
            finally:
                cache.close()

    def test_numeric_status_from_live_page_removed_and_other_status_retained(self):
        def page_for(status):
            return m.parse_page(f'''<!doctype html><html><head><title>FM 25-1: A Manual | T.REX ARMS</title></head>
                <body><div>Status</div><div>{status}</div>
                <a href="{PDF1}">PDF</a><a href="{EPUB1}">EPUB</a></body></html>'''.encode())
        self.assertEqual(m.detail_metadata(page_for('9'))[5], '')
        self.assertEqual(m.detail_metadata(page_for('Active'))[5], 'Active')
        self.assertEqual(m.detail_metadata(page_for('Superseded'))[5], 'Superseded')

    def test_saved_recent_manifest_and_catalog_are_cleaned_without_web_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = m.hashlib.sha256('Medical TCCC'.encode()).hexdigest()[:10]
            manifest = root / f'trex_manifest_selective_{key}.json'
            bad = basic_entry()
            bad.update(site_function='18', collection='35', status='9', category='Other', subtopic='General Reference')
            manifest.write_text(json.dumps([bad]), encoding='utf-8')
            cache = m.CatalogCache(root / 'trex_cache.sqlite3')
            try:
                cache.put_metadata(bad)
                cache.put_listing('fn:medical-tccc', [(URL1, 'First Aid')])
            finally:
                cache.close()
            args = ['prog', '--dest', str(root), '--mode', 'selective',
                    '--site-functions', 'medical-tccc', '--dry-run', '--estimate-samples', '0']
            with patch.object(m.sys, 'argv', args), \
                 patch.object(m, 'html', side_effect=AssertionError('No site request expected')):
                self.assertEqual(m.main(), 0)
            updated = json.loads(manifest.read_text(encoding='utf-8'))[0]
            self.assertEqual(updated['site_function'], 'Medical TCCC')
            self.assertEqual(updated['category'], 'Medical & First Aid')
            self.assertEqual(updated['status'], '')
            self.assertEqual(updated['collection'], '')
            repairs_csv = (root / 'Field_Manual_Metadata_Repairs.csv').read_text(encoding='utf-8-sig')
            self.assertIn('site_function', repairs_csv)
            self.assertIn('status', repairs_csv)
            self.assertEqual(len(repairs_csv.splitlines()), 4)  # header + 3 individual fixes
            report = json.loads((root / 'Field_Manual_Coverage_Report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['suspect_cached_metadata_count'], 0)
            self.assertEqual(list(root.rglob('*.pdf')), [])
            self.assertEqual(list(root.rglob('*.epub')), [])

    def test_offline_index_cleans_old_cache_and_regenerates_coverage_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'trex_cache.sqlite3')
            try:
                bad = basic_entry()
                bad.update(site_function='18', collection='95', status='9')
                cache.put_metadata(bad)
                cache.put_listing('fn:medical-tccc', [(URL1, 'First Aid')])
            finally:
                cache.close()
            (root / 'Field_Manual_Coverage_Report.txt').write_text('old warning', encoding='utf-8')
            with patch.object(m.sys, 'argv', ['prog', '--dest', str(root), '--index-only']), \
                 patch.object(m, 'html', side_effect=AssertionError('offline index must not fetch')):
                self.assertEqual(m.main(), 0)
            report = (root / 'Field_Manual_Coverage_Report.txt').read_text(encoding='utf-8')
            self.assertIn('Unrecognized or malformed cached metadata records: 0', report)
            self.assertIn('Offline cache review: no site scan was performed.', report)
            self.assertTrue((root / 'Field_Manual_Catalog.html').exists())

    def test_unrecognized_textual_status_is_reported_not_erased(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = m.CatalogCache(root / 'cache.db')
            try:
                row = basic_entry()
                row['status'] = 'Pending Review'
                cache.put_metadata(row)
                self.assertEqual(cache.repair_legacy_metadata(), [])
                self.assertEqual(cache.metadata(URL1)['status'], 'Pending Review')
                report = m.write_coverage_report(root, cache, 'offline', [], {}, [])
                self.assertEqual(report['suspect_cached_metadata_count'], 1)
                self.assertIn("unrecognized Status", report['suspect_cached_metadata'][0]['issues'][0])
            finally:
                cache.close()
