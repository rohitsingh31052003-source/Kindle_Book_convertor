#!/usr/bin/env python3
import pathlib, re
p = pathlib.Path('tests/test_regression_e2e.py')
txt = p.read_text(encoding='utf-8', errors='replace')
lines = txt.splitlines(True)
print('file lines', len(lines))
for i, m in enumerate(re.finditer(r'"""', txt), start=1):
    pos = m.start()
    line_no = txt[:pos].count('\n') + 1
    col = pos - txt[:pos].rfind('\n') - 1
    print(f'""" #{i} at line {line_no} col {col}: {repr(lines[line_no-1].strip()[:50])}')

