#!/usr/bin/env python3
"""Apply the pinned Eden display-lifetime adaptation to the Mesa build export."""
from pathlib import Path
import hashlib
import sys

root = Path(__file__).resolve().parents[1]
source = root.parent/'mihawk-mesa-review/src/vulkan/wsi/wsi_common_videoout.c'
target = root.parent/'mihawk-vulkan-review/.deps/work/radv-src/src/vulkan/wsi/wsi_common_videoout.c'
text = source.read_text()

def replace(old, new):
    global text
    assert text.count(old) == 1, f'Pinned WSI anchor changed: {old[:70]}'
    text = text.replace(old, new)

replace('   bool opened;\n', '   unsigned owners;\n   bool closing, stop;\n   bool opened;\n')
# A set of framebuffers per output size: each remembers the memory to give back.
replace('      uint8_t *buffers;\n   } sets[VIDEOOUT_SIZES];',
        '      uint8_t *buffers;\n      int64_t physical;\n      size_t bytes;\n   } sets[VIDEOOUT_SIZES];')
replace('static int\nsceVideoOutSetFlipRate', 'static int\nsceVideoOutClose(int32_t handle)\n{\n   return 0;\n}\n\nstatic int\nsceVideoOutSetFlipRate')
replace('   uint8_t *buffers = NULL;\n#if defined(__PROSPERO__)\n   int64_t physical = -1;',
        '   uint8_t *buffers = NULL;\n   int64_t physical = -1;\n#if defined(__PROSPERO__)')
replace('   out->sets[index] = (struct wsi_videoout_set){.size = size, .buffers = buffers};',
        '   out->sets[index] = (struct wsi_videoout_set){.size = size, .buffers = buffers, .physical = physical,\n'
        '                                                .bytes = bytes};')
replace('      while (out->queue_count == 0)\n         cnd_wait(&out->changed, &out->lock);',
        '      while (out->queue_count == 0 && !out->stop)\n         cnd_wait(&out->changed, &out->lock);\n      if (out->queue_count == 0 && out->stop) {\n         mtx_unlock(&out->lock);\n         return 0;\n      }')
replace('VkResult\nwsi_display_init_wsi',
        (root/'tools/radv-videoout-lifecycle.inc').read_text()+'\nVkResult\nwsi_display_init_wsi')
# 120 Hz: the app asks for it per game session (headless/display_refresh.h). The pinned source
# decides from the package's param.json, which the app cannot read once it has left the sandbox,
# and for the whole run; the package declares the capability (tools/package-headless-native.sh).
declares = 'videoout_title_declares_high_frame_rate(void)\n{\n'
assert text.count(declares) == 1, 'Pinned WSI anchor changed: the 120 Hz declaration'
body_start = text.index(declares) + len(declares)
body_end = text.index('\n}\n', body_start)
assert 'param.json' in text[body_start:body_end], 'Pinned WSI anchor changed: the 120 Hz declaration reads param.json'
text = (text[:body_start] + '   const char *const asked = getenv("EDEN_VIDEOOUT_120HZ");\n'
        '   return asked != NULL && asked[0] == \'1\';' + text[body_end:])
# The pinned source takes the 120 Hz output back unless a vblank then comes about every 8.3 ms.
# On a display with a variable refresh rate that is wrong: before the first frame is shown it
# idles at the bottom of its range (a vblank every 20.87 ms, 48 Hz, in two reports), the output
# went back to 59.94 Hz, and a game could not have 120 Hz at all. Only a display that stayed at
# 59.94 Hz (a vblank about every 16.7 ms) gives the mode back.
replace('         if (period > VIDEOOUT_HIGH_REFRESH_LIMIT_NS) {',
        '         if (period > UINT64_C(15000000) && period < UINT64_C(18500000)) {')
# Say so when the display follows, as the pinned source does when it does not.
replace('''                    (double)period / 1e6);
         }
      } else {''', '''                    (double)period / 1e6);
         } else {
            fprintf(stderr, "wsi/videoout: 119.88 Hz output, a vblank every %.3f ms\\n", (double)period / 1e6);
         }
      } else {''')
replace('   wsi->base.get_support =', '   videoout_ref();\n\n   wsi->base.get_support =')
replace('   if (wsi)\n      vk_free(alloc, wsi);',
        '   if (wsi) {\n      videoout_unref();\n      vk_free(alloc, wsi);\n      wsi_device->wsi[VK_ICD_WSI_PLATFORM_DISPLAY] = NULL;\n   }')
checking = sys.argv[1:2] == ['--check']
if checking:
    # Is the driver that was built (its recorded display source, EDEN_WSI_SHA256) this adaptation?
    recorded = Path(sys.argv[2]).read_text().split()[0] if Path(sys.argv[2]).is_file() else ''
    if recorded != hashlib.sha256(text.encode()).hexdigest():
        sys.exit(1)
changed = not checking and (not target.exists() or target.read_text() != text)
if changed:
    target.write_text(text)
    # Upstream caches the whole build by pin. Invalidate only that receipt;
    # Ninja retains all unchanged objects and rebuilds the patched WSI.
    provenance = root.parent/'mihawk-vulkan-review/.deps/native/radv-release/PROVENANCE.txt'
    if provenance.exists():
        provenance.replace(provenance.with_suffix('.base.txt'))
if not checking:
    print(f'RADV WSI adaptation changed={changed} sha256={hashlib.sha256(text.encode()).hexdigest()}')

# Preserve the reason behind RADV's successful-but-empty device enumeration.
# These startup-only diagnostics avoid repeated full game runs for each stage.
for relative, replacements in {
    # Restore the pinned upload path: explicit CPU flush did not affect HUD corruption.
    'src/amd/vulkan/radv_cmd_buffer.c': [],
    'src/amd/vulkan/radv_physical_device.c': [
        ('   if (result == VK_ERROR_INCOMPATIBLE_DRIVER || result == VK_ERROR_INITIALIZATION_FAILED)\n      return VK_SUCCESS;',
         '   if (result != VK_SUCCESS) fprintf(stderr, "radv/ps5: physical device creation result=%d\\n", result);\n   if (result == VK_ERROR_INCOMPATIBLE_DRIVER || result == VK_ERROR_INITIALIZATION_FAILED)\n      return VK_SUCCESS;'),
        ('   if (!pdev->addrlib) {', '   if (!pdev->addrlib) {\n      fprintf(stderr, "radv/ps5: addrlib creation failed\\n");'),
        # The driver's buffers once: the visible VRAM heap's usage is allocated_vram_vis plus
        # allocated_vram, and both were the whole of it, so VK_EXT_memory_budget reported twice the
        # graphics memory (Eden's texture cache saw 6.1 GB of 4 GB and stayed over its limit).
        ('   heap_info->allocated_vram_vis = driver;',
         '   /* Counted once: the visible heap adds allocated_vram to this (EglantinePS5). */\n'
         '   heap_info->allocated_vram_vis = 0;'),
    ],
    'src/amd/vulkan/winsys/ps5/radv_ps5_winsys.c': [
        ('   if (!radv_ps5_platform_init())\n      return;', '   if (!radv_ps5_platform_init()) {\n      fprintf(stderr, "radv/ps5: queue platform initialization failed\\n");\n      return;\n   }'),
        ('   if (!radv_ps5_memory_alloc(RADV_PS5_RING_BYTES, RADV_PS5_LARGE_BYTES, true, &queue->ring))\n      return;', '   if (!radv_ps5_memory_alloc(RADV_PS5_RING_BYTES, RADV_PS5_LARGE_BYTES, true, &queue->ring)) {\n      fprintf(stderr, "radv/ps5: queue ring allocation failed\\n");\n      return;\n   }'),
        ('      radv_ps5_memory_free(&queue->ring);', '      fprintf(stderr, "radv/ps5: queue marker allocation failed\\n");\n      radv_ps5_memory_free(&queue->ring);'),
    ],
    'src/amd/vulkan/winsys/ps5/radv_ps5_platform.c': [
        ('''bool
radv_ps5_memory_grant_gpu(void *address, uint64_t bytes)
{
   /* A title's anonymous memory takes GPU access this way, and a shader then
    * writes it through the CPU's address (HARDWARE_FINDINGS.md, 2026-09-27). */
   return sceKernelMprotect(address, bytes,
                            PS5_KERNEL_PROT_CPU_READ | PS5_KERNEL_PROT_CPU_WRITE | PS5_KERNEL_PROT_GPU_READ |
                               PS5_KERNEL_PROT_GPU_WRITE) == 0;
}''', (root/'tools/radv-host-memory.inc').read_text().strip()),
        ('   if (!placed) {', '   if (!placed) {\n      fprintf(stderr, "radv/ps5: mapping rejected result=%d address=%p bytes=%llu window32=%d\\n", result, address, (unsigned long long)bytes, window32);\n      radv_ps5_tally_refused();'),
        # What the GPU-visible memory is for (tools/radv-memory-tally.inc), common to the PS5 and the host model.
        ('uint64_t\nradv_ps5_now_ns(void)', (root/'tools/radv-memory-tally.inc').read_text() + '\nuint64_t\nradv_ps5_now_ns(void)'),
        ('      sceKernelReleaseDirectMemory(physical, bytes);\n      return false;\n   }\n\n   *out = (struct radv_ps5_memory){\n      .cpu = address,\n      .bytes = bytes,\n      .physical = physical,\n      .granule = granule,\n      .granules = granules,\n   };\n   return true;\n}\n\nstatic bool radv_ps5_map_fixed',
         '      sceKernelReleaseDirectMemory(physical, bytes);\n      return false;\n   }\n\n   *out = (struct radv_ps5_memory){\n      .cpu = address,\n      .bytes = bytes,\n      .physical = physical,\n      .granule = granule,\n      .granules = granules,\n   };\n   radv_ps5_tally_direct((int64_t)bytes);\n   return true;\n}\n\nstatic bool radv_ps5_map_fixed'),
        ('   const int32_t unmapped = sceKernelMunmap(memory->cpu, memory->bytes);',
         '   radv_ps5_tally_direct(-(int64_t)memory->bytes);\n   const int32_t unmapped = sceKernelMunmap(memory->cpu, memory->bytes);'),
        ('   *out = (struct radv_ps5_memory){.cpu = at, .bytes = span, .physical = -1};\n   return true;\n}\n\nbool\nradv_ps5_vrange_reserve(',
         '   *out = (struct radv_ps5_memory){.cpu = at, .bytes = span, .physical = -1};\n   radv_ps5_tally_add(RADV_PS5_TALLY_SPARSE, (int64_t)span);\n   return true;\n}\n\nbool\nradv_ps5_vrange_reserve('),
        ('.physical = -1, .granule = granule, .granules = granules};\n   return true;',
         '.physical = -1, .granule = granule, .granules = granules};\n   radv_ps5_tally_add(RADV_PS5_TALLY_SPARSE, (int64_t)span);\n   return true;'),
        ('   sceKernelMunmap(range->cpu, range->bytes);\n   radv_ps5_granules_give(&radv_ps5_region, range->granule, range->granules);',
         '   radv_ps5_tally_add(RADV_PS5_TALLY_SPARSE, -(int64_t)range->bytes);\n   sceKernelMunmap(range->cpu, range->bytes);\n   radv_ps5_granules_give(&radv_ps5_region, range->granule, range->granules);'),
    ],
    'src/amd/vulkan/radv_device_memory.h': [
        ('   uint64_t alloc_size;\n', '   uint64_t alloc_size;\n   /* Where vkAllocateMemory was called from (EglantinePS5: radv_eden_memory_sites_report). */\n   uintptr_t eden_site;\n'),
    ],
    'src/amd/vulkan/radv_device_memory.c': [
        ('#include "vk_log.h"\n', '#include "vk_log.h"\n\n#include <inttypes.h>\n#include <stdio.h>\n#include "util/simple_mtx.h"\n' + '\n/* Live device memory by the address vkAllocateMemory was called from (EglantinePS5\'s adaptation):\n * the Vulkan allocator\'s blocks come from one place in it, anything allocated around it from its\n * own; reported with the PS5 winsys\'s memory tally (radv-memory-tally.inc). */\n#define RADV_EDEN_SITES 64\nstatic struct {\n   uintptr_t site;\n   int64_t bytes, count;\n} radv_eden_sites[RADV_EDEN_SITES];\nstatic simple_mtx_t radv_eden_sites_lock = SIMPLE_MTX_INITIALIZER;\n\nstatic void\nradv_eden_site_add(uintptr_t site, int64_t bytes)\n{\n   simple_mtx_lock(&radv_eden_sites_lock);\n   unsigned i = 0;\n   while (i < RADV_EDEN_SITES && radv_eden_sites[i].site != site && radv_eden_sites[i].site != 0)\n      ++i;\n   if (i == RADV_EDEN_SITES)\n      i = RADV_EDEN_SITES - 1; /* the last row takes the rest */\n   radv_eden_sites[i].site = radv_eden_sites[i].site ? radv_eden_sites[i].site : site;\n   radv_eden_sites[i].bytes += bytes;\n   radv_eden_sites[i].count += bytes < 0 ? -1 : 1;\n   simple_mtx_unlock(&radv_eden_sites_lock);\n}\n\nvoid radv_eden_memory_sites_report(void);\n\nvoid\nradv_eden_memory_sites_report(void)\n{\n   simple_mtx_lock(&radv_eden_sites_lock);\n   bool shown[RADV_EDEN_SITES] = {false};\n   for (unsigned n = 0; n < 12; ++n) {\n      int best = -1;\n      for (unsigned i = 0; i < RADV_EDEN_SITES; ++i)\n         if (!shown[i] && radv_eden_sites[i].site && radv_eden_sites[i].bytes > 0 &&\n             (best < 0 || radv_eden_sites[i].bytes > radv_eden_sites[best].bytes))\n            best = (int)i;\n      if (best < 0)\n         break;\n      shown[best] = true;\n      fprintf(stderr, "radv/ps5 memory from %#" PRIxPTR ": %" PRId64 " MiB in %" PRId64 "\\n",\n              radv_eden_sites[best].site, radv_eden_sites[best].bytes >> 20, radv_eden_sites[best].count);\n   }\n   simple_mtx_unlock(&radv_eden_sites_lock);\n}\n'),
        ('''   VK_FROM_HANDLE(radv_device, device, _device);
   return radv_alloc_memory(device, pAllocateInfo, pAllocator, pMem, false);
}''', '''   VK_FROM_HANDLE(radv_device, device, _device);
   const uintptr_t site = (uintptr_t)__builtin_return_address(0);
   const VkResult result = radv_alloc_memory(device, pAllocateInfo, pAllocator, pMem, false);
   if (result == VK_SUCCESS) {
      VK_FROM_HANDLE(radv_device_memory, mem, *pMem);
      if (mem->bo) {
         mem->eden_site = site;
         radv_eden_site_add(site, (int64_t)mem->bo->size);
      }
   }
   return result;
}'''),
        ('''   if (mem)
      radv_device_memory_emit_report(device, mem, /* is_alloc */ false, VK_SUCCESS);
''', '''   if (mem)
      radv_device_memory_emit_report(device, mem, /* is_alloc */ false, VK_SUCCESS);
   if (mem && mem->eden_site && mem->bo)
      radv_eden_site_add(mem->eden_site, -(int64_t)mem->bo->size);
'''),
    ],
    'src/amd/vulkan/winsys/ps5/radv_ps5_platform.h': [
        ('bool radv_ps5_memory_alloc(uint64_t bytes, uint64_t alignment, bool window32,\n                           struct radv_ps5_memory *out);',
         '/* What the GPU-visible memory is for (EglantinePS5, tools/radv-memory-tally.inc). */\n'
         'enum {\n   RADV_PS5_TALLY_APPLICATION,\n   RADV_PS5_TALLY_QUERY,\n   RADV_PS5_TALLY_DESCRIPTOR,\n'
         '   RADV_PS5_TALLY_SHADER,\n   RADV_PS5_TALLY_SPARSE,\n   RADV_PS5_TALLY_SUBMISSION,\n   RADV_PS5_TALLY_KINDS,\n};\n'
         'unsigned radv_ps5_tally_kind(unsigned priority);\nvoid radv_ps5_tally_add(unsigned kind, int64_t bytes);\n'
         'void radv_ps5_tally_report(const char *why);\n\n'
         'bool radv_ps5_memory_alloc(uint64_t bytes, uint64_t alignment, bool window32,\n                           struct radv_ps5_memory *out);'),
    ],
    'src/amd/vulkan/winsys/ps5/radv_ps5_bo.c': [
        ('   bo->base.obj_id = p_atomic_inc_return(&radv_ps5_next_obj_id);\n\n   /* Direct memory arrives',
         '   bo->base.obj_id = p_atomic_inc_return(&radv_ps5_next_obj_id);\n   bo->tally = (uint8_t)radv_ps5_tally_kind(priority);\n   radv_ps5_tally_add(bo->tally, (int64_t)bo->memory.bytes);\n\n   /* Direct memory arrives'),
        ('   if (bo->placed)\n      radv_ps5_memory_unmap_at(',
         '   radv_ps5_tally_add(bo->tally, -(int64_t)bo->memory.bytes);\n   if (bo->placed)\n      radv_ps5_memory_unmap_at('),
    ],
    'src/amd/vulkan/winsys/ps5/radv_ps5_winsys.h': [
        ('   bool imported;\n};', '   bool imported;\n   /* What it is for (radv_ps5_tally_kind). */\n   uint8_t tally;\n};'),
    ],
    'src/amd/vulkan/winsys/ps5/radv_ps5_cs.c': [
        ('      radv_ps5_memory_free(&oldest->dedicated);',
         '      if (oldest->dedicated.cpu)\n         radv_ps5_tally_add(RADV_PS5_TALLY_SUBMISSION, -(int64_t)oldest->dedicated.bytes);\n      radv_ps5_memory_free(&oldest->dedicated);'),
        ('         return NULL;\n      queue->in_flight_count++;',
         '         return NULL;\n      radv_ps5_tally_add(RADV_PS5_TALLY_SUBMISSION, (int64_t)claim->dedicated.bytes);\n      queue->in_flight_count++;'),
    ],
}.items():
    original = root.parent/'mihawk-mesa-review'/relative
    destination = root.parent/'mihawk-vulkan-review/.deps/work/radv-src'/relative
    adapted = original.read_text()
    for old, new in replacements:
        assert adapted.count(old) == 1, f'Pinned initialization anchor changed: {relative}'
        adapted = adapted.replace(old, new)
    if checking:
        # The driver was built from these sources only if they are this adaptation.
        if destination.read_text() != adapted:
            sys.exit(1)
        continue
    if destination.read_text() != adapted:
        destination.write_text(adapted)
        provenance = root.parent/'mihawk-vulkan-review/.deps/native/radv-release/PROVENANCE.txt'
        if provenance.exists():
            provenance.replace(provenance.with_suffix('.base.txt'))
    print(f'RADV initialization diagnostic {relative} sha256={hashlib.sha256(adapted.encode()).hexdigest()}')
if checking:
    sys.exit(0)
