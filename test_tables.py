import unittest
from docs_browser import DocsBrowser
from docs_plan import DocsExecutor,compile_document
from docx_reader import Document,Table,Cell,Paragraph,Run

class PickerBrowser(DocsBrowser):
    def __init__(self):
        super().__init__()
        self.rows=1
        self.columns=1
        self.visible=False
        self.inserted=False
        self.keys=[]
        self.ignore_hover=False
        self.clicks=[]
        self.position=0
    def click(self,selector):
        assert selector=='#docs-insert-menu'
    def evaluate(self,expression):return {'x':10,'y':10}
    def call(self,method,params):
        if method=='Input.dispatchMouseEvent':
            self.visible=True
            if not self.ignore_hover and params['x']>=100 and params['y']>=100:
                self.columns=int((params['x']-100)//18)+1
                self.rows=int((params['y']-100)//18)+1
        return {}
    def click_point(self,x,y):
        self.clicks.append((x,y))
        self.visible=False
        self.inserted=True
    def picker_state(self):
        return {'rows':self.rows,'columns':self.columns,'hit_left':100,'hit_top':100,
                'cell_width':18,'cell_height':18,'visible_columns':11,'viewport_width':1200,'viewport_height':900} if self.visible else None
    def key(self,*keys):
        self.keys.append(keys)
        if keys[-1] in (37,38,39,40):
            raise AssertionError('Arrow keys would move the top menubar, not the dimension picker')
        if keys==(9,):
            self.position+=1
            if self.position>=self.rows*self.columns:self.rows+=1
        elif keys==(16,9):self.position-=1
    def in_table(self,pause,chord):return self.inserted

class TablePickerTests(unittest.TestCase):
    def test_measured_pointer_selects_exact_dimensions_without_arrow_keys(self):
        browser=PickerBrowser()
        browser.table_picker(4,3,lambda seconds:None,browser.key)
        self.assertEqual((browser.rows,browser.columns),(4,3))
        self.assertEqual(browser.clicks,[(145,109)])
        self.assertEqual(browser.keys.count((9,)),11)
        self.assertEqual(browser.keys.count((16,9)),11)
        self.assertEqual(browser.position,0)
    def test_picker_can_reduce_a_previous_selection(self):
        browser=PickerBrowser()
        browser.rows=8;browser.columns=7
        browser.table_picker(2,3,lambda seconds:None,browser.key)
        self.assertEqual((browser.rows,browser.columns),(2,3))
    def test_no_progress_never_commits_an_unknown_table_size(self):
        browser=PickerBrowser()
        browser.ignore_hover=True
        with self.assertRaises(RuntimeError):
            browser.table_picker(2,3,lambda seconds:None,browser.key)
        self.assertFalse(browser.clicks)
    def test_size_selection_alone_is_not_reported_as_a_created_table(self):
        browser=PickerBrowser()
        browser.in_table=lambda *args:False
        with self.assertRaisesRegex(RuntimeError,'실제 표 셀'):
            browser.table_picker(2,3,lambda seconds:None,browser.key)
    def test_target_follows_real_cell_size_instead_of_assuming_twenty_pixels(self):
        from docs_browser import table_picker_target
        for scale in (.75,1,1.25,1.5):
            state={'hit_left':100,'hit_top':200,'cell_width':18*scale,'cell_height':18*scale}
            x,y=table_picker_target(2,3,state)
            self.assertAlmostEqual(x,100+45*scale)
            self.assertAlmostEqual(y,200+27*scale)
    def test_unknown_geometry_is_not_replaced_with_guessed_pixels(self):
        from docs_browser import table_picker_target
        with self.assertRaises(RuntimeError):table_picker_target(2,3,{})

class CellRoutingTests(unittest.TestCase):
    def run_table(self,merge_succeeds):
        sent,keys,warnings=[],[],[]
        class Browser:
            def document_active(self):return True
            def table_picker(self,*args):pass
            def ensure_table_shortcuts(self,*args):pass
            def merge_cells(self,*args):
                if not merge_succeeds:raise RuntimeError('merge UI missing')
            def restore_editor(self,*args):pass
            def set_color(self,*args):pass
            def set_font_size(self,*args):pass
        table=Table(rows=[[[]],[[]]],columns=3,cells=[
            Cell(0,0,[Paragraph([Run('A')])],row_span=2,column_span=2),
            Cell(0,2,[Paragraph([Run('B')])]),Cell(1,2,[Paragraph([Run('C')])])])
        engine=DocsExecutor(Browser(),sent.append,lambda *k:keys.append(k),lambda:None,lambda s:None,lambda *a:None,
                            lambda mods,taps:None,warn=warnings.append,continue_on_error=True)
        engine.run(compile_document(Document(blocks=[table])),.01)
        return engine,sent,keys,warnings
    def test_merge_failure_keeps_real_table_and_routes_around_unmerged_gaps(self):
        engine,sent,keys,warnings=self.run_table(False)
        self.assertFalse(engine.flat_table)
        self.assertEqual(sent,['A','B','C'])
        self.assertEqual(keys.count((9,)),5)
        self.assertTrue(any('실제 표는 유지' in w for w in warnings))
        self.assertFalse(any('일반 글로' in w for w in warnings))
    def test_successful_merge_skips_covered_cells(self):
        engine,sent,keys,warnings=self.run_table(True)
        self.assertEqual(sent,['A','B','C'])
        self.assertEqual(keys.count((9,)),2)
        self.assertFalse(warnings)

if __name__=='__main__':unittest.main()
