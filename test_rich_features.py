import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from docx_reader import read_docx, Document, Paragraph, Run, Table, Cell
from docs_plan import compile_document, DocsExecutor
from docs_browser import DocsBrowser

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'

class RichParserTests(unittest.TestCase):
    def fixture(self, simple=False):
        styles=f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="Normal" w:default="1"><w:pPr><w:spacing w:before="240" w:after="120" w:line="360"/></w:pPr><w:rPr><w:color w:val="BB1122"/></w:rPr></w:style></w:styles>'
        document=f'''<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body><w:p><w:r><w:t>Body</w:t></w:r></w:p>
        <w:tbl><w:tblGrid><w:gridCol w:w="1440"/><w:gridCol w:w="2880"/><w:gridCol w:w="1440"/></w:tblGrid>
        <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/><w:shd w:fill="E8EFF5"/></w:tcPr><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr>
        <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc>
        <w:tc><w:p><w:r><w:t>C</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:sectPr><w:headerReference w:type="default" r:id="h"/><w:footerReference w:type="default" r:id="f"/></w:sectPr></w:body></w:document>'''
        field=('<w:fldSimple w:instr=" PAGE "><w:r><w:t>99</w:t></w:r></w:fldSimple>' if simple else
               '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>PAGE</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/><w:t>99</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>')
        footer=f'<w:ftr xmlns:w="{W}"><w:p><w:r><w:t>Footer </w:t></w:r>{field}</w:p></w:ftr>'
        header=f'<w:hdr xmlns:w="{W}"><w:p><w:r><w:t>Header</w:t></w:r></w:p></w:hdr>'
        rels='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="h" Target="header1.xml"/><Relationship Id="f" Target="/word/footer1.xml"/></Relationships>'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.docx'
            with zipfile.ZipFile(path,'w') as z:
                for name,text in [('word/document.xml',document),('word/styles.xml',styles),('word/header1.xml',header),('word/footer1.xml',footer),('word/_rels/document.xml.rels',rels)]:
                    z.writestr(name,text)
            return read_docx(path)

    def test_color_spacing_merge_geometry_and_footer_field(self):
        document=self.fixture()
        self.assertFalse(document.blockers)
        self.assertEqual(document.blocks[0].runs[0].style['color'],'BB1122')
        self.assertEqual(document.blocks[0].spacing,{'before_pt':12,'after_pt':6,'line_multiple':1.5})
        table=document.blocks[1]
        self.assertEqual(table.columns,3)
        self.assertEqual(len(table.cells),3)
        self.assertEqual((table.cells[0].row_span,table.cells[0].column_span),(2,2))
        self.assertEqual(table.cells[0].background,'E8EFF5')
        self.assertEqual(document.footers[0].runs[-1].field_code,'PAGE')
        commands=compile_document(document)
        self.assertEqual([c['inches'] for c in commands if c['op']=='COLUMN_WIDTH'],[1,2,1])
        merge=next(c for c in commands if c['op']=='MERGE_CELLS')
        self.assertEqual((merge['row'],merge['column'],merge['row_span'],merge['column_span']),(0,0,2,2))
        body=''.join(c.get('text','') for c in commands)
        self.assertEqual(body,'BodyABCHeaderFooter ')
        self.assertNotIn('99',body)
        self.assertEqual([c['zone'] for c in commands if c['op']=='HEADER_FOOTER'],['header','footer'])

    def test_simple_field_is_not_typed_as_a_cached_number(self):
        commands=compile_document(self.fixture(simple=True))
        self.assertEqual(sum(c['op']=='PAGE_FIELD' for c in commands),1)
        self.assertNotIn('99',''.join(c.get('text','') for c in commands))

class ExecutorTests(unittest.TestCase):
    def test_new_commands_reach_the_ui_adapter_and_keep_character_typing(self):
        events=[]
        class Browser:
            def document_active(self): return True
            def set_color(self,kind,color,*args):events.append(('color',kind,color))
            def paragraph_spacing(self,value,*args):events.append(('spacing',value))
            def column_width(self,col,width,*args):events.append(('width',col,width))
            def merge_cells(self,value,*args):events.append(('merge',value))
            def page_field(self,kind,*args):events.append(('field',kind))
        sent=[]
        executor=DocsExecutor(Browser(),sent.append,lambda *k:events.append(('key',k)),lambda:None,lambda s:None,lambda *a:None,lambda m,t:events.append(('sequence',m,t)))
        commands=[{'op':'CELL_BACKGROUND','color':'E8EFF5'},
                  {'op':'PARAGRAPH_SPACING','value':{'before_pt':12,'after_pt':6,'line_multiple':1.5}},
                  {'op':'COLUMN_WIDTH','column':1,'inches':2},
                  {'op':'MERGE_CELLS','row':0,'column':0,'row_span':2,'column_span':2},
                  {'op':'TABLE_START'},{'op':'HEADER_FOOTER','zone':'footer'},
                  {'op':'TYPE','text':'한A😀'},{'op':'PAGE_FIELD','kind':'PAGE'},{'op':'BODY'}]
        executor.run(commands,.0005)
        self.assertEqual(sent,['한','A','😀'])
        self.assertIn(('color','cell','E8EFF5'),events)
        self.assertIn(('width',1,2),events)
        self.assertIn(('field','PAGE'),events)
        self.assertIn(('sequence',[17,18],[79,70]),events)

class UIAdapterTests(unittest.TestCase):
    def width_adapter(self,context):
        browser=DocsBrowser()
        writes=[]
        browser.table_cell_rect=lambda *args:{'x':1,'y':1}
        browser.click_point=lambda *args:None
        browser.menu_action=lambda *args:None
        browser.named_control=lambda names,kind='button':{'x':1,'y':1,'context':context}
        browser.write_control=lambda control,value,*args:writes.append(float(value))
        return browser,writes

    def test_centimetres_and_inches_are_not_confused(self):
        for context,expected in [('Column width (cm)',5.08),('Column width (inches)',2)]:
            browser,writes=self.width_adapter(context)
            browser.column_width(1,2,lambda c:None,lambda *k:None,lambda:None,lambda s:None)
            self.assertEqual(writes,[expected])

    def test_unknown_units_stop_before_writing(self):
        browser,writes=self.width_adapter('Column width')
        with self.assertRaises(RuntimeError):
            browser.column_width(1,2,lambda c:None,lambda *k:None,lambda:None,lambda s:None)
        self.assertFalse(writes)

    def test_rgb_validation_rejects_an_invalid_color(self):
        with self.assertRaises(RuntimeError):
            DocsBrowser().set_color('text','not-a-color',None,None,None,None)

    def test_generated_ui_javascript_is_syntactically_valid(self):
        expressions=[]
        class Capture(DocsBrowser):
            def evaluate(self,code):
                expressions.append(code)
                return {'x':1,'y':1,'left':1,'right':10,'top':1,'bottom':10,'step':20}
            def call(self,*args):return {}
        browser=Capture()
        browser.control('#x')
        browser.named_control(['Column width'],'input')
        browser.table_cell_rect(0,1)
        browser.set_color('text','BB1122',lambda c:None,lambda *k:None,lambda:None,lambda s:None)
        browser.picker_state()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'expressions.json'
            path.write_text(json.dumps(expressions),encoding='utf8')
            result=subprocess.run(['node','-e',"const fs=require('fs'); for(const code of JSON.parse(fs.readFileSync(process.argv[1],'utf8'))) new Function('return ('+code+')');",str(path)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':
    unittest.main()
