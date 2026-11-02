import unittest
from docs_plan import DocsExecutor

class NoToolbarBrowser:
    def __init__(self):self.calls=[]
    def document_active(self):return True
    def control(self,*args):raise AssertionError('bold button DOM must not be required')
    def set_font(self,font,*args):self.calls.append(('font',font))
    def set_font_size(self,size,*args):self.calls.append(('size',size))
    def set_color(self,kind,color,*args):self.calls.append(('color',kind,color))
    def restore_editor(self,*args):self.calls.append(('restore',))

class StyleRecoveryTests(unittest.TestCase):
    def engine(self,browser):
        sent,keys,warnings=[],[],[]
        engine=DocsExecutor(browser,sent.append,lambda *k:keys.append(k),lambda:None,lambda s:None,lambda *a:None,
                            warn=warnings.append,continue_on_error=True)
        return engine,sent,keys,warnings

    def test_bold_italic_underline_work_without_toolbar_ids(self):
        engine,sent,keys,warnings=self.engine(NoToolbarBrowser())
        engine.run([{'op':'STYLE','value':{'bold':True,'italic':True,'underline':True,'size':15}},
                    {'op':'TYPE','text':'한A😀'}],.01)
        self.assertIn((17,220),keys)
        for key in (66,73,85):self.assertIn((17,key),keys)
        self.assertEqual(sent,['한','A','😀'])
        self.assertFalse(warnings)

    def test_repeated_same_style_does_not_toggle_bold_off(self):
        engine,sent,keys,warnings=self.engine(NoToolbarBrowser())
        style={'bold':True,'size':15}
        engine.run([{'op':'STYLE','value':style},{'op':'TYPE','text':'A'},
                    {'op':'STYLE','value':style},{'op':'TYPE','text':'B'},
                    {'op':'STYLE','value':{'bold':False,'size':15}},{'op':'TYPE','text':'C'}],.01)
        self.assertEqual(keys.count((17,66)),2)
        self.assertEqual(keys.count((17,220)),1)
        self.assertEqual(sent,['A','B','C'])

    def test_missing_font_does_not_skip_size_color_or_text(self):
        class Browser(NoToolbarBrowser):
            def set_font(self,font,*args):
                self.calls.append(('font',font))
                raise RuntimeError('font not available')
        browser=Browser()
        engine,sent,keys,warnings=self.engine(browser)
        style={'font':'Unavailable Font','bold':True,'size':21,'color':'BB1122'}
        engine.run([{'op':'STYLE','value':style},{'op':'TYPE','text':'A'},
                    {'op':'STYLE','value':style},{'op':'TYPE','text':'B'}],.01)
        self.assertEqual(browser.calls.count(('font','Unavailable Font')),1)
        self.assertIn(('size',21),browser.calls)
        self.assertIn(('color','text','BB1122'),browser.calls)
        self.assertEqual(sent,['A','B'])
        self.assertEqual(len(warnings),1)

    def test_unavailable_paragraph_ui_is_not_retried_for_every_paragraph(self):
        class Browser(NoToolbarBrowser):
            def paragraph_spacing(self,*args):
                self.calls.append(('spacing',))
                raise RuntimeError('dialog missing')
        browser=Browser()
        engine,sent,keys,warnings=self.engine(browser)
        commands=[]
        for char in 'ABCDE':
            commands.extend([{'op':'PARAGRAPH_SPACING','value':{}},{'op':'TYPE','text':char}])
        engine.run(commands,.01)
        self.assertEqual(browser.calls.count(('spacing',)),1)
        self.assertEqual(len(warnings),1)
        self.assertEqual(''.join(sent),'ABCDE')

if __name__=='__main__':unittest.main()
