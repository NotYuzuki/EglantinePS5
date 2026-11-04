#!/usr/bin/env python3
"""Run focused shader host regressions using the existing local Catch2 libraries."""
from pathlib import Path
import subprocess
import sys
r = Path(__file__).resolve().parents[1]
c = r/'Eglantine'
b = r/'.local/nx2-core-check'
import tempfile
work = tempfile.TemporaryDirectory(prefix='shader-conversion-comparison-')
scratch = Path(work.name)
files = ['ampere_i2f64.cpp', 'ampere_uniform_fset.cpp', 'ampere_f2i.cpp',
         'ampere_release_f2i.cpp', 'ampere_uniform_lea.cpp']
args = ['clang++-18', '-std=c++20', '-O1', '-g', '-DARCHITECTURE_x86_64=1', '-pthread',
        '-fsanitize=address,undefined']
includes = [c/'src', b/'_deps/catch2-src/src', b/'_deps/catch2-build/generated-includes']
args += ['-I'+str(p) for p in includes]
args += [str(c/'src/tests/video_core'/p) for p in files]
args += [str(b/'_deps/catch2-build/src/libCatch2Main.a'),
         str(b/'_deps/catch2-build/src/libCatch2.a'), '-o', str(scratch/'check')]
subprocess.run(args, check=True)
result = subprocess.run([str(scratch/'check'), '--reporter', 'compact'], capture_output=True, text=True)
print(result.stdout[-6500:])
print(result.stderr[-1500:])
sys.exit(result.returncode)
