import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from docx_reader import read_docx, Document, Paragraph, Run, Table
from docs_plan import compile_document, DocsExecutor, Cancelled
from shortcuts import parse_shortcut, alternatives, command_supported

class ShortcutTests(unittest.TestCase):
    def test_sequence_holds_modifiers(self):
        self.assertEqual(parse_shortcut('holding Ctrl + Alt + Shift, press t then e'), ([17,18,16],[84,69]))
    def test_decimal_font_size_does_not_affect_key_parsing(self):
        self.assertEqual(parse_shortcut('Ctrl + Shift + .'), ([17,16],[190]))
    def test_catalog(self):
        catalog = json.loads(Path(__file__).with_name('docs_shortcuts.json').read_text(encoding='utf8'))
        expanded = [{**c,'name':c['name']+n,'shortcut':s} for c in catalog['commands'] for n,s in alternatives(c['shortcut'])]
        self.assertEqual(len(catalog['commands']),174)
        self.assertEqual(len(expanded),194)
        self.assertEqual(sum(command_supported(c)[0] for c in expanded),183)
        self.assertFalse(command_supported({'name':'Paste','shortcut':'Ctrl + v'})[0])

class DocxTests(unittest.TestCase):
    def test_inherited_style_and_explicit_bold_off(self):
        header = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        styles = f'<w:styles {header}><w:style w:type="paragraph" w:styleId="Normal" w:default="1"><w:rPr><w:b/><w:sz w:val="25"/></w:rPr></w:style></w:styles>'
        document = f'<w:document {header}><w:body><w:p><w:r><w:t>한글</w:t></w:r><w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t>English 😀</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'test.docx'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('word/styles.xml',styles)
                z.writestr('word/document.xml',document)
            result = read_docx(path)
        first = result.blocks[0]
        self.assertTrue(first.runs[0].style['bold'])
        self.assertFalse(first.runs[1].style['bold'])
        self.assertEqual(first.runs[0].style['size'],12.5)
        commands = compile_document(result)
        self.assertEqual(''.join(c.get('text','') for c in commands),'한글English 😀AB')
        self.assertEqual(sum(c['op']=='TABLE' for c in commands),1)
        self.assertFalse(result.blockers)

    def test_content_is_sent_character_by_character(self):
        sent, pauses, chords = [], [], []
        class Browser:
            def document_active(self): return True
        executor = DocsExecutor(Browser(),sent.append,lambda *keys:chords.append(keys),lambda:None,pauses.append,lambda *args:None)
        executor.run([{'op':'TYPE','text':'한A😀'}], .0005)
        self.assertEqual(sent,['한','A','😀'])
        self.assertEqual(pauses,[.0005]*3)
        self.assertFalse(chords)

    def test_cancellation_sends_no_further_characters(self):
        sent=[]
        class Browser:
            def document_active(self): return True
        def check():
            if sent:
                raise Cancelled('stop')
        executor=DocsExecutor(Browser(),sent.append,lambda *args:None,check,lambda seconds:None,lambda *args:None)
        with self.assertRaises(Cancelled):
            executor.run([{'op':'TYPE','text':'ABC'}], .01)
        self.assertEqual(sent,['A'])

class NativeInputTests(unittest.TestCase):
    def setUp(self):
        import typer
        self.typer=typer
        self.original=typer.u.SendInput
        self.events=[]
        def capture(count, events, size):
            self.assertEqual(size,40)
            self.events.extend((events[i].ki.wVk,events[i].ki.wScan,events[i].ki.dwFlags) for i in range(count))
            return count
        typer.u.SendInput=capture
    def tearDown(self):
        self.typer.u.SendInput=self.original
    def test_korean_and_surrogate_pair_are_unicode_keyboard_events(self):
        self.typer.send_character('한')
        self.typer.send_character('😀')
        self.assertEqual([x[2] for x in self.events],[4,6,4,6,4,6])
        self.assertTrue(all(x[0]==0 for x in self.events))
        self.assertEqual(self.events[0][1],ord('한'))
    def test_literal_tab_cannot_switch_browser_tab(self):
        self.typer.send_character('\t',literal=True)
        self.assertEqual(self.events,[(0,9,4),(0,9,6)])
    def test_modifiers_stay_down_for_a_multi_step_shortcut(self):
        self.typer.send_key_sequence([17,18,16],[84,69])
        self.assertEqual([x[0] for x in self.events],[17,18,16,84,84,69,69,16,18,17])
        self.assertEqual([x[2] for x in self.events],[0,0,0,0,2,0,2,2,2,2])

if __name__ == '__main__':
    unittest.main()
