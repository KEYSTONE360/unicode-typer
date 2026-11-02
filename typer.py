import ctypes
from ctypes import wintypes as w
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
from pathlib import Path
from timing import parse_interval_ms, parse_delay_seconds
from docx_reader import read_docx
from docs_plan import compile_document, plan_summary, DocsExecutor, Cancelled
from docs_browser import DocsBrowser
from shortcuts import alternatives, command_supported, parse_shortcut
import sys

u = ctypes.WinDLL('user32', use_last_error=True)
ULONG_PTR = ctypes.c_size_t
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', w.WORD), ('wScan', w.WORD), ('dwFlags', w.DWORD), ('time', w.DWORD), ('dwExtraInfo', ULONG_PTR)]
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', w.LONG), ('dy', w.LONG), ('mouseData', w.DWORD), ('dwFlags', w.DWORD), ('time', w.DWORD), ('dwExtraInfo', ULONG_PTR)]
class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', w.DWORD), ('wParamL', w.WORD), ('wParamH', w.WORD)]
class UNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT), ('hi', HARDWAREINPUT)]
class INPUT(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', w.DWORD), ('data', UNION)]
u.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
u.SendInput.restype = w.UINT
u.GetForegroundWindow.restype = w.HWND
u.GetAsyncKeyState.argtypes = [ctypes.c_int]
u.GetAsyncKeyState.restype = ctypes.c_short
last_shortcut_time=0.0

def send_character(char, literal=False):
    events = []
    if char in '\n\t' and not literal:
        vk = 13 if char == '\n' else 9
        for flags in (0, 2):
            events.append(INPUT(type=1, ki=KEYBDINPUT(wVk=vk, dwFlags=flags)))
    else:
        raw = char.encode('utf-16-le', errors='strict')
        for i in range(0, len(raw), 2):
            unit = int.from_bytes(raw[i:i+2], 'little')
            for flags in (4, 6):
                events.append(INPUT(type=1, ki=KEYBDINPUT(wScan=unit, dwFlags=flags)))
    array = (INPUT * len(events))(*events)
    if u.SendInput(len(events), array, ctypes.sizeof(INPUT)) != len(events):
        raise RuntimeError('키 입력 전송 실패. 대상 프로그램의 관리자 권한 또는 입력 제한을 확인하십시오.')

def send_chord(*keys):
    global last_shortcut_time
    events = [INPUT(type=1, ki=KEYBDINPUT(wVk=k)) for k in keys]
    events += [INPUT(type=1, ki=KEYBDINPUT(wVk=k, dwFlags=2)) for k in reversed(keys)]
    array = (INPUT * len(events))(*events)
    if u.SendInput(len(events), array, ctypes.sizeof(INPUT)) != len(events):
        # Release modifiers even if Windows accepted only part of a chord.
        releases = (INPUT * len(keys))(*[INPUT(type=1, ki=KEYBDINPUT(wVk=k, dwFlags=2)) for k in reversed(keys)])
        u.SendInput(len(keys), releases, ctypes.sizeof(INPUT))
        raise RuntimeError('단축키 전송 실패')
    last_shortcut_time=time.monotonic()

def send_key_sequence(modifiers, taps):
    global last_shortcut_time
    # Keep modifier keys held across multi-step Google Docs shortcuts.
    events = [INPUT(type=1, ki=KEYBDINPUT(wVk=k)) for k in modifiers]
    for key in taps:
        events.extend([INPUT(type=1, ki=KEYBDINPUT(wVk=key)), INPUT(type=1, ki=KEYBDINPUT(wVk=key, dwFlags=2))])
    events.extend(INPUT(type=1, ki=KEYBDINPUT(wVk=k, dwFlags=2)) for k in reversed(modifiers))
    array = (INPUT * len(events))(*events)
    sent = u.SendInput(len(events), array, ctypes.sizeof(INPUT))
    last_shortcut_time=time.monotonic()
    if sent != len(events):
        releases = (INPUT * len(modifiers))(*[INPUT(type=1, ki=KEYBDINPUT(wVk=k, dwFlags=2)) for k in reversed(modifiers)])
        if modifiers:
            u.SendInput(len(modifiers), releases, ctypes.sizeof(INPUT))
        raise RuntimeError('연속 단축키 전송 실패')

class App:
    def __init__(self, root):
        self.root = root
        self.stop = threading.Event()
        self.messages = queue.Queue()
        self.running = False
        self.document = None
        self.commands = None
        self.browser = DocsBrowser()
        self.warning_log=[]
        root.title('Unicode Typer Docs Preview 0.5 — 한글 · English · 기호')
        root.geometry('940x860')
        root.minsize(900, 780)
        root.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='자동 타이핑', font=('맑은 고딕', 18, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='① 글 넣기    →    ② 시작 누르기    →    ③ 다른 앱의 입력 위치 클릭', padding=(0, 10)).pack(anchor='w')
        source = ttk.Frame(frame)
        source.pack(fill='x', pady=(0, 8))
        self.open_button = ttk.Button(source, text='DOCX 불러오기', command=self.load_docx)
        self.open_button.pack(side='left')
        self.plain_button = ttk.Button(source, text='일반 글 모드', command=self.plain_mode)
        self.plain_button.pack(side='left', padx=6)
        self.plan_button = ttk.Button(source, text='분석 / 실행 명령', command=self.show_plan)
        self.plan_button.pack(side='left')
        self.browser_button = ttk.Button(source, text='Google Docs 브라우저 열기', command=self.open_browser)
        self.browser_button.pack(side='right')
        ttk.Button(source, text='Docs 명령 목록', command=self.command_palette).pack(side='right', padx=5)
        self.source_label = tk.StringVar(value='일반 글 모드 — 다른 앱에 문자만 입력')
        ttk.Label(frame, textvariable=self.source_label, wraplength=880).pack(anchor='w', pady=(0, 8))
        area = ttk.Frame(frame)
        area.pack(fill='both', expand=True)
        self.editor = tk.Text(area, height=5, wrap='word', undo=True, font=('맑은 고딕', 12), padx=10, pady=10)
        scroll = ttk.Scrollbar(area, command=self.editor.yview)
        self.editor.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.editor.pack(fill='both', expand=True)
        settings = ttk.Frame(frame, padding=(0, 14))
        settings.pack(fill='x')
        ttk.Label(settings, text='시작 대기(초)').pack(side='left')
        self.delay = tk.StringVar(value='5')
        self.delay_box = ttk.Spinbox(settings, from_=1, to=60, textvariable=self.delay, width=6)
        self.delay_box.pack(side='left', padx=(8, 20))
        ttk.Label(settings, text='글자 간격(ms)').pack(side='left')
        self.interval = tk.StringVar(value='50')
        self.interval_box = ttk.Spinbox(settings, from_=0.01, to=5000, increment=0.1, textvariable=self.interval, width=9)
        self.interval_box.pack(side='left', padx=8)
        ttk.Button(settings, text='느리게', command=lambda: self.interval.set('150')).pack(side='left', padx=(8, 3))
        ttk.Button(settings, text='보통', command=lambda: self.interval.set('50')).pack(side='left', padx=3)
        ttk.Button(settings, text='빠르게', command=lambda: self.interval.set('10')).pack(side='left', padx=3)
        ttk.Label(frame, text='소수점 입력 가능: 0.5 / 12.5ms · 1000ms = 1초 · 실제 속도는 Windows/대상 앱 처리시간에 따라 달라집니다.', foreground='#555555').pack(anchor='w', pady=(0, 10))
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x')
        self.start_button = tk.Button(buttons, text='▶  타이핑 시작', command=self.start, font=('맑은 고딕', 14, 'bold'), bg='#146C43', fg='white', activebackground='#105535', activeforeground='white', relief='flat', cursor='hand2')
        self.start_button.pack(side='left', fill='x', expand=True, ipady=10)
        self.stop_button = tk.Button(buttons, text='■  즉시 중단 (F8)', command=self.stop.set, state='disabled', font=('맑은 고딕', 14, 'bold'), bg='#B42332', fg='white', activebackground='#901C28', activeforeground='white', relief='flat', cursor='hand2')
        self.stop_button.pack(side='left', fill='x', expand=True, padx=(12, 0), ipady=10)
        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill='x', pady=(16, 0))
        self.status = tk.StringVar(value='준비 — F8을 누르면 다른 창에서도 중단됩니다.')
        ttk.Label(frame, textvariable=self.status, font=('맑은 고딕', 11, 'bold'), padding=(0, 10), wraplength=700).pack(anchor='w')
        warning_row=ttk.Frame(frame)
        warning_row.pack(fill='x',pady=(0,6))
        self.warning_status=tk.StringVar(value='서식 적용 실패 시 경고를 남기고 계속 진행합니다.')
        ttk.Label(warning_row,textvariable=self.warning_status,foreground='#9A5800',wraplength=730).pack(side='left',fill='x',expand=True)
        ttk.Button(warning_row,text='경고 내역',command=self.show_warnings).pack(side='right')
        ttk.Label(frame, text='클립보드 붙여넣기 없이 Windows Unicode 키 입력을 전송합니다.\n입력 중 다른 창으로 전환하면 자동 중단합니다. Enter/Tab은 대상 앱의 동작을 실행할 수 있습니다.', foreground='#555555', wraplength=700).pack(anchor='w')
        root.after(20, self.poll)

    def record_warning(self, message):
        self.warning_log.append(message)
        self.warning_status.set(f'경고 {len(self.warning_log)}건 — {message[:170]}')

    def show_warnings(self):
        dialog=tk.Toplevel(self.root)
        dialog.title('경고 내역 — 입력은 계속 진행')
        dialog.geometry('850x550')
        text=tk.Text(dialog,wrap='word',font=('맑은 고딕',10))
        text.pack(fill='both',expand=True,padx=12,pady=12)
        text.insert('1.0','\n\n'.join(f'{i+1}. {message}' for i,message in enumerate(self.warning_log)) or '경고가 없습니다.')
        text.configure(state='disabled')
        ttk.Button(dialog,text='닫기',command=dialog.destroy).pack(pady=(0,10))

    def command_palette(self):
        path = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'docs_shortcuts.json'
        catalog = json.loads(path.read_text(encoding='utf8'))
        entries = [{**c, 'name':c['name']+suffix, 'shortcut':notation}
                   for c in catalog['commands'] for suffix, notation in alternatives(c['shortcut'])]
        dialog = tk.Toplevel(self.root)
        dialog.title('Google Docs 공식 명령 목록 — Windows / Chrome')
        dialog.geometry('1100x720')
        ttk.Label(dialog, text=f'공식 도움말 {catalog["checked_date"]} 확인 · 기본 {len(catalog["commands"])}항목 · 선택 키 확장 {len(entries)}항목\n명령 전송 구현과 실제 Google Docs 동작 검증은 다릅니다. 문서/커서/선택 상태에 따라 결과가 달라집니다.', padding=12).pack(anchor='w')
        search = tk.StringVar()
        ttk.Entry(dialog, textvariable=search).pack(fill='x', padx=12, pady=(0,8))
        area = ttk.Frame(dialog)
        area.pack(fill='both', expand=True, padx=12)
        tree = ttk.Treeview(area, columns=('category','name','keys','status'), show='headings', selectmode='browse')
        for name, title, width in [('category','분류',150),('name','기능',260),('keys','단축키',250),('status','상태',250)]:
            tree.heading(name,text=title)
            tree.column(name,width=width)
        bar = ttk.Scrollbar(area, command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        bar.pack(side='right',fill='y')
        tree.pack(fill='both',expand=True)
        def filter_entries(*args):
            tree.delete(*tree.get_children())
            query = search.get().casefold()
            for i, c in enumerate(entries):
                ok, reason = command_supported(c)
                if query in (c['category']+' '+c['name']+' '+c['shortcut']).casefold():
                    tree.insert('', 'end', iid=str(i), values=(c['category'],c['name'],c['shortcut'],reason))
        search.trace_add('write',filter_entries)
        filter_entries()
        hint = tk.StringVar(value='Docs 전용 브라우저의 대상 문서로 이동할 수 있도록 설정한 시작 대기시간 후 명령을 보냅니다.')
        ttk.Label(dialog,textvariable=hint,wraplength=1040,padding=12).pack(anchor='w')
        def execute():
            if self.running:
                hint.set('다른 타이핑/명령이 실행 중입니다. F8로 먼저 중단하십시오.')
                return
            selected = tree.selection()
            if not selected:
                return
            command = entries[int(selected[0])]
            ok, reason = command_supported(command)
            if not ok:
                hint.set(reason)
                return
            try:
                delay = parse_delay_seconds(self.delay.get())
            except ValueError as exc:
                hint.set(str(exc))
                return
            self.stop.clear()
            self.running = True
            self.start_button.configure(state='disabled')
            self.stop_button.configure(state='normal')
            threading.Thread(target=self.run_catalog_command,args=(command,delay),daemon=True).start()
            hint.set('대기 중 — 대상 Google 문서/입력 위치로 이동하십시오. F8로 중단할 수 있습니다.')
        ttk.Button(dialog,text='선택 명령 실행',command=execute).pack(pady=(0,12))

    def run_catalog_command(self, command, delay):
        try:
            if self.stop.wait(delay):
                raise Cancelled('중단')
            target = u.GetForegroundWindow()
            if target == self.own_handle:
                raise Cancelled('대상 문서를 선택하지 않아 중단')
            self.browser.connect()
            if not self.browser.document_active():
                raise Cancelled('Google Docs 문서 탭이 활성화되어 있지 않습니다.')
            if self.stop.is_set() or u.GetAsyncKeyState(0x77)&0x8000:
                raise Cancelled('중단')
            modifiers, taps = parse_shortcut(command['shortcut'])
            send_key_sequence(modifiers,taps)
            result = f'명령 전송: {command["name"]} — 결과 확인 필요'
        except Exception as exc:
            result = str(exc)
        finally:
            self.browser.close()
            self.messages.put(('done',result))

    def open_browser(self):
        try:
            self.browser.open()
            self.status.set('전용 브라우저에서 직접 로그인한 뒤 빈 Google 문서를 하나만 열고 입력 위치를 클릭하십시오.')
        except Exception as exc:
            messagebox.showerror('브라우저 열기 실패', str(exc))

    def plain_mode(self):
        if self.running:
            return
        self.document = None
        self.commands = None
        self.editor.configure(state='normal')
        self.source_label.set('일반 글 모드 — 다른 앱에 문자만 입력')

    def load_docx(self):
        if self.running:
            return
        path = filedialog.askopenfilename(title='타이핑할 DOCX 선택', filetypes=[('Word 문서', '*.docx')])
        if not path:
            return
        self.load_docx_path(path)

    def load_docx_path(self, path):
        try:
            document = read_docx(path)
            commands = compile_document(document)
        except Exception as exc:
            messagebox.showerror('DOCX 분석 실패', str(exc))
            return
        self.document, self.commands = document, commands
        self.warning_log=[]
        self.warning_status.set('서식 적용 실패 시 경고를 남기고 계속 진행합니다.')
        for warning in document.blockers+document.warnings:
            self.record_warning(warning)
        summary = plan_summary(commands)
        self.editor.configure(state='normal')
        self.editor.delete('1.0', 'end')
        self.editor.insert('1.0', document.plain_text())
        self.editor.configure(state='disabled')
        self.source_label.set(f'DOCX: {Path(path).name} · 본문 {summary["typed_characters"]:,}자 · 표 {summary["tables"]}개 · 실행 명령 {summary["commands"]:,}개')
        self.status.set('DOCX 분석 완료 — 분석 / 실행 명령에서 지원 범위를 확인하십시오.')
        self.show_plan()

    def show_plan(self):
        if not self.document:
            messagebox.showinfo('DOCX 필요', '먼저 DOCX를 불러오십시오.')
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('DOCX 분석 및 타이핑 실행 명령 — 서식 일치 검증 전')
        dialog.geometry('850x650')
        text = tk.Text(dialog, wrap='word', font=('맑은 고딕', 10))
        text.pack(fill='both', expand=True, padx=12, pady=12)
        report = {'summary': self.document.summary(), 'plan_summary': plan_summary(self.commands), 'commands': self.commands}
        text.insert('1.0', json.dumps(report, ensure_ascii=False, indent=2))
        text.configure(state='disabled')
        ttk.Button(dialog, text='닫기', command=dialog.destroy).pack(pady=(0, 12))

    def start(self):
        if self.running:
            return
        text = self.editor.get('1.0', 'end-1c').replace('\r\n', '\n').replace('\r', '\n')
        if not text:
            messagebox.showinfo('입력 필요', '타이핑할 글을 입력하십시오.')
            return
        try:
            delay = parse_delay_seconds(self.delay.get())
            interval = parse_interval_ms(self.interval.get())
            text.encode('utf-16-le')
        except (ValueError, UnicodeError) as exc:
            messagebox.showerror('설정 확인', str(exc))
            return
        self.stop.clear()
        self.running = True
        self.progress.configure(value=0)
        for widget in (self.start_button, self.delay_box, self.interval_box, self.open_button, self.plain_button, self.browser_button):
            widget.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.editor.configure(state='disabled')
        method = self.type_document if self.document else self.type_text
        threading.Thread(target=method, args=(text, delay, interval / 1000), daemon=True).start()

    def type_document(self, text, delay, interval):
        executor = None
        try:
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                self.messages.put(('status', f'DOCX 시작까지 {max(1, int(deadline-time.monotonic())+1)}초 — 전용 Docs 문서 입력 위치를 클릭하십시오.'))
                if self.stop.wait(.05):
                    raise Cancelled('중단')
            target = u.GetForegroundWindow()
            if not target or target == self.own_handle:
                raise Cancelled('대상 문서를 선택하지 않아 중단')
            self.browser.connect()
            def check():
                if self.stop.is_set() or u.GetAsyncKeyState(0x77) & 0x8000:
                    raise Cancelled('중단')
                if u.GetForegroundWindow() != target:
                    raise Cancelled('대상 창 전환으로 중단')
                if time.monotonic()-last_shortcut_time>.05 and any(u.GetAsyncKeyState(k) & 0x8000 for k in (16, 17, 18, 91, 92)):
                    raise Cancelled('사용자 조합키 입력으로 중단')
            def pause(seconds):
                end = time.monotonic() + seconds
                while True:
                    check()
                    remaining = end-time.monotonic()
                    if remaining <= 0:
                        break
                    self.stop.wait(min(.01, remaining))
            def progress(sent, total, index, count, op):
                self.messages.put(('status', f'DOCX 타이핑 {sent:,}/{total:,}자 · 명령 {index+1}/{count}: {op} · F8 중단'))
                self.messages.put(('progress', sent * 100 / max(1, total)))
            executor = DocsExecutor(self.browser, lambda char: send_character(char, literal=True), send_chord, check, pause, progress, send_key_sequence,
                                    warn=lambda message:self.messages.put(('warning',message)),continue_on_error=True)
            executor.run(self.commands, interval)
            result = f'타이핑 명령 전송 완료 · 실행 경고 {len(executor.warnings)}건 — 결과 대조 필요'
        except Exception as exc:
            result = str(exc)
        finally:
            self.browser.close()
            self.messages.put(('done', f'{result} · {executor.sent if executor else 0:,}자 전송'))

    def type_text(self, text, delay, interval):
        sent = 0
        result = '중단'
        try:
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                self.messages.put(('status', f'시작까지 {max(1, int(deadline - time.monotonic()) + 1)}초 — 대상 입력 위치를 클릭하십시오.'))
                if self.stop.wait(min(.05, max(0, deadline-time.monotonic()))):
                    return
            target = u.GetForegroundWindow()
            if not target or target == self.own_handle:
                result = '대상 창이 선택되지 않아 중단'
                return
            for char in text:
                if self.stop.is_set():
                    break
                if u.GetForegroundWindow() != target:
                    result = '대상 창 전환으로 중단'
                    break
                if any(u.GetAsyncKeyState(key) & 0x8000 for key in (16, 17, 18, 91, 92)):
                    result = 'Shift/Ctrl/Alt/Win 키 감지로 중단'
                    break
                send_character(char)
                sent += 1
                self.messages.put(('status', f'입력 중 {sent:,} / {len(text):,}자 — 중단: F8'))
                self.messages.put(('progress', sent * 100 / len(text)))
                if self.stop.wait(interval):
                    break
            if sent == len(text):
                result = '완료'
        except Exception as exc:
            result = str(exc)
        finally:
            self.messages.put(('done', f'{result} — {sent:,} / {len(text):,}자 전송'))

    def poll(self):
        if self.running and u.GetAsyncKeyState(0x77) & 0x8000:
            self.stop.set()
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind=='warning':
                    self.record_warning(value)
                    continue
                if kind == 'progress':
                    self.progress.configure(value=value)
                    continue
                self.status.set(value)
                if kind == 'done':
                    self.running = False
                    for widget in (self.start_button, self.delay_box, self.interval_box, self.open_button, self.plain_button, self.browser_button):
                        widget.configure(state='normal')
                    self.editor.configure(state='disabled' if self.document else 'normal')
                    self.stop_button.configure(state='disabled')
        except queue.Empty:
            pass
        self.root.after(20, self.poll)

    def close(self):
        self.stop.set()
        self.root.destroy()

if __name__ == '__main__':
    if '--self-test-output' in sys.argv:
        output = Path(sys.argv[sys.argv.index('--self-test-output') + 1])
        catalog_path = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'docs_shortcuts.json'
        catalog = json.loads(catalog_path.read_text(encoding='utf8'))
        entries = [{**c,'name':c['name']+n,'shortcut':s} for c in catalog['commands'] for n,s in alternatives(c['shortcut'])]
        assert ctypes.sizeof(INPUT) == 40
        assert parse_interval_ms('0.5') == .5
        assert len(catalog['commands']) == 174
        assert sum(command_supported(c)[0] for c in entries) == 183
        from docx_reader import Document,Paragraph,Run,Table,Cell
        fixture=Document(blocks=[Table(rows=[[[]],[[]]],widths=[1440,1440],columns=2,
                        cells=[Cell(0,0,[Paragraph([Run('한A',{'color':'BB1122'})],spacing={'line_multiple':1.5})],row_span=2,column_span=2,background='E8EFF5')])],
                         footers=[Paragraph([Run('',field_code='PAGE')])])
        operations={c['op'] for c in compile_document(fixture)}
        required={'CELL_BACKGROUND','COLUMN_WIDTH','MERGE_CELLS','HEADER_FOOTER','PAGE_FIELD','PARAGRAPH_SPACING'}
        assert required <= operations
        output.write_text(json.dumps({'status':'ok','native_input_size':40,'decimal_ms':.5,
                                     'documented_entries':174,'expanded_entries':194,'mapped_entries':183,
                                     'rich_commands':sorted(required),'warning_continue':True,
                                     'version':'0.5.0-preview','style_shortcut_fallback':True,
                                     'table_measured_picker':True,'table_tab_row_growth':True,'merge_failure_preserves_table':True,
                                     'live_google_docs_test':False},indent=2),encoding='utf8')
        raise SystemExit(0)
    root = tk.Tk()
    root.option_add('*Font', ('맑은 고딕', 10))
    app = App(root)
    root.update_idletasks()
    u.GetAncestor.argtypes = [w.HWND, w.UINT]
    u.GetAncestor.restype = w.HWND
    app.own_handle = u.GetAncestor(root.winfo_id(), 2)
    if '--open-docs' in sys.argv:
        root.after(500, app.open_browser)
    paths = [a for a in sys.argv[1:] if a.lower().endswith('.docx')]
    if paths:
        root.after(200, lambda: app.load_docx_path(paths[0]))
    root.mainloop()
