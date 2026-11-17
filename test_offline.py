import csv
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('archiver', HERE/'trex_vault_archiver.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PDF = b'%PDF-1.7\n' + b'fixture-pdf-test-' * 30

def epub_bytes():
    s = io.BytesIO()
    with zipfile.ZipFile(s, 'w', compression=zipfile.ZIP_STORED) as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('OEBPS/content.xhtml', '<html>'+'hello'*50+'</html>')
    return s.getvalue()

class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.status = 200
        self.headers = {'Content-Length': str(len(payload))}

class Tests(unittest.TestCase):
    def test_listing_parse(self):
        p = m.parse_page(b'''<html><title>Vault</title><a href="/field-manual-vault/tc-3-21-75-2013">Warrior Ethos</a><a href="?fn=medical-tccc&page=2">Next</a></html>''')
        links, next_url = m.listing_page_links('https://www.trex-arms.com/field-manual-vault?fn=medical-tccc', p)
        self.assertEqual(len(links), 1)
        self.assertEqual(next_url, 'https://www.trex-arms.com/field-manual-vault?fn=medical-tccc&page=2')
    def test_detail(self):
        p = m.parse_page(b'''<html><head><title>TC 3-21.75: The Warrior Ethos and Soldier Combat Skills | T.REX ARMS Field Manual Vault</title></head><body>
        <a href="https://files.trex-arms.com/pdf/army/tc/file.pdf">PDF</a>
        <a href="https://files.trex-arms.com/epub/army/tc/file.epub">EPUB</a>
        <div>Function</div><div>Small Unit Tactics</div><div>Collection</div><div>Current Doctrine</div><div>Edition</div><div>Oct 2013</div></body></html>''')
        desig, title, edition, function, coll, _status, _branch, pdf, epub = m.detail_metadata(p)
        self.assertEqual(desig, 'TC 3-21.75')
        self.assertEqual(edition, 'Oct 2013')
        self.assertEqual(function, 'Small Unit Tactics')
        self.assertTrue(pdf.endswith('/file.pdf'))
        self.assertTrue(epub.endswith('/file.epub'))
        primary, sub, tags = m.category_for(function, title, coll)
        self.assertEqual(primary, 'Infantry & Small Unit Tactics')
        self.assertEqual(sub, 'Soldier Combat Skills')
        self.assertIn('Combat Skills', tags)
    def test_navigation_primary_category(self):
        a, b, tags = m.category_for('Fieldcraft Survival', 'Map Reading and Land Navigation')
        self.assertEqual(a, 'Navigation & Terrain')
        self.assertEqual(b, 'Land Navigation & Maps')
        self.assertIn('Land Navigation', tags)
    def test_sanitization_and_paths(self):
        with tempfile.TemporaryDirectory() as t:
            e={'category':'Medical & First Aid','subtopic':'First Aid','folder':'FM 4-25.11 - First Aid','file_stem':'FM 4-25.11 - First Aid (Dec 2002)', 'designation':'FM 4-25.11', 'title':'First Aid','edition':'Dec 2002','source_url':'https://www.trex-arms.com/field-manual-vault/fm-4-25-11'}
            p=m.destination_file(Path(t),e,'.pdf','')
            self.assertEqual(p.parent.parent.name, 'First Aid')
            self.assertEqual(p.suffix,'.pdf')
            self.assertIn('Organized Library',str(p))
            m.write_offline_index([e],Path(t),'')
            index=(Path(t)/'Field_Manual_Catalog.html').read_text()
            self.assertIn('Medical &amp; First Aid',index)
            self.assertIn('Vault page',index)
    def test_hash_duplicates_and_hardlink(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            existing = root/'First Aid'/'Warrior Ethos'/'tc 3-21.75.pdf'
            existing.parent.mkdir(parents=True)
            existing.write_bytes(PDF)
            existing_epub=root/'First Aid'/'Warrior Ethos'/'tc 3-21.75.epub'
            existing_epub.write_bytes(epub_bytes())
            identical_pdf=root/'Old'/'duplicate.pdf'
            identical_pdf.parent.mkdir()
            identical_pdf.write_bytes(PDF)
            index=m.inventory_existing(root,root/'inventory.csv',root/'duplicates.csv')
            self.assertEqual(len(index),2)
            dup=(root/'duplicates.csv').read_text(encoding='utf-8-sig')
            self.assertIn('First Aid',dup)
            self.assertIn('Old',dup)
            target=root/'Organized Library'/'Infantry & Small Unit Tactics'/'Soldier Combat Skills'/'TC 3-21.75'/'tc.pdf'
            with patch.object(m,'request',return_value=Response(PDF)):
                status=m.download_with_resume('https://files.trex-arms.com/pdf/file.pdf',target,'.pdf',index)
            self.assertEqual(status,'linked')
            self.assertEqual(target.read_bytes(),PDF)
            self.assertIn(target.stat().st_ino, {existing.stat().st_ino, identical_pdf.stat().st_ino})
            self.assertFalse((target.with_name('tc.pdf.part')).exists())
    def test_epub_new_download(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            target=root/'New.epub'
            payload=epub_bytes()
            with patch.object(m,'request',return_value=Response(payload)):
                status=m.download_with_resume('https://files.trex-arms.com/epub/file.epub',target,'.epub',{})
            self.assertEqual(status,'downloaded')
            self.assertTrue(m.valid_file(target,'.epub'))
    def test_manifest_from_fake_html(self):
        with tempfile.TemporaryDirectory() as t:
            page = m.parse_page(b'''<html><head><title>FM 4-25.11: First Aid | T.REX ARMS Field Manual Vault</title></head><body>
                <a href="https://files.trex-arms.com/pdf/army/fm/test.pdf">PDF</a>
                <a href="https://files.trex-arms.com/epub/army/fm/test.epub">EPUB</a>
                <div>Edition</div><div>Dec 2002</div><div>Function</div><div>Medical TCCC</div><div>Collection</div><div>Recent Archive</div>
                </body></html>''')
            with patch.object(m,'html',return_value=page):
                entries=m.build_manifest({'https://www.trex-arms.com/field-manual-vault/fm-4-25-11-2002-12-2':''},Path(t)/'checkpoint.json',True)
            self.assertEqual(len(entries),1)
            self.assertEqual(entries[0]['category'],'Medical & First Aid')
            self.assertEqual(entries[0]['subtopic'],'First Aid')
            csv_path=Path(t)/'catalog.csv'
            m.write_csv(entries,csv_path)
            with csv_path.open(encoding='utf-8-sig') as f:
                row=list(csv.DictReader(f))[0]
            self.assertIn('Medical',row['category'])


class SelectiveTests(unittest.TestCase):
    def fixture(self, function, category, title, suffix):
        desig = 'FM 4-25.11' if function == 'Medical TCCC' else 'FM 3-25.26'
        return {
            'category': category, 'subtopic': 'First Aid' if function == 'Medical TCCC' else 'Land Navigation & Maps',
            'tags': '', 'site_function': function, 'collection':'Current', 'status':'', 'branch':'',
            'designation': desig, 'title':title, 'edition':'Jan 2000',
            'folder': f'{desig} - {title}', 'file_stem':f'{desig} - {title} (Jan 2000)',
            'source_url':f'https://www.trex-arms.com/field-manual-vault/{suffix}',
            'pdf_url':'https://files.trex-arms.com/pdf/army/fm/test.pdf',
            'epub_url':'https://files.trex-arms.com/epub/army/fm/test.epub',
        }

    def test_interactive_options_and_default_cap(self):
        with patch('builtins.input', side_effect=['6,13', '', 'p', '25', '']):
            self.assertEqual(m.ask_selective(),
                             (['Medical TCCC','Fieldcraft Survival'], '', 'pdf', 25, 5.0))

    def test_selected_categories_parser(self):
        self.assertEqual(m.resolve_site_functions('medical-tccc,Fieldcraft Survival,medical-tccc'),
                         ['Medical TCCC','Fieldcraft Survival'])
        for bad in ('', 'all', 'Nonexistent'):
            with self.assertRaises(ValueError):
                m.resolve_site_functions(bad)

    def test_category_crawler_only_requests_selected(self):
        with patch.object(m, 'crawl_listing', return_value=[]) as scan:
            m.crawl_functions(['Medical TCCC','Fieldcraft Survival'])
        self.assertEqual(scan.call_count,2)
        self.assertEqual(scan.call_args_list[0].args[0], {'fn':'medical-tccc'})
        self.assertEqual(scan.call_args_list[1].args[0], {'fn':'fieldcraft-survival'})

    def test_byte_budget_stops_mid_file_and_preserves_partial(self):
        with tempfile.TemporaryDirectory() as t:
            dest = Path(t)/'test.pdf'
            payload = b'%PDF-1.7\n' + b'X'*450_000
            budget = m.DownloadBudget(300_000)
            with patch.object(m,'request',return_value=Response(payload)):
                with self.assertRaises(m.BudgetReached):
                    m.download_with_resume('https://files.trex-arms.com/pdf/test.pdf',dest,'.pdf',budget=budget)
            self.assertFalse(dest.exists())
            self.assertEqual(budget.used_bytes,262_144)
            self.assertEqual(Path(str(dest)+'.part').stat().st_size,262_144)

    def test_selective_filters_by_site_category_and_title(self):
        import hashlib
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            key=hashlib.sha256('Medical TCCC'.encode()).hexdigest()[:10]
            cache=root/f'trex_manifest_selective_{key}.json'
            entries=[
                self.fixture('Medical TCCC','Medical & First Aid','First Aid','first-aid'),
                self.fixture('Medical TCCC','Medical & First Aid','Medical Logistics','medical-logistics'),
                self.fixture('Fieldcraft Survival','Navigation & Terrain','Map Reading','navigation'),
            ]
            cache.write_text(m.json.dumps(entries),encoding='utf8')
            argv=['archiver','--mode','selective','--site-functions','medical-tccc',
                  '--title-contains','First Aid','--format','pdf','--dest',str(root)]
            with patch.object(m.sys,'argv',argv), patch.object(m,'request',return_value=Response(PDF)):
                self.assertEqual(m.main(),0)
            self.assertTrue((root/'Organized Library'/'Medical & First Aid'/'First Aid').exists())
            self.assertFalse((root/'Organized Library'/'Navigation & Terrain').exists())
            self.assertEqual(len(list((root/'Organized Library').rglob('*.pdf'))),1)
            self.assertEqual(len(list((root/'Organized Library').rglob('*.epub'))),0)

    def test_combines_cached_subset_catalogs(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            first=self.fixture('Medical TCCC','Medical & First Aid','First Aid','first-aid')
            second=self.fixture('Fieldcraft Survival','Navigation & Terrain','Map Reading','navigation')
            (root/'trex_manifest_selective_abc.json').write_text(m.json.dumps([first]),encoding='utf8')
            (root/'trex_manifest_selective_def.json').write_text(m.json.dumps([second]),encoding='utf8')
            merged=m.combine_selective_catalogs(root,[first])
            self.assertEqual({e['source_url'] for e in merged}, {first['source_url'],second['source_url']})

if __name__=='__main__':
    unittest.main(verbosity=2)
