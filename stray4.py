#!/usr/bin/env python3
import pathlib
p = pathlib.Path('tests/test_regression_e2e.py')
raw = p.read_bytes()
lines = raw.split(b'\n')
# Line 228 (index 227) has escaped quotes: 20 20 20 20 5c 22 5c 22 5c 22 0d
# Should be:                20 20 20 20 22 22 22 0d  (unescaped closing docstring)
for i, line in enumerate(lines):
    print(i+1, line[:20].hex(), repr(line[:20]))
    if i >= 228:
        break

