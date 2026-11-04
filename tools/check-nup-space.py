from pathlib import Path
import subprocess, tempfile
root = Path(__file__).resolve().parents[1]
code = r"""
#include "nup_space.h"
#include <cassert>
#include <limits>
using namespace Eden::NupSpace;
int main() {
 constexpr std::uint64_t GiB = 1ULL << 30;
 assert(!Insufficient(180*GiB, 30*GiB + (16ULL<<20), 0));
 assert(Insufficient(20*GiB, 30*GiB, 0));
 assert(!Insufficient(std::nullopt, 30*GiB, 0));
 assert(!Insufficient(20*GiB, 30*GiB, 10*GiB));
 assert(Insufficient(20*GiB, 30*GiB, 10*GiB-1));
 assert(!Insufficient(30*GiB, 30*GiB, 0));
 assert(Insufficient(0, 1, 0));
 assert(!Insufficient(std::nullopt, std::numeric_limits<std::uint64_t>::max(), 0));
#ifdef PS5_NATIVE
 assert(!Available("/data"));
#else
 assert(!Available("/this-directory-does-not-exist-nup-space-check"));
 assert(Available("."));
#endif
}
"""
with tempfile.TemporaryDirectory(prefix='nup-space-') as temp:
 p=Path(temp); (p/'check.cpp').write_text(code)
 for flags in ([], ['-DPS5_NATIVE']):
  subprocess.run(['clang++-18','-std=c++20','-fsanitize=address,undefined','-I'+str(root/'headless'),*flags,str(p/'check.cpp'),'-o',str(p/'check')],check=True)
  subprocess.run([str(p/'check')],check=True)
print('NUP space: unknown/native query failure, 180 GiB vs 30 GiB, real shortfall, exact fit, partial-file credit and overflow boundaries PASS')
