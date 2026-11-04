#!/usr/bin/env python3
"""Run TXD channel-mask host regressions using the existing local Catch2 libraries."""
from pathlib import Path
import subprocess
import sys
r = Path(__file__).resolve().parents[1]
c = r/'Eglantine'
b = r/'.local/nx2-core-check'
import tempfile
work = tempfile.TemporaryDirectory(prefix='gradient-texture-masks-')
scratch = Path(work.name)
files = ['ampere_gradient_texture.cpp']
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
