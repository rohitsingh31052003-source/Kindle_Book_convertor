#!/usr/bin/env python3
import pathlib
p = pathlib.Path('tests/test_regression_e2e.py')
raw = p.read_bytes()
idx = 0
for _ in range(228):
    idx = raw.index(b'\n', idx) + 1
print('bytes at line 228 start:', raw[idx-2:idx+20])
# Also check what comes after line 227's content
# Find line 228 in text
txt = raw.decode('utf-8', errors='replace')
lines = txt.splitlines(True)
print('line 228 repr:', repr(lines[227]))
print('line 229 repr:', repr(lines[228]))
