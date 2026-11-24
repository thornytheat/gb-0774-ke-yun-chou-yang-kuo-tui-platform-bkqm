import csv
from io import StringIO

BOM = '\ufeff'


def scrubbed_returns(text, tickets):
    '''从回收原件文本中剔除撤回票的记录行，保留表头分段、空行与其余行的内容。
    列数异常的行无法对齐票号列，只要任一字段命中撤回票即一并剔除，避免答案经隔离清单残留。'''
    tickets = set(tickets)
    reader = csv.reader(StringIO(text.removeprefix(BOM), newline=''), strict=True)
    output = StringIO()
    writer = csv.writer(output, lineterminator='\n')
    header, column, removed = None, 0, 0
    for fields in reader:
        if header is None:
            header = fields
            if 'ticket' not in header:
                return text, 0
            column = header.index('ticket')
            writer.writerow(fields)
            continue
        if fields == header or not any(field.strip() for field in fields):
            writer.writerow(fields)
            continue
        if len(fields) == len(header):
            hit = fields[column].strip() in tickets
        else:
            hit = any(field.strip() in tickets for field in fields)
        if hit:
            removed += 1
            continue
        writer.writerow(fields)
    body = output.getvalue()
    return (BOM + body if text.startswith(BOM) else body), removed


def guarded(tables, tickets):
    '''归档或预览前剔除撤回票行，晚到的补访记录不会重新入样。'''
    if not tickets:
        return tables
    try:
        text, removed = scrubbed_returns(tables['returns'], tickets)
    except csv.Error:
        return tables
    return {**tables, 'returns': text} if removed else tables
