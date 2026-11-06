#!/usr/bin/env python3
"""Keep the RADV/Mesa implementation private beside the OpenGL Mesa build.

Only defined implementation symbols are renamed, together with their references
inside the archive. Public Vulkan entrypoints and shared C++ runtime identities
remain unchanged. The input release archive is never modified.

The PS5 queue's sync condition is also moved to Mesa's monotonic clock first
(adapt_sync_clock): RADV gives its timed wait a deadline from os_time_get_nano(),
the monotonic clock, but makes the condition with cnd_init, which waits on the
realtime clock. Such a deadline is decades in the past, so the wait of up to a
millisecond for an unsubmitted signal returned at once and the waiting thread spun
on the queue's lock (a 5 ms wait ended after 2-5 us on the console; found in
heydemoura/EglantinePS5#11).
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = __import__('runpy').run_path(str(root/'tools/radv_refs.py'))['radv_refs'](root)/'mihawk-vulkan-review/.deps/native/radv-release/lib/libvulkan_radeon.ps5.a'
# EDEN_RADV_ISOLATED: another output folder (a check that leaves the build's archive alone).
output = Path(os.environ.get('EDEN_RADV_ISOLATED', root/'build/radv-isolated'))
output.mkdir(parents=True, exist_ok=True)

def symbols(path, defined=False):
    cmd = ['nm', '-g', '--format=posix']
    if defined:
        cmd.append('--defined-only')
    result = subprocess.run(cmd+[str(path)], check=True, capture_output=True, text=True)
    return {p[0] for line in result.stdout.splitlines()
            if len(p := line.split()) >= 2 and len(p[1]) == 1}

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def adapt_sync_clock(archive, directory):
    """A copy of the archive whose PS5 queue sync condition uses the monotonic clock.

    Redirecting the condition's three calls to Mesa's u_cnd_monotonic helpers is the same
    as changing its type in radv_ps5_winsys.h and the calls in the source: struct
    u_cnd_monotonic is one pthread_cond_t, as cnd_t is, so its storage is unchanged. The
    other Mesa conditions keep cnd_* and their clock.
    """
    replacements = {
        'winsys_ps5_radv_ps5_winsys.c.o': {'cnd_init': 'u_cnd_monotonic_init'},
        'winsys_ps5_radv_ps5_sync.c.o': {'cnd_broadcast': 'u_cnd_monotonic_broadcast',
                                         'cnd_timedwait': 'u_cnd_monotonic_timedwait'},
    }
    members = subprocess.run(['ar', 't', str(archive)], check=True, capture_output=True,
                             text=True).stdout.splitlines()
    helpers = {new for mapping in replacements.values() for new in mapping.values()}
    assert helpers <= symbols(archive, True), 'RADV lacks the monotonic condition helpers'
    objects = []
    for member, mapping in replacements.items():
        assert members.count(member) == 1, f'Expected one RADV member {member}'
        obj = directory/member
        obj.write_bytes(subprocess.run(['ar', 'p', str(archive), member], check=True,
                                       capture_output=True).stdout)
        undefined = symbols(obj) - symbols(obj, True)
        assert set(mapping) <= undefined, f'The sync calls in {member} changed: review the adapter'
        subprocess.run(['objcopy', *[f'--redefine-sym={old}={new}' for old, new in mapping.items()],
                        str(obj)], check=True)
        assert not set(mapping) & symbols(obj), f'{member} still calls the realtime condition'
        objects.append(obj)
    adapted = directory/archive.name
    shutil.copy2(archive, adapted)
    subprocess.run(['ar', 'r', str(adapted), *map(str, objects)], check=True)
    return adapted

work = tempfile.TemporaryDirectory(prefix='radv-clock-')
before = digest(source)
release = source
source = adapt_sync_clock(release, Path(work.name))

defined = sorted(symbols(source, True))
demangled = subprocess.run(['c++filt'], input='\n'.join(defined)+'\n',
                           check=True, capture_output=True, text=True).stdout.splitlines()
assert len(defined) == len(demangled)
public = {s for s in defined if re.match(r'vk[A-Z]|vk_icd', s)} | {'radv_GetInstanceProcAddr'}
assert 'radv_GetInstanceProcAddr' in defined
private = []
for name, readable in zip(defined, demangled):
    # A std:: container instantiated with aco/GLSL types still belongs to that
    # compiler version. Isolate its weak methods too, or the linker may silently
    # coalesce incompatible implementations from the OpenGL compiler archive.
    shared_cpp = ((re.match(r'^(typeinfo (for|name for) std::|vtable for std::)', readable)
                   and '<' not in readable)
                  or readable.startswith(('operator new', 'operator delete'))
                  or name.startswith(('__cxa_', '__gxx_', '_Unwind_')))
    if name not in public and not shared_cpp:
        private.append(name)
mapping = output/'symbols.map'
mapping.write_text(''.join(f'{s} eden_radv_private_{s}\n' for s in private))
target = output/source.name
subprocess.run(['objcopy', '--redefine-syms='+str(mapping), str(source), str(target)], check=True)
subprocess.run(['ranlib', str(target)], check=True)
assert digest(release) == before, 'Input RADV archive changed'
work.cleanup()
after_symbols = symbols(target)
assert not set(private).intersection(after_symbols), 'Unrenamed implementation symbol'
assert public.intersection(defined) <= symbols(target, True), 'Lost public entrypoint'
(output/'manifest.json').write_text(json.dumps({
    'source': str(release), 'source_sha256': before,
    'sync_clock': 'PS5 queue sync condition on the monotonic clock (u_cnd_monotonic)',
    'isolated_sha256': digest(target), 'renamed_symbols': len(private),
    'public_symbols': sorted(public.intersection(defined)),
    'note': 'Archive namespace verification only; final native link and runtime validation still required.'
}, indent=2)+'\n')
print(f'RADV isolated: {len(private)} implementation symbols; {target}')
