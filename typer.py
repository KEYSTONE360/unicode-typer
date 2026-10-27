import ctypes
from ctypes import wintypes as w
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

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

def send_character(char):
    events = []
    if char in '\n\t':
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

class App:
    def __init__(self, root):
        self.root = root
        self.stop = threading.Event()
        self.messages = queue.Queue()
        self.running = False
        root.title('Unicode Typer — 한글 · English · 기호')
        root.geometry('780x700')
        root.minsize(720, 620)
        root.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='자동 타이핑', font=('맑은 고딕', 18, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='① 글 넣기    →    ② 시작 누르기    →    ③ 다른 앱의 입력 위치 클릭', padding=(0, 10)).pack(anchor='w')
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
        self.interval_box = ttk.Spinbox(settings, from_=1, to=5000, textvariable=self.interval, width=7)
        self.interval_box.pack(side='left', padx=8)
        ttk.Button(settings, text='느리게', command=lambda: self.interval.set('150')).pack(side='left', padx=(8, 3))
        ttk.Button(settings, text='보통', command=lambda: self.interval.set('50')).pack(side='left', padx=3)
        ttk.Button(settings, text='빠르게', command=lambda: self.interval.set('10')).pack(side='left', padx=3)
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
        ttk.Label(frame, text='클립보드 붙여넣기 없이 Windows Unicode 키 입력을 전송합니다.\n입력 중 다른 창으로 전환하면 자동 중단합니다. Enter/Tab은 대상 앱의 동작을 실행할 수 있습니다.', foreground='#555555', wraplength=700).pack(anchor='w')
        root.after(20, self.poll)

    def start(self):
        if self.running:
            return
        text = self.editor.get('1.0', 'end-1c').replace('\r\n', '\n').replace('\r', '\n')
        if not text:
            messagebox.showinfo('입력 필요', '타이핑할 글을 입력하십시오.')
            return
        try:
            delay, interval = float(self.delay.get()), float(self.interval.get())
            if not (1 <= delay <= 60 and 1 <= interval <= 5000):
                raise ValueError()
            text.encode('utf-16-le')
        except (ValueError, UnicodeError):
            messagebox.showerror('설정 확인', '대기는 1~60초, 글자 간격은 1~5000ms로 입력하십시오.')
            return
        self.stop.clear()
        self.running = True
        self.progress.configure(value=0)
        for widget in (self.start_button, self.delay_box, self.interval_box):
            widget.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.editor.configure(state='disabled')
        threading.Thread(target=self.type_text, args=(text, delay, interval / 1000), daemon=True).start()

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
                if kind == 'progress':
                    self.progress.configure(value=value)
                    continue
                self.status.set(value)
                if kind == 'done':
                    self.running = False
                    for widget in (self.start_button, self.delay_box, self.interval_box, self.editor):
                        widget.configure(state='normal')
                    self.stop_button.configure(state='disabled')
        except queue.Empty:
            pass
        self.root.after(20, self.poll)

    def close(self):
        self.stop.set()
        self.root.destroy()

if __name__ == '__main__':
    root = tk.Tk()
    root.option_add('*Font', ('맑은 고딕', 10))
    app = App(root)
    root.update_idletasks()
    u.GetAncestor.argtypes = [w.HWND, w.UINT]
    u.GetAncestor.restype = w.HWND
    app.own_handle = u.GetAncestor(root.winfo_id(), 2)
    root.mainloop()
