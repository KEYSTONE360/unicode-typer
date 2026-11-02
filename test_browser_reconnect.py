import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from docs_browser import DocsBrowser

class ReconnectTests(unittest.TestCase):
    def test_reuses_chrome_port_after_app_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            profile=Path(directory)/'UnicodeTyper'/'DocsBrowser'
            profile.mkdir(parents=True)
            (profile/'DevToolsActivePort').write_text('45678\n/devtools/browser/test',encoding='utf8')
            with patch.dict(os.environ,{'LOCALAPPDATA':directory}),patch('docs_browser.local_json',return_value={}) as query,patch('docs_browser.subprocess.Popen') as launch:
                browser=DocsBrowser()
                browser.open()
                self.assertEqual(browser.port,45678)
                query.assert_called_once_with(45678,'/json/version')
                launch.assert_not_called()

    def test_new_browser_uses_the_port_chrome_actually_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            def start(args,**kwargs):
                self.assertIn('--remote-debugging-port=0',args)
                path=Path(directory)/'UnicodeTyper'/'DocsBrowser'/'DevToolsActivePort'
                path.write_text('34567\n/devtools/browser/test',encoding='utf8')
            with patch.dict(os.environ,{'LOCALAPPDATA':directory}),patch('docs_browser.local_json',return_value={}),patch('docs_browser.subprocess.Popen',side_effect=start):
                browser=DocsBrowser()
                browser.open()
                self.assertEqual(browser.port,34567)

if __name__=='__main__':unittest.main()
