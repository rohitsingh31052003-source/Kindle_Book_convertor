#!/usr/bin/env python3
import pathlib
p = pathlib.Path('tests/test_regression_e2e.py')
lines = p.read_text(encoding='utf-8').splitlines(True)
print('total', len(lines))
start = 310
print('START around', start)
for i in range(start, max(start, len(lines)-5)):
    print(i+1, repr(lines[i]))
