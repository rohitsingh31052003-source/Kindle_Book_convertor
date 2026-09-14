#!/usr/bin/env python3
import pathlib, ast
p = pathlib.Path('tests/test_regression_e2e.py')
for enc in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be'):
    try:
        t = p.read_text(encoding=enc)
        ast.parse(t)
        print('OK', enc, 'lines', t.count(chr(10)))
        break
    except Exception as e:
        print('FAIL', enc, type(e).__name__, e)
else:
    raw = p.read_bytes()
    print('RAW first 4 bytes:', raw[:4])
    # try decoding with errors='replace' as a last resort
    t = p.read_text(encoding='utf-8', errors='replace')
    try:
        ast.parse(t)
        print('OK utf-8 replace')
    except SyntaxError as e:
        print('SYNTAX ERROR', e.lineno, e.offset, e.msg, 'char', e.text[:40])
