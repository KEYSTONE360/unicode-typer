"""Translate the official Windows shortcut notation into native key events."""
import re

KEYS = {'enter':13, 'return':13, 'tab':9, 'space':32, 'esc':27, 'escape':27,
        'home':36, 'end':35, 'page up':33, 'page down':34, 'backspace':8,
        'delete':46, 'left arrow':37, 'up arrow':38, 'right arrow':39, 'down arrow':40,
        'left':37, 'up':38, 'right':39, 'down':40, '/':191, '\\':220,
        '[':219, ']':221, '.':190, ',':188, '-':189, '+':187, '=':187,
        "'":222, ';':186, 'pg down':34, 'pg up':33}
MODIFIERS = {'ctrl':17, 'control':17, 'alt':18, 'shift':16}

def keycode(value):
    value = value.strip().lower()
    if value in KEYS:
        return KEYS[value]
    if re.fullmatch(r'[a-z0-9]', value):
        return ord(value.upper())
    if re.fullmatch(r'f(?:[1-9]|1[0-2])', value):
        return 111 + int(value[1:])
    raise ValueError('지원하지 않는 키 표기: ' + value)

def parse_shortcut(notation):
    """Return modifier keys held throughout a sequence, and keys to tap."""
    text = re.sub(r'\s+', ' ', notation.strip()).lower()
    text = re.sub(r',\s*press\s+', ', press ', text)
    # Where several alternatives are documented, use Chrome / first alternative.
    if 'in google chrome:' in text:
        text = text.split('in google chrome:',1)[1].split('other browsers:',1)[0].strip()
    elif 'google chrome:' in text:
        text = text.split('google chrome:',1)[1].strip()
    if 'holding ' in text and ', press ' in text:
        mods, taps = text.split(', press ', 1)
        modifiers = [MODIFIERS[x.strip()] for x in mods.removeprefix('holding ').split('+')]
        taps = re.split(r'\s+(?=ctrl\s*\+)', taps)[0].strip()
        return modifiers, [keycode(x) for x in taps.split(' then ')]
    if text.startswith('after selecting one section of text, '):
        text = text.removeprefix('after selecting one section of text, ').split('. use ')[0]
        return parse_shortcut(text)
    if text.startswith('holding '):
        return parse_shortcut(text.removeprefix('holding '))
    # Multiple simple alternatives separated by original newlines.
    original = notation.splitlines()[0].strip().lower()
    if original.startswith(('in google chrome:', 'other browsers:')):
        original = original.split(':',1)[1].strip()
    if 'google chrome:' not in text:
        text = original
    if text.endswith('+ +'):
        parts = [x.strip() for x in text[:-1].split('+') if x.strip()] + ['+']
    else:
        parts = [x.strip() for x in text.split('+')]
    if not parts or not parts[-1]:
        raise ValueError('단축키를 해석하지 못했습니다.')
    modifiers = [MODIFIERS[x] for x in parts[:-1]]
    key = keycode(parts[-1])
    if parts[-1] == '+' and 16 not in modifiers:
        modifiers.append(16)
    return modifiers, [key]

def alternatives(notation):
    # Expand documented direction/range alternatives into independently usable commands.
    if '[1-6]' in notation:
        return [(f' {n}', notation.replace('[1-6]',str(n))) for n in range(1,7)]
    for pattern, values in [('Left/right arrow',['Left arrow','Right arrow']),
                            ('Up/down arrow',['Up arrow','Down arrow'])]:
        if pattern.lower() in notation.lower():
            return [(' '+v, re.sub(pattern, v, notation, flags=re.I)) for v in values]
    return [('', notation)]

def command_supported(command):
    # This application deliberately does not offer clipboard copy/cut/paste routes.
    if re.search(r'\b(copy|cut|paste)\b', command['name'], re.I):
        return False, '클립보드 명령은 타이핑 요구에 따라 제외'
    try:
        parse_shortcut(command['shortcut'])
    except (ValueError, KeyError):
        return False, '별도 UI 조작 또는 키 선택 필요'
    return True, '키 전송 구현 · 실제 Docs 검증 전'
