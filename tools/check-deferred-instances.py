#!/usr/bin/env python3
"""Exercise real deferred draw methods without building the emulator."""
from pathlib import Path
import os
import resource
import shutil
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
import subprocess
import tempfile
root = Path(__file__).resolve().parents[1]
source = (root / 'Eglantine/src/video_core/engines/draw_manager.cpp').read_text()
def method(name):
    start = source.index('void Maxwell3D::DrawManager::' + name + '(')
    return source[start:source.index('\n}\n', start) + 3]
methods = '\n'.join(method(name) for name in
                    ('DrawDeferred', 'DrawBegin', 'DrawEnd', 'DrawArrayInstanced'))
# The old signature can run the same scenarios; it ignores continuation.
if 'bool loop_continues' not in method('DrawDeferred'):
    methods = methods.replace('DrawDeferred(Maxwell3D& maxwell3d)',
                              'DrawDeferred(Maxwell3D& maxwell3d, bool loop_continues)')
prefix = r"""
#include <cassert>
#include <cstdint>
#include <vector>
using u8 = uint8_t;
using u32 = uint32_t;
namespace VideoCommon::Dirty { constexpr int IndexBuffer = 0; }
struct Maxwell3D {
    struct Regs {
        enum class PrimitiveTopology { Triangles };
        enum class IndexFormat { UnsignedInt };
        struct VertexBuffer { u32 first{}, count{}; } vertex_buffer;
        struct IndexBuffer { u32 first{}, count{}; IndexFormat format{}; } index_buffer;
        struct Draw {
            enum class InstanceId { First, Subsequent };
            InstanceId instance_id{};
            PrimitiveTopology topology{};
        } draw;
        u32 global_base_instance_index{10}, global_base_vertex_index{7};
    } regs;
    struct { bool flags[1]{}; } dirty;
    struct DrawManager {
        enum class DrawMode { General, Instance, InlineIndex };
        struct State {
            DrawMode draw_mode{};
            Regs::PrimitiveTopology topology{};
            bool draw_indexed{};
            u32 instance_count{}, drawn_instances{}, base_instance{}, base_index{};
            Regs::VertexBuffer vertex_buffer;
            Regs::IndexBuffer index_buffer;
            std::vector<u8> inline_index_draw_indexes;
        } draw_state;
        u32 instance_index{};
        struct Call { u32 base, count; bool indexed; };
        std::vector<Call> calls;
        void DrawDeferred(Maxwell3D&, bool loop_continues = false);
        void DrawBegin(Maxwell3D&);
        void DrawEnd(Maxwell3D&, u32 instance_count = 1, bool force_draw = false);
        void DrawArrayInstanced(Maxwell3D&, Regs::PrimitiveTopology, u32, u32, bool);
        void ProcessDraw(Maxwell3D&, bool indexed, u32 count) {
            calls.push_back({draw_state.base_instance, count, indexed});
        }
    };
};
"""
tests = r"""
void pending(Maxwell3D::DrawManager& d, Maxwell3D& m, u32 count, bool indexed = false) {
    m.regs.draw.instance_id = Maxwell3D::Regs::Draw::InstanceId::Subsequent;
    for (u32 i = 0; i < count; ++i) { d.DrawBegin(m); d.draw_state.draw_indexed = indexed; d.DrawEnd(m); }
}
void expect(const Maxwell3D::DrawManager& d, size_t i, u32 base, u32 count, bool indexed = false) {
    assert(i < d.calls.size());
    assert(d.calls[i].base == base && d.calls[i].count == count && d.calls[i].indexed == indexed);
}
int main() {
    // Unsplit loops retain the established count, base and indexed/nonindexed behavior.
    for (bool indexed : {false, true}) {
        Maxwell3D m; Maxwell3D::DrawManager d;
        pending(d, m, 4, indexed); d.DrawDeferred(m);
        assert(d.calls.size() == 1); expect(d, 0, 10, 5, indexed);
    }
    for (bool indexed : {false, true}) {
        Maxwell3D m; Maxwell3D::DrawManager d;
        pending(d, m, 2, indexed); d.DrawDeferred(m, true);
        expect(d, 0, 10, 3, indexed);
        d.DrawDeferred(m, true); // An additional state write with no pending draws.
        assert(d.calls.size() == 1);
        pending(d, m, 2, indexed); d.DrawDeferred(m, true);
        expect(d, 1, 13, 2, indexed);
        pending(d, m, 1, indexed); d.DrawDeferred(m);
        expect(d, 2, 15, 1, indexed);
        assert(d.calls.size() == 3);
        // Ending the loop must reset continuation before a fresh loop.
        m.regs.draw.instance_id = Maxwell3D::Regs::Draw::InstanceId::First;
        d.DrawBegin(m); pending(d, m, 1, indexed); d.DrawDeferred(m);
        expect(d, 3, 10, 2, indexed);
    }
    {
        Maxwell3D m; Maxwell3D::DrawManager d;
        pending(d, m, 1); d.DrawDeferred(m, true); d.DrawDeferred(m);
        pending(d, m, 1); d.DrawDeferred(m); expect(d, 1, 10, 2);
    }
    {
        Maxwell3D m; Maxwell3D::DrawManager d;
        pending(d, m, 1); d.DrawDeferred(m, true);
        m.regs.draw.instance_id = Maxwell3D::Regs::Draw::InstanceId::First;
        d.DrawBegin(m); pending(d, m, 1); d.DrawDeferred(m); expect(d, 1, 10, 2);
    }
    {
        Maxwell3D m; Maxwell3D::DrawManager d;
        pending(d, m, 1); d.DrawDeferred(m, true);
        auto topology = Maxwell3D::Regs::PrimitiveTopology::Triangles;
        d.DrawArrayInstanced(m, topology, 0, 3, false);
        d.DrawArrayInstanced(m, topology, 0, 3, true);
        expect(d, 1, 0, 1); expect(d, 2, 1, 1);
        assert(d.calls.size() == 3 && d.draw_state.drawn_instances == 0);
    }
}
"""
def run(code, should_pass):
    with tempfile.TemporaryDirectory(prefix='deferred-instances-') as tmp:
        cpp, exe = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
        cpp.write_text(prefix + code + tests)
        subprocess.run([os.environ.get('CXX') or shutil.which('clang++') or 'clang++-18',
                        '-std=c++20', '-O1', '-g', '-fsanitize=address,undefined',
                        str(cpp), '-o', str(exe)], check=True)
        result = subprocess.run([str(exe)], capture_output=True, text=True)
        if should_pass and result.returncode:
            raise RuntimeError(result.stderr)
        if not should_pass and result.returncode == 0:
            raise RuntimeError('Broken continuation unexpectedly passed')
run(methods, True)
# Independent controls: fail if either the count correction or base advance is removed.
count_fix = 'draw_state.instance_count + (continued ? 0 : 1)'
base_fix = 'draw_state.base_instance += draw_state.drawn_instances + 1;'
assert methods.count(count_fix) == methods.count(base_fix) == 1
run(methods.replace(count_fix, 'draw_state.instance_count + 1'), False)
run(methods.replace(base_fix, '(void)0;'), False)
# Both register-writing paths must preserve continuation; macro completion must end it.
caller = (root / 'Eglantine/src/video_core/engines/maxwell_3d.cpp').read_text()
assert caller.count('draw_manager.DrawDeferred(*this, true);') == 2
for name in ('ProcessDirtyRegisters', 'CallMultiMethod'):
    start = caller.index('void Maxwell3D::' + name + '(')
    end = caller.index('\nvoid Maxwell3D::', start + 1)
    body = caller[start:end]
    assert 'draw_manager.DrawDeferred(*this, true);' in body
for name in ('CallMacroMethod', 'FinishFrontMacro'):
    start = caller.index('void Maxwell3D::' + name + '(')
    end = caller.index('\n}\n', start) + 3
    assert 'draw_manager.DrawDeferred(*this);' in caller[start:end]
print('Deferred instance continuation, immediate arrays and both negative controls PASS')
