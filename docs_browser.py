"""Controlled Chrome session. Content is sent by the Windows keyboard backend.

CDP is used only to inspect the editing UI and click its controls, never to
insert a document, assign innerHTML, or paste the body.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import urlopen, build_opener, ProxyHandler
from urllib.parse import urlparse
import websocket
import re
import math

def table_picker_target(rows,columns,state):
    if not (1<=rows<=20 and 1<=columns<=20):
        raise RuntimeError('표 삽입 메뉴는 1~20행/열을 지원합니다.')
    values=[state.get(k) for k in ('hit_left','hit_top','cell_width','cell_height')]
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in values) or min(values[2:])<=0:
        raise RuntimeError('표 선택기의 실제 셀 크기를 측정하지 못했습니다.')
    left,top,width,height=values
    x,y=left+(columns-.5)*width,top+(rows-.5)*height
    if x>=state.get('viewport_width',float('inf')) or y>=state.get('viewport_height',float('inf')):
        raise RuntimeError('요청한 표 크기의 선택 위치가 화면 밖에 있습니다.')
    return x,y

def local_json(port, path):
    opener = build_opener(ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{port}{path}', timeout=2) as response:
        return json.load(response)

class DocsBrowser:
    def __init__(self):
        self.port = None
        self.ws = None
        self.sequence = 0

    def open(self):
        if self.port:
            try:
                local_json(self.port, '/json/version')
                return
            except Exception:
                pass
        paths = [Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
                 Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe',
                 Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe']
        executable = next((p for p in paths if p.is_file()), None)
        if executable is None:
            raise RuntimeError('Chrome 또는 Edge 설치를 찾지 못했습니다.')
        profile = Path(os.environ['LOCALAPPDATA']) / 'UnicodeTyper' / 'DocsBrowser'
        profile.mkdir(parents=True, exist_ok=True)
        active_port=profile/'DevToolsActivePort'
        def existing_port():
            try:
                port=int(active_port.read_text(encoding='utf8').splitlines()[0])
                if not 1<=port<=65535:
                    return None
                local_json(port,'/json/version')
                return port
            except (OSError,ValueError,IndexError):
                return None
        reused=existing_port()
        if reused:
            self.port=reused
            return
        subprocess.Popen([str(executable), f'--user-data-dir={profile}',
                          '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                          '--no-first-run', '--no-default-browser-check',
                          'https://docs.google.com/document/'], close_fds=True)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            ready=existing_port()
            if ready:
                self.port=ready
                return
            time.sleep(.1)
        self.port=None
        raise RuntimeError('전용 Chrome 연결 포트를 확인하지 못했습니다. 이전 버전에서 연 전용 Chrome 창을 닫은 뒤 다시 여십시오.')

    def connect(self):
        if not self.port:
            raise RuntimeError('먼저 Google Docs 브라우저 열기 버튼을 누르십시오.')
        tabs = local_json(self.port, '/json/list')
        candidates = [t for t in tabs if t.get('type') == 'page'
                      and urlparse(t.get('url', '')).hostname == 'docs.google.com'
                      and '/document/d/' in t.get('url', '')
                      and '/edit' in t.get('url', '')]
        if len(candidates) != 1:
            raise RuntimeError('전용 브라우저에서 편집할 Google 문서 탭을 하나만 열어두십시오.')
        self.close()
        self.ws = websocket.create_connection(candidates[0]['webSocketDebuggerUrl'], timeout=5,
                                              suppress_origin=True, http_no_proxy=['127.0.0.1'])
        if not self.evaluate("!!document.querySelector('.kix-appview-editor') && !!document.querySelector('#docs-format-menu')"):
            self.close()
            raise RuntimeError('Google Docs 편집 도구를 찾지 못했습니다. 편집 권한 및 문서 로딩을 확인하십시오.')
        self.url = candidates[0]['url']
        return self.url

    def call(self, method, params=None):
        if not self.ws:
            raise RuntimeError('브라우저 연결이 없습니다.')
        self.sequence += 1
        current = self.sequence
        self.ws.send(json.dumps({'id': current, 'method': method, 'params': params or {}}))
        while True:
            response = json.loads(self.ws.recv())
            if response.get('id') == current:
                if 'error' in response:
                    raise RuntimeError(str(response['error']))
                return response.get('result', {})

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
        if 'exceptionDetails' in result:
            raise RuntimeError('Google Docs UI 상태 조회에 실패했습니다.')
        return result.get('result', {}).get('value')

    def control(self, selector):
        return self.evaluate('''(() => {
            const e=document.querySelector(%s); if(!e) return null;
            const r=e.getBoundingClientRect();
            if(!r.width || !r.height || getComputedStyle(e).visibility==='hidden') return null;
            return {x:r.x+r.width/2,y:r.y+r.height/2,
                    value:e.value??e.getAttribute('aria-label')??'',
                    pressed:e.getAttribute('aria-pressed')??e.getAttribute('aria-checked'),
                    text:(e.textContent||'').trim()};
        })()''' % json.dumps(selector))

    def font_input(self, kind):
        selectors=(['#fontSizeSelect input','input#fontSizeSelect','input[aria-label="Font size"]','input[aria-label="글꼴 크기"]']
                   if kind=='size' else ['#fontFamilySelect input','input#fontFamilySelect'])
        for selector in selectors:
            control=self.control(selector)
            if control:
                control['selector']=selector
                return control
        return self.named_control(['Font size','글꼴 크기','글자 크기'] if kind=='size' else ['Font','글꼴'],'input')

    def set_font_size(self, size, send, chord, check, pause):
        if not 1<=float(size)<=400:
            raise RuntimeError('Google Docs 글자 크기 범위를 벗어났습니다: '+str(size))
        control=self.font_input('size')
        if control:
            try:
                if abs(float(control['value'])-float(size))<.001:
                    return
            except (ValueError,TypeError,KeyError):
                pass
            self.write_control(control,format(size,'g'),send,chord,check,pause,enter=True)
            actual=self.font_input('size')
            if not actual or abs(float(actual['value'])-float(size))>.001:
                raise RuntimeError('글자 크기 적용값을 확인하지 못했습니다.')
        else:
            self.menu_action([format(size,'g'),f'font size {size:g}'],[format(size,'g'),f'글꼴 크기 {size:g}'],send,chord,check,pause)

    def set_font(self, font, send, chord, check, pause):
        control=self.font_input('font')
        if control:
            if control.get('value','').strip().casefold()==font.casefold():
                return
            self.write_control(control,font,send,chord,check,pause,enter=True)
        else:
            # The font selector can be a non-editable menu button in Docs.
            # Official tool finder also supports finding fonts by their name.
            current=self.control('#docs-font-family') or self.control('#fontFamilySelect')
            if current and font.casefold() in current.get('text','').casefold():
                return
            self.menu_action(font,font,send,chord,check,pause)
        current=self.font_input('font') or self.control('#docs-font-family') or self.control('#fontFamilySelect')
        if not current or font.casefold() not in (current.get('value','')+' '+current.get('text','')).casefold():
            raise RuntimeError('원본 글꼴이 없거나 적용을 확인하지 못했습니다: '+font)

    def click_point(self, x, y):
        for kind in ('mouseMoved', 'mousePressed', 'mouseReleased'):
            params = {'type': kind, 'x': x, 'y': y}
            if kind != 'mouseMoved':
                params.update(button='left', clickCount=1)
            self.call('Input.dispatchMouseEvent', params)

    def click(self, selector):
        control = self.control(selector)
        if not control:
            raise RuntimeError('편집 도구가 없거나 숨겨졌습니다: ' + selector)
        self.click_point(control['x'], control['y'])

    def document_active(self):
        return self.evaluate('document.hasFocus() && location.href === ' + json.dumps(self.url))

    def close(self):
        if self.ws:
            self.ws.close()
            self.ws = None

    def named_control(self, names, kind='button'):
        """Resolve exactly one visible semantic UI control; never select by guess."""
        selectors = {'button':'button,[role="button"],[role="menuitem"],[role="option"]',
                     'input':'input:not([type="hidden"]),textarea,[role="spinbutton"]'}
        return self.evaluate('''(() => {
            const names=%s, selector=%s;
            const normalized=s=>(s||'').replace(/\\s+/g,' ').trim().toLowerCase();
            function path(e){const p=[];while(e&&e.nodeType===1){let n=e.tagName.toLowerCase();
                if(e.id){p.unshift('#'+CSS.escape(e.id));break;}
                const siblings=[...e.parentElement?.children||[]].filter(x=>x.tagName===e.tagName);
                p.unshift(n+':nth-of-type('+(siblings.indexOf(e)+1)+')');e=e.parentElement;}return p.join(' > ');}
            const found=[...document.querySelectorAll(selector)].filter(e=>{
                const r=e.getBoundingClientRect();if(!r.width||!r.height||getComputedStyle(e).visibility==='hidden'||e.disabled||e.getAttribute('aria-disabled')==='true')return false;
                const label=[e.getAttribute('aria-label'),e.getAttribute('title'),
                    ...[...e.labels||[]].map(x=>x.textContent),e.tagName==='INPUT'?'':e.textContent,
                    ...(e.getAttribute('aria-labelledby')||'').split(' ').map(id=>document.getElementById(id)?.textContent)]
                    .filter(Boolean).map(normalized);
                return names.some(n=>label.some(s=>s===normalized(n)||s.startsWith(normalized(n)+' (')));
            });
            if(found.length!==1)return null;const e=found[0],r=e.getBoundingClientRect();
            return {x:r.x+r.width/2,y:r.y+r.height/2,selector:path(e),value:e.value||'',
                    context:(e.parentElement?.parentElement?.textContent||'').slice(0,1500)};
        })()''' % (json.dumps(names),json.dumps(selectors[kind])))

    def write_control(self, control, value, send, chord, check, pause, enter=False):
        if not control:
            raise RuntimeError('필수 편집 입력란을 유일하게 식별하지 못했습니다.')
        check()

        self.click_point(control['x'],control['y'])
        chord(17,65)
        for character in str(value):
            check()
            send(character)
        if enter:
            chord(13)
        else:
            chord(9)
        pause(.15)
        actual=self.control(control['selector'])
        if actual is None:
            if enter:
                return
            raise RuntimeError('편집 입력 후 값을 다시 읽지 못했습니다.')
        try:
            matches=abs(float(actual['value'])-float(value))<.001
        except (ValueError,TypeError):
            matches=str(actual['value']).lstrip('#').casefold()==str(value).lstrip('#').casefold()
        if not matches:
            raise RuntimeError('서식 입력값 확인 실패: '+str(value))


    def picker_state(self):
        return self.evaluate('''(() => {
            const grids=[...document.querySelectorAll('.goog-dimension-picker')].filter(e=>{
                const r=e.getBoundingClientRect();return r.width&&r.height&&getComputedStyle(e).visibility!=='hidden';});
            if(grids.length!==1)return null;
            const e=grids[0],r=e.getBoundingClientRect();
            const status=e.querySelector('.goog-dimension-picker-status')||document.querySelector('.goog-dimension-picker-status');
            const text=(status?.textContent||e.getAttribute('aria-valuetext')||'').trim();
            const match=text.match(/(\\d+)\\s*[x×]\\s*(\\d+)/i);
            const unhighlighted=e.querySelector('.goog-dimension-picker-unhighlighted');
            const catcher=e.querySelector('.goog-dimension-picker-mousecatcher');
            let cellWidth=null,cellHeight=null,hit=null;
            if(unhighlighted&&catcher&&/em$/.test(unhighlighted.style.width)&&/em$/.test(unhighlighted.style.height)){
                const u=unhighlighted.getBoundingClientRect();hit=catcher.getBoundingClientRect();
                cellWidth=u.width/Number.parseFloat(unhighlighted.style.width);
                cellHeight=u.height/Number.parseFloat(unhighlighted.style.height);
            }
            return {columns:match?Number(match[1]):null,rows:match?Number(match[2]):null,
                    status:text,left:r.left,top:r.top,cell_width:cellWidth,cell_height:cellHeight,
                    visible_columns:unhighlighted?Number.parseFloat(unhighlighted.style.width):null,
                    hit_left:hit?.left,hit_top:hit?.top,viewport_width:innerWidth,viewport_height:innerHeight};
        })()''')

    def in_table(self, pause, chord):
        chord(16,121)
        pause(.18)
        try:
            return bool(self.evaluate('''(() => {
                return [...document.querySelectorAll('[role="menuitem"]')].some(e=>{
                    const r=e.getBoundingClientRect();
                    return r.width&&r.height&&getComputedStyle(e).visibility!=='hidden'&&e.getAttribute('aria-disabled')!=='true'&&
                        /^(Delete table|표\\s*삭제)(\\s|$)/i.test(e.textContent.trim());
                });
            })()'''))
        finally:
            chord(27)
            pause(.08)

    def ensure_table_shortcuts(self, chord, pause):
        expression="(() => {const e=document.querySelector('#docs-screenreader-menu, #docs-accessibility-menu');return !!e&&!!e.getBoundingClientRect().width})()"
        if not self.evaluate(expression):
            chord(17,18,90)
            pause(.3)
        if not self.evaluate(expression):
            raise RuntimeError('표 이동 단축키를 위한 Docs 스크린리더 지원 상태를 확인하지 못했습니다.')

    def exit_table_without_shortcuts(self, chord, pause):
        # Documents are appended in order. Docs keeps a paragraph after a table.
        chord(17,35)
        pause(.15)
        if not self.in_table(pause,chord):
            return
        chord(40)
        pause(.15)
        if self.in_table(pause,chord):
            raise RuntimeError('표 밖의 본문으로 이동하지 못했습니다.')

    def table_picker(self, rows, columns, pause, chord=None):
        if not (1<=rows<=20 and 1<=columns<=20):
            raise RuntimeError('표 삽입 메뉴는 1~20행/열을 지원합니다.')
        def ui_key(*keys):
            if chord:
                chord(*keys)
                return
            code=keys[-1]
            names={9:'Tab',13:'Enter',27:'Escape',37:'ArrowLeft',38:'ArrowUp',39:'ArrowRight',40:'ArrowDown',121:'F10'}
            name=names.get(code,chr(code).lower() if 65<=code<=90 else '')
            modifiers=sum({16:8,17:2,18:1}[key] for key in keys[:-1])
            for kind in ('rawKeyDown','keyUp'):
                self.call('Input.dispatchKeyEvent',{'type':kind,'key':name,
                         'windowsVirtualKeyCode':code,'modifiers':modifiers})
        self.click('#docs-insert-menu')
        pause(.2)
        item=self.evaluate('''(() => {
            const items=[...document.querySelectorAll('[role="menuitem"]')].filter(e=>{
                const r=e.getBoundingClientRect();return r.width&&r.height&&getComputedStyle(e).visibility!=='hidden'&&
                    /^(Table|표)(\\s|\\(|$)/i.test(e.textContent.trim())&&e.getAttribute('aria-disabled')!=='true';});
            if(items.length!==1)return null;const r=items[0].getBoundingClientRect();
            return {x:r.x+r.width/2,y:r.y+r.height/2};
        })()''')
        if not item:
            raise RuntimeError('사용 가능한 표 삽입 메뉴를 유일하게 찾지 못했습니다.')
        self.call('Input.dispatchMouseEvent',{'type':'mouseMoved',**item})
        pause(.25)
        state=self.picker_state()
        if not state:
            self.click_point(item['x'],item['y'])
            pause(.2)
            state=self.picker_state()
        if not state or state['columns'] is None or state['rows'] is None:
            raise RuntimeError('표 선택기의 행·열 상태를 읽지 못했습니다.')
        # Opening this submenu does not move keyboard focus into the picker.
        # In the live Docs UI, Right moves the top menubar to Format instead.
        # Measure the actual em-sized grid and use trusted pointer events.
        # Only one row is selected in the popup. Large row targets can fall
        # outside the currently visible menu and close it instead of selecting.
        # Tab from the last cell reliably adds the remaining real table rows.
        initial_columns=min(columns,int(state.get('visible_columns') or columns))
        x,y=table_picker_target(1,initial_columns,state)
        self.call('Input.dispatchMouseEvent',{'type':'mouseMoved','x':x,'y':y})
        pause(.15)
        state=self.picker_state()
        if not state:
            raise RuntimeError('표 크기를 선택하는 중 선택기가 닫혔습니다.')
        if (state['rows'],state['columns'])!=(1,initial_columns):
            raise RuntimeError('요청한 표 크기와 실제 선택된 행·열 수가 다릅니다.')
        self.click_point(x,y)
        pause(.3)
        if self.picker_state():
            raise RuntimeError('표 선택기를 확정하지 못했습니다.')
        if not self.in_table(pause,ui_key):
            raise RuntimeError('삽입 후 실제 표 셀에 들어왔는지 확인하지 못했습니다.')
        if initial_columns<columns:
            self.ensure_table_shortcuts(ui_key,pause)
            for _ in range(columns-initial_columns):
                ui_key(16,121)
                pause(.12)
                self.button_named(['Insert column right','오른쪽에 열 1개 삽입','오른쪽에 열 삽입'])
                pause(.12)
            ui_key(17,18,16,84)
            ui_key(17,18,16,83)
            pause(.15)
        total=rows*columns
        if rows>1:
            for step in range(1,total):
                ui_key(9)
                pause(.12 if step%columns==0 else .035)
            for _ in range(total-1):
                ui_key(16,9)
                pause(.035)
            if not self.in_table(pause,ui_key):
                raise RuntimeError('행 확장 후 표 셀 위치를 확인하지 못했습니다.')
    def button_named(self, names):
        control=self.named_control(names)
        if not control:
            raise RuntimeError('편집 버튼을 유일하게 식별하지 못했습니다: '+' / '.join(names))
        self.click_point(control['x'],control['y'])

    def locale(self):
        label=self.evaluate("document.querySelector('#docs-format-menu')?.textContent||''")
        if '서식' in label:
            return 'ko'
        if 'format' in label.casefold():
            return 'en'
        raise RuntimeError('Google Docs 메뉴 언어를 확인하지 못했습니다. 한국어/영어 UI를 사용하십시오.')

    def menu_action(self, en, ko, send, chord, check, pause):
        names=(ko if self.locale()=='ko' else en)
        if isinstance(names,str):
            names=[names]
        for query in names:
            check()
            chord(18,191)
            pause(.25)
            focused=self.evaluate("(() => {const e=document.activeElement;return !!e&&e.tagName==='INPUT'&&e.getBoundingClientRect().width>0})()")
            if not focused:
                chord(27)
                raise RuntimeError('도구 검색 입력란에 포커스되지 않아 중단했습니다.')
            chord(17,65)
            for character in query:
                check()
                send(character)
            pause(.35)
            control=self.named_control([query])
            if control:
                self.click_point(control['x'],control['y'])
                pause(.25)
                return
            chord(27)
            pause(.1)
        raise RuntimeError('정확히 일치하는 도구 검색 결과가 없습니다: '+' / '.join(names))

    def set_color(self, kind, color, send, chord, check, pause):
        color=color.removeprefix('#').upper()
        if not re.fullmatch('[0-9A-F]{6}',color):
            raise RuntimeError('유효하지 않은 RGB 색상: '+color)
        selectors=(['#textColorButton','#fontColorButton'] if kind=='text' else ['#cellBackgroundColorButton','#tableCellBackgroundColorButton'])
        control=next((self.control(s) for s in selectors if self.control(s)),None)
        if control:
            self.click_point(control['x'],control['y'])
        else:
            self.button_named(['Text color','텍스트 색상'] if kind=='text' else ['Cell background color','Background color','셀 배경 색상','셀 배경색','배경 색상'])
        pause(.2)
        swatch=self.evaluate('''(() => {
            const target=%s.toLowerCase(),rgb=target.match(/../g).map(x=>parseInt(x,16));
            const matches=[...document.querySelectorAll('[role="menu"] [data-color],.goog-menu [data-color],[role="dialog"] [data-color],.goog-palette-cell')].filter(e=>{
                const r=e.getBoundingClientRect();if(!r.width||!r.height)return false;
                if((e.getAttribute('data-color')||'').replace('#','').toLowerCase()===target)return true;
                return [...e.querySelectorAll('[style]'),e].some(n=>{
                    const s=getComputedStyle(n).backgroundColor.replace(/\\s/g,'');
                    return s==='rgb('+rgb.join(',')+')';});
            });
            if(matches.length!==1)return null;const r=matches[0].getBoundingClientRect();
            return {x:r.x+r.width/2,y:r.y+r.height/2};
        })()''' % json.dumps(color))
        if swatch:
            self.click_point(swatch['x'],swatch['y'])
            pause(.2)
            return
        self.button_named(['Add a custom color','Custom','맞춤 색상 추가','맞춤'])
        pause(.2)
        field=self.named_control(['Hex','Hex color','Hex color value','16진수','16진수 색상','색상 코드'],'input')
        self.write_control(field,color,send,chord,check,pause)
        self.button_named(['OK','Apply','확인','적용'])
        pause(.2)

    def paragraph_spacing(self, values, send, chord, check, pause):
        self.menu_action('Custom spacing','맞춤 간격',send,chord,check,pause)
        fields=[(['Line spacing','줄 간격'],values.get('line_multiple',1)),
                (['Before','Before paragraph','문단 앞','단락 앞'],values.get('before_pt',0)),
                (['After','After paragraph','문단 뒤','단락 뒤'],values.get('after_pt',0))]
        for labels,value in fields:
            self.write_control(self.named_control(labels,'input'),format(value,'.8g'),send,chord,check,pause)
        self.button_named(['Apply','적용'])
        pause(.2)

    def table_cell_rect(self, row, column):
        # Canvas-only editors may expose no cell boxes. Stop in that case rather
        # than deriving a cell position from text size or unverified pixel offsets.
        rect=self.evaluate('''(() => {
            const ts=[...document.querySelectorAll('.kix-appview-editor table,.kix-appview-editor [role="table"],.kix-tablelayout')]
                .filter(t=>{const r=t.getBoundingClientRect();return r.width&&r.height&&r.bottom>0&&r.top<innerHeight});
            const tables=ts.filter(t=>!ts.some(x=>x!==t&&x.contains(t)));
            if(tables.length!==1)return null;const table=tables[0];
            const rows=[...table.querySelectorAll('tr,[role="row"],.kix-tablerow')];
            let wanted=null;const occupied=new Set();
            for(let ri=0;ri<rows.length;ri++){let ci=0;
              for(const cell of rows[ri].querySelectorAll(':scope > td,:scope > th,:scope > [role="cell"],:scope > [role="gridcell"],:scope > .kix-tablecell')){
                while(occupied.has(ri+','+ci))ci++;
                const rs=Number(cell.rowSpan||cell.getAttribute('aria-rowspan')||1),cs=Number(cell.colSpan||cell.getAttribute('aria-colspan')||1);
                if(%d>=ri&&%d<ri+rs&&%d>=ci&&%d<ci+cs)wanted=cell;
                for(let r=ri;r<ri+rs;r++)for(let c=ci;c<ci+cs;c++)occupied.add(r+','+c);ci+=cs;
              }
            }
            if(!wanted)return null;wanted.scrollIntoView({block:'nearest',inline:'nearest'});
            const r=wanted.getBoundingClientRect();
            if(r.x<0||r.y<0||r.right>innerWidth||r.bottom>innerHeight)return null;
            return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,x:r.x+r.width/2,y:r.y+r.height/2};
        })()''' % (row,row,column,column))
        if not rect:
            raise RuntimeError('현재 표의 셀 위치를 유일하게 식별하지 못했습니다. 캔버스 전용/화면 밖 표를 임의 좌표로 조작하지 않습니다.')
        return rect

    def merge_cells(self, command, send, chord, check, pause):
        first=self.table_cell_rect(command['row'],command['column'])
        last=self.table_cell_rect(command['row']+command['row_span']-1,command['column']+command['column_span']-1)
        check()
        self.call('Input.dispatchMouseEvent',{'type':'mousePressed','button':'left','buttons':1,'clickCount':1,'x':first['left']+3,'y':first['top']+3})
        try:
            self.call('Input.dispatchMouseEvent',{'type':'mouseMoved','buttons':1,'x':last['right']-3,'y':last['bottom']-3})
        finally:
            self.call('Input.dispatchMouseEvent',{'type':'mouseReleased','button':'left','clickCount':1,'x':last['right']-3,'y':last['bottom']-3})
        pause(.2)
        self.menu_action('Merge cells','셀 병합',send,chord,check,pause)

    def column_width(self, column, inches, send, chord, check, pause):
        rect=self.table_cell_rect(0,column)
        self.click_point(rect['x'],rect['y'])
        self.menu_action(['Table options','Table properties'],['표 옵션','표 속성'],send,chord,check,pause)
        control=self.named_control(['Column width','열 너비'],'input')
        if not control:
            self.button_named(['Column','열'])
            pause(.2)
            control=self.named_control(['Column width','열 너비'],'input')
        if not control:
            raise RuntimeError('열 너비 입력란을 찾지 못했습니다.')
        context=control.get('context','').lower()
        if re.search(r'\bcm\b|센티미터',context):
            value=inches*2.54
        elif re.search(r'\bin\b|inch|인치',context):
            value=inches
        else:
            raise RuntimeError('열 너비 UI의 길이 단위를 확인하지 못했습니다. 단위를 추측하지 않고 중단했습니다.')
        self.write_control(control,format(value,'.6g'),send,chord,check,pause)
        close=self.named_control(['Close','Close table options','닫기','표 옵션 닫기'])
        if not close:
            raise RuntimeError('표 옵션 닫기 버튼을 유일하게 식별하지 못했습니다.')
        self.click_point(close['x'],close['y'])
        pause(.2)

    def page_field(self, kind, send, chord, check, pause):
        if kind=='NUMPAGES':
            self.menu_action('Page count','페이지 수',send,chord,check,pause)
            return
        if kind!='PAGE':
            raise RuntimeError('지원하지 않는 페이지 필드: '+kind)
        self.menu_action(['Page number','Page numbers'],['페이지 번호'],send,chord,check,pause)
        # Require a semantically identified footer preset, never a guessed slot.
        self.button_named(['Page numbers in footer, show on first page',
                           'Page numbers at bottom of page, show on first page',
                           '바닥글의 페이지 번호, 첫 페이지에 표시',
                           '페이지 하단에 페이지 번호, 첫 페이지에 표시'])
        pause(.25)

    def restore_editor(self, chord, check, pause):
        """Close transient UI and verify the document event target before resuming."""
        check()
        chord(27)
        pause(.1)
        chord(27)
        pause(.1)
        focused=self.evaluate('''(() => {
            const e=document.activeElement;
            if(!e)return false;
            if(e.tagName==='IFRAME'&&e.classList.contains('docs-texteventtarget-iframe'))return true;
            return !!e.closest('.kix-appview-editor')&&(e.isContentEditable||e.tagName==='TEXTAREA');
        })()''')
        if not focused:
            # Refocus the existing editor event target; never click an arbitrary
            # point that would move the insertion position in the document.
            focused=self.evaluate('''(() => {
                const frame=document.querySelector('iframe.docs-texteventtarget-iframe');
                if(!frame)return false;
                try{const e=frame.contentDocument.querySelector('[contenteditable="true"],textarea');
                    if(!e)return false;frame.focus();e.focus();
                    return document.activeElement===frame;
                }catch{return false;}
            })()''')
        if not focused:
            raise RuntimeError('경고 후 본문 입력 포커스를 복구하지 못했습니다. 다른 입력란으로의 오입력을 막기 위해 중단했습니다.')
        check()
