import unittest
from docs_plan import DocsExecutor,Cancelled

class WarningContinueTests(unittest.TestCase):
    def executor(self,browser):
        sent,keys,warnings=[],[],[]
        engine=DocsExecutor(browser,sent.append,lambda *k:keys.append(k),lambda:None,lambda s:None,lambda *a:None,
                            lambda m,t:keys.append((tuple(m),tuple(t))),warn=warnings.append,continue_on_error=True)
        return engine,sent,keys,warnings

    def test_format_failure_warns_and_types_remaining_content(self):
        class Browser:
            restored=0
            def document_active(self):return True
            def control(self,selector):return None
            def set_font_size(self,*args):pass
            def set_color(self,*args):raise RuntimeError('color UI unavailable')
            def restore_editor(self,*args):self.restored+=1
        browser=Browser()
        engine,sent,keys,warnings=self.executor(browser)
        engine.run([{'op':'TYPE','text':'A'},{'op':'STYLE','value':{'color':'FF0000'}},{'op':'TYPE','text':'한B'}],.0005)
        self.assertEqual(sent,['A','한','B'])
        self.assertEqual(len(warnings),1)
        self.assertEqual(browser.restored,1)

    def test_failed_table_keeps_every_cell_as_plain_text(self):
        class Browser:
            def document_active(self):return True
            def table_picker(self,*args):raise RuntimeError('table UI unavailable')
            def restore_editor(self,*args):pass
        engine,sent,keys,warnings=self.executor(Browser())
        engine.run([{'op':'TABLE','rows':2,'columns':2},
                    {'op':'CELL','row':0,'column':0},{'op':'TYPE','text':'A'},{'op':'NEXT_CELL'},
                    {'op':'CELL','row':0,'column':1},{'op':'TYPE','text':'B'},{'op':'NEXT_CELL'},
                    {'op':'CELL','row':1,'column':0},{'op':'TYPE','text':'C'},
                    {'op':'EXIT_TABLE'},{'op':'TYPE','text':'D'}],.01)
        self.assertEqual(sent,['A','\t','B','C','D'])
        self.assertIn((16,13),keys)
        self.assertNotIn((9,),keys)
        self.assertEqual(len(warnings),2)

    def test_focus_recovery_failure_stops_before_next_character(self):
        class Browser:
            def document_active(self):return True
            def control(self,selector):return None
            def restore_editor(self,*args):raise RuntimeError('editor focus unavailable')
        engine,sent,keys,warnings=self.executor(Browser())
        with self.assertRaisesRegex(RuntimeError,'editor focus unavailable'):
            engine.run([{'op':'STYLE','value':{}},{'op':'TYPE','text':'must not type into a dialog'}],.01)
        self.assertFalse(sent)
        self.assertEqual(len(warnings),1)

    def test_f8_cancellation_is_never_treated_as_a_warning(self):
        class Browser:
            def document_active(self):return True
            def paragraph_spacing(self,*args):raise Cancelled('F8')
            def restore_editor(self,*args):raise AssertionError('must not recover a user stop')
        engine,sent,keys,warnings=self.executor(Browser())
        with self.assertRaises(Cancelled):
            engine.run([{'op':'PARAGRAPH_SPACING','value':{}},{'op':'TYPE','text':'X'}],.01)
        self.assertFalse(sent)
        self.assertFalse(warnings)

if __name__=='__main__':
    unittest.main()
