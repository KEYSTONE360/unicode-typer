"""Read WordprocessingML without executing macros or extracting archive paths."""
from dataclasses import dataclass, field
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
import posixpath

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{' + NS['w'] + '}'

def val(node, tag, default=None):
    child = node.find('w:' + tag, NS) if node is not None else None
    return child.get(W + 'val', default) if child is not None else default

def properties(node):
    result = {}
    if node is None:
        return result
    for tag, key in [('b', 'bold'), ('i', 'italic'), ('strike', 'strike')]:
        child = node.find('w:' + tag, NS)
        if child is not None:
            result[key] = child.get(W + 'val', '1') not in ('0', 'false', 'off')
    underline = val(node, 'u')
    if underline is not None:
        result['underline'] = underline != 'none'
    size = val(node, 'sz')
    if size:
        result['size'] = int(size) / 2
    fonts = node.find('w:rFonts', NS)
    if fonts is not None:
        name = fonts.get(W + 'eastAsia') or fonts.get(W + 'ascii') or fonts.get(W + 'hAnsi')
        if name:
            result['font'] = name
    color = val(node, 'color')
    if color and color != 'auto':
        result['color'] = color
    return result

@dataclass
class Run:
    text: str
    style: dict = field(default_factory=dict)
    field_code: str = ''

@dataclass
class Paragraph:
    runs: list = field(default_factory=list)
    alignment: str = 'left'
    style_name: str = ''
    spacing: dict = field(default_factory=dict)

@dataclass
class Cell:
    row: int
    column: int
    paragraphs: list = field(default_factory=list)
    row_span: int = 1
    column_span: int = 1
    background: str = 'FFFFFF'

@dataclass
class Table:
    rows: list = field(default_factory=list)
    widths: list = field(default_factory=list)
    cells: list = field(default_factory=list)
    columns: int = 0

@dataclass
class Document:
    blocks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    source: str = ''
    blockers: list = field(default_factory=list)
    headers: list = field(default_factory=list)
    footers: list = field(default_factory=list)

    def plain_text(self):
        def paragraph(p):
            return ''.join(r.text for r in p.runs)
        out = []
        for block in self.blocks:
            if isinstance(block, Paragraph):
                out.append(paragraph(block))
            else:
                for row in block.rows:
                    out.append('\t'.join('\n'.join(paragraph(p) for p in cell) for cell in row))
        return '\n'.join(out)

    def summary(self):
        tables = [b for b in self.blocks if isinstance(b, Table)]
        return {'characters': len(self.plain_text()), 'tables': len(tables),
                'table_shapes': [(len(t.rows), max((len(r) for r in t.rows), default=0)) for t in tables],
                'warnings': self.warnings, 'unsupported_features': self.blockers,
                'continues_with_warnings':True,
                'header_paragraphs':len(self.headers),'footer_paragraphs':len(self.footers),
                'merged_cells':sum(c.row_span>1 or c.column_span>1 for t in tables for c in t.cells)}

def read_docx(path):
    path = Path(path)
    doc = Document(source=str(path))
    def warn(message):
        if message not in doc.warnings:
            doc.warnings.append(message)
    def block(message):
        if message not in doc.blockers:
            doc.blockers.append(message)
    with zipfile.ZipFile(path) as archive:
        if sum(x.file_size for x in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError('압축 해제 크기가 100MB를 넘는 문서는 지원하지 않습니다.')
        def xml(name):
            try:
                data = archive.read(name)
            except KeyError:
                return None
            if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
                raise ValueError('DTD 및 외부 엔터티를 포함한 문서는 지원하지 않습니다.')
            return ET.fromstring(data)
        root = xml('word/document.xml')
        if root is None:
            raise ValueError('DOCX 본문이 없습니다.')
        styles_root = xml('word/styles.xml')
        styles = {}
        base = {'bold': False, 'italic': False, 'underline': False, 'strike': False, 'size': 11.0, 'font': 'Arial'}
        default_paragraph = None
        if styles_root is not None:
            base.update(properties(styles_root.find('w:docDefaults/w:rPrDefault/w:rPr', NS)))
            for s in styles_root.findall('w:style', NS):
                styles[s.get(W + 'styleId')] = s
                if s.get(W + 'type') == 'paragraph' and s.get(W + 'default') == '1':
                    default_paragraph = s.get(W + 'styleId')
        def chain(style_id):
            seen, result = set(), []
            while style_id in styles and style_id not in seen:
                seen.add(style_id)
                s = styles[style_id]
                result.insert(0, s)
                style_id = val(s, 'basedOn')
            return result
        def parse_paragraph(p):
            pp = p.find('w:pPr', NS)
            style_id = val(pp, 'pStyle', default_paragraph)
            inherited = dict(base)
            alignment = 'left'
            spacing = {'before_pt': 0.0, 'after_pt': 0.0, 'line_multiple': 1.0}
            def read_spacing(props):
                element = props.find('w:spacing', NS) if props is not None else None
                if element is None:
                    return
                for name in ('before','after'):
                    if element.get(W+name) is not None:
                        spacing[name+'_pt'] = int(element.get(W+name))/20
                if element.get(W+'line') is not None:
                    rule = element.get(W+'lineRule','auto')
                    if rule == 'auto':
                        spacing['line_multiple'] = int(element.get(W+'line'))/240
                    else:
                        block('고정 pt/최소 pt 줄 간격은 Google Docs 배수 간격과 동일하게 변환할 수 없습니다.')
            if styles_root is not None:
                read_spacing(styles_root.find('w:docDefaults/w:pPrDefault/w:pPr',NS))
            for s in chain(style_id):
                inherited.update(properties(s.find('w:rPr', NS)))
                alignment = val(s.find('w:pPr', NS), 'jc', alignment)
                read_spacing(s.find('w:pPr',NS))
            alignment = val(pp, 'jc', alignment)
            read_spacing(pp)
            if pp is not None and pp.find('w:numPr', NS) is not None:
                warn('자동 번호/글머리표의 번호 체계는 별도 재현이 필요합니다.')
                block('자동 번호/글머리표 재현은 아직 지원하지 않습니다.')
            result = Paragraph(alignment=alignment, style_name=style_id or '', spacing=spacing)
            field_code, in_field, field_result = '', False, False
            parents={child:parent for parent in p.iter() for child in parent}
            handled_simple=set()
            for r in p.iter(W + 'r'):
                rp = r.find('w:rPr', NS)
                style = dict(inherited)
                for s in chain(val(rp, 'rStyle')):
                    style.update(properties(s.find('w:rPr', NS)))
                style.update(properties(rp))
                style.setdefault('color','000000')
                ancestor=parents.get(r)
                simple=None
                while ancestor is not None and ancestor is not p:
                    if ancestor.tag==W+'fldSimple':
                        simple=ancestor
                        break
                    ancestor=parents.get(ancestor)
                if simple is not None:
                    if simple not in handled_simple:
                        code=(simple.get(W+'instr','').strip().split() or [''])[0].upper()
                        if code in ('PAGE','NUMPAGES'):
                            result.runs.append(Run('',dict(style),code))
                        else:
                            block('지원하지 않는 Word 필드: '+(code or '빈 필드'))
                        handled_simple.add(simple)
                    continue
                text = ''
                for elem in r:
                    if elem.tag == W+'fldChar':
                        kind = elem.get(W+'fldCharType')
                        if kind == 'begin':
                            in_field, field_result, field_code = True, False, ''
                        elif kind == 'separate':
                            field_result = True
                        elif kind == 'end':
                            code = field_code.strip().split()[0].upper() if field_code.strip() else ''
                            if code in ('PAGE','NUMPAGES'):
                                result.runs.append(Run('',dict(style),code))
                            else:
                                block('지원하지 않는 Word 필드: '+(code or '빈 필드'))
                            in_field, field_result, field_code = False, False, ''
                        continue
                    if elem.tag == W+'instrText':
                        field_code += elem.text or ''
                        continue
                    if in_field and field_result:
                        continue
                    if elem.tag == W + 't':
                        text += elem.text or ''
                    elif elem.tag == W + 'tab':
                        text += '\t'
                    elif elem.tag in (W + 'br', W + 'cr'):
                        text += '\n'
                    elif elem.tag in (W + 'drawing', W + 'pict', W + 'object'):
                        warn('이미지/도형/삽입 개체가 있습니다. 키 입력만으로는 재현하지 않습니다.')
                        block('이미지/도형/삽입 개체 재현은 아직 지원하지 않습니다.')
                    elif elem.tag == W + 'footnoteReference':
                        warn('각주가 있습니다. 본문 외 각주 처리가 필요합니다.')
                        block('각주 재현은 아직 지원하지 않습니다.')
                if text:
                    if result.runs and result.runs[-1].style == style and not result.runs[-1].field_code:
                        result.runs[-1].text += text
                    else:
                        result.runs.append(Run(text, style))
            if p.find('.//w:hyperlink', NS) is not None:
                warn('하이퍼링크 주소는 일반 문자 입력으로 보존되지 않습니다.')
                block('하이퍼링크 재현은 아직 지원하지 않습니다.')
            return result
        body = root.find('w:body', NS)
        if body is None:
            raise ValueError('DOCX 본문 구조가 올바르지 않습니다.')
        for elem in body:
            if elem.tag == W + 'p':
                doc.blocks.append(parse_paragraph(elem))
            elif elem.tag == W + 'tbl':
                table = Table()
                for col in elem.findall('w:tblGrid/w:gridCol', NS):
                    table.widths.append(int(col.get(W + 'w', '0')))
                active_merges = {}
                for row_index, tr in enumerate(elem.findall('w:tr', NS)):
                    row = []
                    column = int(val(tr.find('w:trPr',NS),'gridBefore','0'))
                    next_merges = {}
                    for tc in tr.findall('w:tc', NS):
                        span = int(val(tc.find('w:tcPr',NS),'gridSpan','1'))
                        merge = tc.find('w:tcPr/w:vMerge',NS)
                        continuation = merge is not None and merge.get(W+'val','continue') == 'continue'
                        shd = tc.find('w:tcPr/w:shd', NS)
                        fill = shd.get(W+'fill','FFFFFF') if shd is not None else 'FFFFFF'
                        if fill == 'auto':
                            fill = 'FFFFFF'
                        if tc.find('w:tbl', NS) is not None:
                            warn('표 안의 표가 있습니다. 중첩 표 처리가 필요합니다.')
                            block('중첩 표 재현은 아직 지원하지 않습니다.')
                        paragraphs = [parse_paragraph(p) for p in tc.findall('w:p', NS)]
                        row.append(paragraphs)
                        if continuation:
                            owner = active_merges.get((column,span))
                            if owner is None:
                                block('시작 셀이 없는 세로 병합 구조입니다.')
                            else:
                                owner.row_span += 1
                                next_merges[(column,span)] = owner
                                if any(r.text or r.field_code for p in paragraphs for r in p.runs):
                                    block('세로 병합의 이어지는 셀에 별도 내용이 있습니다.')
                        else:
                            cell = Cell(row_index,column,paragraphs,column_span=span,background=fill.upper())
                            table.cells.append(cell)
                            if merge is not None:
                                next_merges[(column,span)] = cell
                        column += span
                    table.columns = max(table.columns,column)
                    active_merges = next_merges
                    table.rows.append(row)
                table.columns = max(table.columns,len(table.widths))
                occupied = set()
                for cell in table.cells:
                    for ri in range(cell.row,cell.row+cell.row_span):
                        for ci in range(cell.column,cell.column+cell.column_span):
                            if (ri,ci) in occupied:
                                block('겹치는 표 병합 구조입니다.')
                            occupied.add((ri,ci))
                for ri in range(len(table.rows)):
                    for ci in range(table.columns):
                        if (ri,ci) not in occupied:
                            table.cells.append(Cell(ri,ci))
                table.cells.sort(key=lambda cell:(cell.row,cell.column))
                doc.blocks.append(table)
            elif elem.tag != W + 'sectPr':
                warn('본문에 별도 처리해야 하는 콘텐츠 컨트롤 또는 추적 변경 구조가 있습니다.')
                block('본문의 콘텐츠 컨트롤 또는 별도 XML 구조를 지원하지 않습니다.')
        relationships = xml('word/_rels/document.xml.rels')
        relations = {} if relationships is None else {e.get('Id'):e for e in relationships}
        sections = body.findall('.//w:sectPr',NS)
        if len(sections) > 1:
            block('구역마다 다른 머리말/바닥글 및 페이지 설정은 아직 지원하지 않습니다.')
        for kind, target in [('header',doc.headers),('footer',doc.footers)]:
            references = [ref for s in sections for ref in s.findall('w:'+kind+'Reference',NS)]
            for ref in references:
                if ref.get(W+'type','default') != 'default':
                    block('첫 페이지/짝수 페이지별 머리말·바닥글은 아직 지원하지 않습니다.')
                    continue
                rid=ref.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                relation=relations.get(rid)
                if relation is None or relation.get('TargetMode') == 'External':
                    block('머리말/바닥글 관계를 안전하게 해석하지 못했습니다.')
                    continue
                relation_path=relation.get('Target','')
                name=posixpath.normpath(relation_path.lstrip('/') if relation_path.startswith('/') else posixpath.join('word',relation_path))
                if not name.startswith('word/'):
                    block('유효하지 않은 머리말/바닥글 경로입니다.')
                    continue
                zone=xml(name)
                if zone is None:
                    block('머리말/바닥글 파일이 없습니다.')
                    continue
                if zone.find('w:tbl',NS) is not None:
                    block('머리말/바닥글 내부 표는 아직 지원하지 않습니다.')
                target.extend(parse_paragraph(p) for p in zone.findall('w:p',NS))
        if root.find('.//w:ins', NS) is not None or root.find('.//w:del', NS) is not None:
            warn('추적 변경이 있습니다. 표시/수락 기준을 확인해야 합니다.')
            block('추적 변경 문서는 지원하지 않습니다.')
        warn('페이지 배치·여백·테두리 및 Google Docs 실제 UI 적용 결과는 별도로 검증해야 합니다.')
    return doc
