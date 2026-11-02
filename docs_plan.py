"""Typed content and editor commands are explicitly separate."""
from docx_reader import Paragraph, Table, Cell

def compile_document(document):
    commands = []
    def paragraph(p):
        commands.append({'op': 'ALIGN', 'value': p.alignment})
        if p.spacing:
            commands.append({'op':'PARAGRAPH_SPACING','value':p.spacing})
        for run in p.runs:
            commands.append({'op': 'STYLE', 'value': run.style})
            if run.field_code:
                commands.append({'op':'PAGE_FIELD','kind':run.field_code})
            elif run.text:
                commands.append({'op': 'TYPE', 'text': run.text})
    for i, block in enumerate(document.blocks):
        if isinstance(block, Paragraph):
            paragraph(block)
            if i != len(document.blocks)-1:
                commands.append({'op': 'ENTER'})
        elif isinstance(block, Table):
            rows = len(block.rows)
            columns = block.columns or (len(block.rows[0]) if rows else 0)
            if not rows or not columns or (not block.cells and any(len(r) != columns for r in block.rows)):
                raise ValueError('행마다 열 수가 다르거나 비어 있는 표는 지원하지 않습니다.')
            commands.append({'op': 'TABLE', 'rows': rows, 'columns': columns})
            for column,width in enumerate(block.widths):
                if width > 0:
                    commands.append({'op':'COLUMN_WIDTH','column':column,'inches':width/1440})
            cells = block.cells or [Cell(ri,ci,paragraphs) for ri,row in enumerate(block.rows) for ci,paragraphs in enumerate(row)]
            merges = [c for c in cells if c.row_span>1 or c.column_span>1]
            for cell in sorted(merges,key=lambda c:(c.row,c.column),reverse=True):
                commands.append({'op':'MERGE_CELLS','row':cell.row,'column':cell.column,'row_span':cell.row_span,'column_span':cell.column_span})
            if merges or block.widths:
                commands.append({'op':'TABLE_START'})
            for cell_index, cell in enumerate(cells):
                commands.append({'op': 'CELL', 'index': cell_index,'row':cell.row,'column':cell.column})
                commands.append({'op':'CELL_BACKGROUND','color':cell.background})
                for p_index, p in enumerate(cell.paragraphs):
                    paragraph(p)
                    if p_index != len(cell.paragraphs)-1:
                        commands.append({'op': 'ENTER'})
                if cell_index != len(cells)-1:
                    commands.append({'op': 'NEXT_CELL'})
            commands.append({'op': 'EXIT_TABLE'})
    for zone,paragraphs in [('header',document.headers),('footer',document.footers)]:
        if paragraphs:
            commands.append({'op':'HEADER_FOOTER','zone':zone})
            for i,p in enumerate(paragraphs):
                paragraph(p)
                if i<len(paragraphs)-1:
                    commands.append({'op':'ENTER'})
            commands.append({'op':'BODY'})
    return commands

def plan_summary(commands):
    return {'typed_characters': sum(len(c.get('text', '')) for c in commands),
            'tables': sum(c['op'] == 'TABLE' for c in commands), 'commands': len(commands),
            'merged_ranges':sum(c['op']=='MERGE_CELLS' for c in commands),
            'page_fields':sum(c['op']=='PAGE_FIELD' for c in commands)}

class Cancelled(Exception):
    pass

class RecoveryFailed(RuntimeError):
    pass

class DocsExecutor:
    def __init__(self, browser, send_character, chord, check, pause, progress, sequence=None, warn=None, continue_on_error=False):
        self.browser = browser
        self.send_character = send_character
        self.chord = chord
        self.check = check
        self.pause = pause
        self.progress = progress
        self.sent = 0
        self.style = {}
        self.sequence = sequence
        self.strike = False
        self.warn=warn or (lambda message:None)
        self.continue_on_error=continue_on_error
        self.warnings=[]
        self.flat_table=False
        self.flat_row=None
        self.character_state=None
        self.failed_style_features=set()
        self.warning_signatures=set()
        self.disabled_ops=set()
        self.table_rows=0
        self.table_columns=0
        self.table_position=0
        self.successful_merges=[]
        self.table_navigation=True

    def visible_table_cells(self):
        covered=set()
        for merge in self.successful_merges:
            for row in range(merge['row'],merge['row']+merge['row_span']):
                for column in range(merge['column'],merge['column']+merge['column_span']):
                    if (row,column)!=(merge['row'],merge['column']):
                        covered.add((row,column))
        return [(row,column) for row in range(self.table_rows) for column in range(self.table_columns) if (row,column) not in covered]

    def move_to_cell(self,row,column):
        cells=self.visible_table_cells()
        if (row,column) not in cells:
            raise RuntimeError('현재 표에서 대상 셀 좌표를 찾지 못했습니다.')
        target=cells.index((row,column))
        while self.table_position!=target:
            self.check()
            forward=self.table_position<target
            self.chord(*((9,) if forward else (16,9)))
            self.table_position+=1 if forward else -1
            self.pause(.08)

    def warning_once(self, signature, message):
        if signature not in self.warning_signatures:
            self.warning_signatures.add(signature)
            self.warnings.append(message)
            self.warn(message)

    def style_feature(self, key, action):
        if key in self.failed_style_features:
            return False
        self.check()
        try:
            action()
            return True
        except Cancelled:
            raise
        except Exception as error:
            if not self.continue_on_error:
                raise
            self.failed_style_features.add(key)
            self.warning_once(('style',key),f'서식 {key}: {error} — 이 기능만 건너뛰고 나머지 서식과 타이핑은 계속합니다.')
            try:
                self.browser.restore_editor(self.chord,self.check,self.pause)
            except Cancelled:
                raise
            except Exception as recovery_error:
                raise RecoveryFailed(str(recovery_error)) from recovery_error
            return False

    def field(self, selector, value):
        self.check()
        self.browser.click(selector)
        self.chord(17, 65)
        for char in str(value):
            self.check()
            self.send_character(char)
        self.chord(13)
        self.pause(.25)

    def apply_style(self, style):
        # Establish a known insertion-format state once per cell/zone. Google
        # Docs' documented clear-format shortcut avoids guessing toggle state
        # from a particular toolbar DOM id or ARIA implementation.
        if self.character_state is None:
            self.chord(17,220)
            self.pause(.08)
            self.character_state={key:False for key in ('bold','italic','underline','strike')}
            self.style={}
        for prop, keys in [('bold',(17,66)),('italic',(17,73)),('underline',(17,85)),('strike',(18,16,53))]:
            wanted = bool(style.get(prop))
            if self.character_state[prop] != wanted:
                self.chord(*keys)
                self.pause(.08)
                self.character_state[prop]=wanted
        font=style.get('font')
        if font and self.style.get('font')!=font:
            if self.style_feature(('font',font),lambda:self.browser.set_font(font,self.send_character,self.chord,self.check,self.pause)):
                self.style['font']=font
        size = style.get('size', 11)
        if self.style.get('size')!=size:
            if self.style_feature(('size',),lambda:self.browser.set_font_size(size,self.send_character,self.chord,self.check,self.pause)):
                self.style['size']=size
        color=style.get('color','000000').upper()
        if self.style.get('color') != color:
            if self.style_feature(('color',color),lambda:self.browser.set_color('text',color,self.send_character,self.chord,self.check,self.pause)):
                self.style['color']=color

    def run(self, commands, interval):
        total=sum(len(c.get('text','')) for c in commands)
        for index,command in enumerate(commands):
            self.check()
            if not self.browser.document_active():
                raise Cancelled('브라우저 문서 또는 탭이 바뀌어 중단했습니다.')
            op=command['op']
            self.progress(self.sent,total,index,len(commands),op)
            if op in self.disabled_ops:
                continue
            if op=='TABLE':
                self.flat_table=False
                self.flat_row=None
                self.table_rows=command['rows']
                self.table_columns=command['columns']
                self.table_position=0
                self.successful_merges=[]
            try:
                self.execute(command,interval,index,len(commands),total)
            except (Cancelled,RecoveryFailed):
                raise
            except Exception as error:
                if not self.continue_on_error or op=='TYPE':
                    raise
                self.check()
                message=f'명령 {index+1} {op}: {error} — 해당 서식/기능을 건너뛰고 계속합니다.'
                self.warning_once((op,str(error)),message)
                self.browser.restore_editor(self.chord,self.check,self.pause)
                self.style={}
                self.character_state=None
                if op in ('PARAGRAPH_SPACING','COLUMN_WIDTH','CELL_BACKGROUND','PAGE_FIELD'):
                    self.disabled_ops.add(op)
                if op=='MERGE_CELLS':
                    self.warning_once(('merge_keeps_table',),'병합만 건너뛰고 실제 표는 유지합니다. 셀 좌표에 맞춰 내용을 계속 입력합니다.')
                if op=='TABLE':
                    self.flat_table=True
                    self.flat_row=None
                    fallback='표 구조를 적용하지 못해 셀 내용을 탭/줄바꿈으로 구분한 일반 글로 계속 입력합니다.'
                    self.warning_once(('flat_table',),fallback)
        return self.sent

    def execute(self, command, interval, index, count, total):
        op=command['op']
        if self.flat_table:
            if op=='CELL':
                row=command.get('row',0)
                if self.flat_row is not None:
                    if row!=self.flat_row:
                        self.chord(16,13)
                    else:
                        self.send_character('\t')
                self.flat_row=row
                return
            if op in ('CELL_BACKGROUND','COLUMN_WIDTH','MERGE_CELLS','TABLE_START','NEXT_CELL'):
                return
            if op=='EXIT_TABLE':
                self.flat_table=False
                self.chord(13)
                return
        if op == 'TYPE':
            for char in command['text']:
                self.check()
                if char == '\t':
                    # Send a Unicode tab character, never Ctrl+Tab (browser tab switch).
                    self.send_character(char)
                elif char == '\n':
                    self.chord(16, 13)
                else:
                    self.send_character(char)
                self.sent += 1
                self.progress(self.sent, total, index, count, op)
                self.pause(interval)
        elif op == 'STYLE':
            self.apply_style(command['value'])
        elif op == 'PARAGRAPH_SPACING':
            self.browser.paragraph_spacing(command['value'],self.send_character,self.chord,self.check,self.pause)
        elif op == 'CELL_BACKGROUND':
            self.browser.set_color('cell',command['color'],self.send_character,self.chord,self.check,self.pause)
        elif op == 'COLUMN_WIDTH':
            if not self.table_navigation:
                return
            self.browser.column_width(command['column'],command['inches'],self.send_character,self.chord,self.check,self.pause)
        elif op == 'MERGE_CELLS':
            if not self.table_navigation:
                return
            self.browser.merge_cells(command,self.send_character,self.chord,self.check,self.pause)
            self.successful_merges.append(command)
        elif op == 'TABLE_START':
            if not self.table_navigation:
                return
            if self.sequence is None:
                raise RuntimeError('연속 단축키 입력 백엔드가 없습니다.')
            self.sequence([17,18,16],[84,83])
            self.pause(.2)
            self.style={}
            self.strike=False
            self.character_state=None
            self.table_position=0
        elif op == 'HEADER_FOOTER':
            if self.sequence is None:
                raise RuntimeError('연속 단축키 입력 백엔드가 없습니다.')
            self.sequence([17,18],[79,72 if command['zone']=='header' else 70])
            self.pause(.3)
            self.style={}
            self.strike=False
            self.character_state=None
        elif op == 'PAGE_FIELD':
            self.browser.page_field(command['kind'],self.send_character,self.chord,self.check,self.pause)
            self.style={}
        elif op == 'BODY':
            self.chord(27)
            self.chord(17,35)
            self.pause(.2)
            self.style={}
            self.character_state=None
        elif op == 'ALIGN':
            align = command['value']
            key = {'left': 76, 'center': 69, 'right': 82, 'both': 74, 'justify': 74}.get(align)
            if key is None:
                raise RuntimeError('지원하지 않는 문단 정렬: ' + align)
            self.chord(17, 16, key)
            self.pause(.08)
        elif op == 'ENTER':
            self.chord(13)
            self.pause(.08)
        elif op == 'TABLE':
            self.browser.table_picker(command['rows'], command['columns'], self.pause,self.chord)
            try:
                self.browser.ensure_table_shortcuts(self.chord,self.pause)
                self.table_navigation=True
            except Cancelled:
                raise
            except Exception as error:
                self.table_navigation=False
                self.warning_once(('table_navigation',),f'{error} — 실제 표와 셀 입력은 유지하고 너비/병합만 건너뜁니다.')
            self.style = {}
            self.character_state=None
        elif op == 'NEXT_CELL':
            # CELL carries an absolute logical row/column. It performs all
            # movement, including gaps left by a skipped merge.
            self.style = {}
            self.character_state=None
        elif op == 'CELL':
            self.move_to_cell(command.get('row',0),command.get('column',0))
            self.style={}
            self.character_state=None
        elif op == 'EXIT_TABLE':
            if not self.table_navigation:
                self.browser.exit_table_without_shortcuts(self.chord,self.pause)
                self.style={}
                self.character_state=None
                return
            # Google Docs documented chord: hold Ctrl+Alt+Shift, T then E.
            if self.sequence is None:
                raise RuntimeError('연속 단축키 입력 백엔드가 없습니다.')
            self.sequence([17, 18, 16], [84, 69])
            self.pause(.2)
            self.style = {}
            self.character_state=None
        elif op != 'CELL':
            raise RuntimeError('알 수 없는 실행 명령: ' + op)
