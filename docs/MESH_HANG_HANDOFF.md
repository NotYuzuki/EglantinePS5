# Ocarina of Time task/mesh hang: handoff (2026-11-06)

Goal: Ocarina of Time (`0400486024dec000`, a Switch 2 title) freezes right after its intro movie,
the moment it issues its first task+mesh draws. Fix it, so the game plays without
`EDEN_NX2_NO_MESH=1`. Everything below is from the session that got as far as instrumenting the
driver; the next step is a console run with that instrumentation (section 5).

## 1. Where things are

- **Worktree:** `/home/esotericizm/exploitizmPS5/.claude/worktrees/elgantine-eden-ports-22bee8`,
  branch `claude/elgantine-eden-ports-22bee8`. Work there: it has the driver build (section 3).
  Nothing on it is pushed.
- **Console:** PS5 at `192.168.50.39`, FTP on port `2121` (anonymous). App folder
  `/data/homebrew/PPSA98515`; logs in `/data/eglantineps5/logs` (`stderr.log`, `hang-*.txt`,
  `crash-*.txt`) and `/data/eglantineps5/user/log/eden_log.txt`. The user runs the game; you cannot
  start it yourself.
- **Evidence from this session:** `.local/mesh-hang/` (ignored by git):
  `run-0951-old-driver/` (hang on the Oct 5 driver) and `run-1039-new-driver/` (hang on the new
  driver; `hang-104852.sym.txt` is the symbolized report), and `pull-logs.py OUT_DIR` to fetch the
  console's logs again.
- **Evidence from the earlier session** (the Lichii core update): in the other worktree,
  `/home/esotericizm/exploitizmPS5/.claude/worktrees/zelda-gpu-semaphore-bug-ee12c5/.local/lichii-handoff/`:
  `gfx-spirv/` (the task shader `46cc6aaba42f700f-s1.spv`, the mesh shaders
  `98b8c77741389321-s3.spv` and `7caf726f95774b01-s3.spv`, disassembly beside them),
  `spirv_nesting.py`, and **`mesh-repro/`: a captured task+mesh draw** (`input-task-cbuf0..2.bin`,
  `input-task-ssbo.bin`, `input-mesh-cbuf0..2.bin`, `input-mesh-ssbo.bin`, `frag.spv`, and two
  minimal test shaders, `mesh-null.mesh` and `mesh-tri.mesh`). Nobody has run it yet.
- **Earlier write-up:** [LICHII_UPDATE_HANDOFF.md](LICHII_UPDATE_HANDOFF.md), section "The hang
  after the movie: task/mesh draws".

## 2. What is known about the hang

**Symptom.** After the movie, `eden_log.txt` logs `Mesh draw: groups=N ...` (the first 64 mesh
draws, from `vk_rasterizer.cpp`); within about a second no frame is presented again, and the hang
watch writes `hang-*.txt` 30 s later ("no game frame shown for 30 s"). Memory is not the cause: the
pool's largest free block was 5 GB at the time.

**Stacks** (identical on the old and the new driver; `hang-104852.sym.txt`):
- `VulkanWorker` is inside `radv_ps5_submit` → `sceAgcDriverSubmitDcb`
  (`winsys/ps5/radv_ps5_platform.c:958`): the kernel call that hands a command buffer to the GPU
  does not return. The GPU stopped and its ring is full.
- `GPU` (the emulator's GPU thread) waits in `VideoCommon::GpuSplit::Drain`, called from
  `Tegra::DmaPusher::FlushGpuWrittenCommands`: it waits for GPU-written commands that never come.
- `GPUFencingThread` and `GPUBack` wait on Vulkan timeline semaphores
  (`vk_sync_timeline_wait`); `CPUCore_3` is blocked pushing a GPU command because the queue is full.

**The draws.** 64 mesh draws were logged before the hang, all direct, `instances=1`, non-indexed,
`groups` 0 to 13 (9 of them `groups=0`). The core records them as
`cmdbuf.DrawMeshTasksEXT(groups, 1, 1)` (`Eglantine/src/video_core/renderer_vulkan/vk_rasterizer.cpp:777`).
Indirect task/mesh draws are not supported by the core and are reported as failures, not drawn.

**Ruled out:**
- Memory, ASTC transcodes and the `format=0` assert (earlier session: `astc=cpu` still hangs).
- Zero-group draws: `radv_task_emu_draw_direct` dispatches nothing when `x*y*z == 0`.
- The mesh "parts" spin-wait: a mesh workgroup is split into parts that wait on each other only
  when its shader has per-primitive outputs (`radv_ms_prim_parts`, `radv_shader_info.c:422`), and
  the earlier session found none in Ocarina's mesh shaders. Worth re-checking against the actual
  `per_prim_mask` RADV computes (implicit outputs such as primitive ID or layer count too).
- The shaders themselves (earlier session): small draws, loops capped at 8192, barriers in
  uniform control flow.

**Workaround:** `env=EDEN_NX2_NO_MESH=1` in a development build's `dev-settings.txt` skips mesh
draws (`vk_rasterizer.cpp:401`); the game then plays with some shader problems. Release builds do
not read dev-settings environment overrides.

## 3. The driver

Task and mesh shaders on the PS5 are Mihawk's own work, Sep 27-28 in `mihawk-99/PS5_Mesa`, and
nobody has changed them since (searched: Mihawk's PS5_Mesa and PS5_Vulkan, Oliv3rmoon's Belladonna
and Nightshade, niakw's ProsperoEden-Encore on GitHub and Eden's Forgejo, heydemoura's
EglantinePS5, blackbearreloaded's ProsperoEden, Djihads80's Eglantine, zortos293's Lichii). The PS5
lacks the CP's task/mesh dispatch packets and an async compute queue, so:

- `ded225a9396` "task shaders on the graphics ring": a task draw goes in chunks of as many task
  workgroups as the task rings hold. Per chunk, on the graphics ring: the task shader as a compute
  dispatch (`radv_task_emu_dispatch`), the records prepass (`radv_meta_mesh_draw_records`), then the
  mesh draw of those records (`radv_task_emu_draw_chunk`). The next chunk waits for the mesh shaders
  (`RADV_CMD_FLAG_VS_PARTIAL_FLUSH`). Code: `src/amd/vulkan/radv_cmd_buffer.c`, from the comment at
  about line 15400 to `radv_CmdDrawMeshTasksEXT` (line 15653 in our branch).
- `0ad8a3e7d9c`, `ff437af31b9`: mesh shaders without per-primitive parameters, and the parts that
  publish to a device ring (`AC_MS_PUBLISH_RING_BYTES`).
- `0be56b9bcec`: stages run as compute get all their descriptors.
- Tested by Mihawk with a smoke title (`PS5_Vulkan radv/shaders/mesh_ticket.mesh`) and the CTS:
  23,057 task/mesh cases pass. Ocarina's draws evidently do something those do not.

**This worktree's driver.** The three driver checkouts used to be shared by every worktree
(`../mihawk-*-review`). This worktree has its own, selected by `.local/radv-refs` (contents:
`.local/radv`), see `tools/radv-refs.sh` and commit `05c8a2a`:

- `.local/radv/mihawk-mesa-review`: branch **`eden-radv`**, local only, shallow clone of
  `https://github.com/mihawk-99/PS5_Mesa` with remote `belladonna` =
  `https://github.com/Oliv3rmoon/Belladonna`:
  - `f5610fe` Mihawk's main (Nov 3);
  - `1a17eee` (Belladonna `76c71a05f54`): no null pointer for a dropped geometry-shader-as-compute draw;
  - `def3fa1` (Belladonna `9d73715bd1a`): split long runs of transfers and barriers;
  - `c7f2756` (Belladonna `318deb146da`): **the stall dump** (see section 4);
  - `1b8b30d` (ours): the stall dump on request (`radv_ps5_dump_stall_now`, offered through the
    app's `eden_radv_stall_dump` pointer).
- `.local/radv/mihawk-vulkan-review`: `mihawk-99/PS5_Vulkan` at `f410e67a024` (reads
  `PS5_MESA_REVISION`); `.deps/native` copied from the shared checkout. The RADV build and its
  exported, patched source are in `.deps/work/radv-src` (that is the code that runs; edit
  `mihawk-mesa-review` and commit, then move the pin).
- `.local/radv/mihawk-sdk-review` → link to the shared `../mihawk-sdk-review` (pin unchanged).
- `tools/deps.json` pins `ps5-mesa` at `1b8b30db401f95c14559e2011132281694e62f75` and
  `ps5-vulkan-tools` at `f410e67a0241b3e8edcdbcd2a9b285dfc4a412ed`.

The four `eden-radv` commits are also in this repository as patches,
`tools/eden-radv-patches/0001-0004`. If `.local/radv` is missing (a fresh checkout of the branch),
recreate it with `set -e` and `git -C`:

```bash
R=.local/radv; mkdir -p $R
git clone --depth 30 --branch main https://github.com/mihawk-99/PS5_Mesa.git $R/mihawk-mesa-review
git -C $R/mihawk-mesa-review checkout -b eden-radv f5610fe2709
git -C $R/mihawk-mesa-review am "$PWD"/tools/eden-radv-patches/*.patch
git clone --filter=blob:none https://github.com/mihawk-99/PS5_Vulkan.git $R/mihawk-vulkan-review
git -C $R/mihawk-vulkan-review checkout f410e67a0241b3e8edcdbcd2a9b285dfc4a412ed
mkdir -p $R/mihawk-vulkan-review/.deps/native
cp -a ../mihawk-vulkan-review/.deps/native/{ps5-payload-sdk,ps5-payload-sdk.zip,zlib} $R/mihawk-vulkan-review/.deps/native/
ln -sfn "$(cd .. && pwd)/mihawk-sdk-review" $R/mihawk-sdk-review
echo .local/radv > .local/radv-refs
```

`git am` gives new commit ids: put the new `eden-radv` head in `tools/deps.json` (`ps5-mesa`).
**The `eden-radv` branch needs a public home before this can merge** (`tools/deps.json` names
Mihawk's repo, which does not have it). Do not push without asking the user.

To change the driver: edit and commit in `.local/radv/mihawk-mesa-review`, put the new commit in
`tools/deps.json` (`ps5-mesa`), and rebuild; `tools/prepare-build.sh` rebuilds RADV when the
installed revision differs from the pin. `tools/patch-radv-wsi.py` then applies this repository's
own anchored edits to the export (display lifetime, 120 Hz, memory tallies); if an anchor moves,
it fails with "Pinned WSI anchor changed".

## 4. What the stall dump will say

Belladonna's dump (`radv_ps5_dump_stall` in `winsys/ps5/radv_ps5_winsys.c`) decodes the stuck
submission and the ones behind it: PM4 packet counts, draws, dispatches, `WAIT_REG_MEM` waits and
`COND_EXEC`s with their address, reference, mask and what the CPU reads there now; **the first wait
whose condition does not hold** (where the GPU stopped); and the last draws/dispatches before it
with the programs they run (`SPI_SHADER_PGM_*`, `COMPUTE_PGM_*`), named by pipeline hash and
`VkPipeline` handle. Lines start with `radv/ps5 stall:` in `stderr.log`.

It never ran on its own in this hang: its ten-second check lives in `radv_ps5_queue_wait_seq`,
which only runs while a thread polls a submission's marker, and here nobody does. Commit `a4af97f`
makes the hang watch (`headless/crash_report.cpp`, `WriteHangReport`) call
`eden_radv_stall_dump` first, at 30 s without a frame. Expect in `stderr.log`:

- `radv/ps5: stall dump asked: the GPU has run submission N of M` and the dump, or
- `radv/ps5: stall dump asked: the GPU has run every submission (N)`: then the GPU is not stuck
  and the wait is on the emulator's side (`GpuSplit::Drain`, `FlushGpuWrittenCommands`): look at
  what GPU-written command list the game expected (the earlier session saw
  `TextureCacheRuntime::ReleaseTranscodes` and `WaitPendingFences` in this position too).

The dump runs once per process. The first launch after a driver change also writes a 120 s hang
report while shaders compile (0 frames shown); that is normal, and if the GPU is idle then the
dump just says so and stays available.

## 5. Next steps

1. **Console run with the dump** (installed now, commit `a4af97f`, driver `1b8b30d`): ask the user
   to start Ocarina without the mesh workaround and, once it freezes, wait about 40 s before
   closing it. Pull the logs (`python3 -I .local/mesh-hang/pull-logs.py <dir>`), symbolize the
   newest `hang-*.txt` with
   `python3 -I tools/symbolize-crash.py <hang.txt> build/headless-native/llvm-pie.elf`
   (the ELF of the installed build), and read the `radv/ps5 stall:` lines.
2. **Map the stuck wait to the emulation.** Which packet stops it: the task dispatch, a
   `WAIT_REG_MEM` from the chunk's flushes (`radv_emit_cache_flush`, `VS_PARTIAL_FLUSH`), the
   records prepass, or the mesh draw? Match the program addresses to the task (slot 1) and mesh
   (slot 3) pipelines; the core logs `slot1=`/`slot3=` per mesh draw. Candidates to check against
   it: the task rings' entry count and payload stride for Ocarina's task shader
   (`radv_task_emu_begin`: `entries = MIN2(RADV_TASK_EMU_MAX_ENTRIES, 1 << log2(PAYLOAD_BYTES / stride))`);
   the draw ring and records layout; scratch for the task dispatch; the descriptors a task shader
   run as compute gets (`0be56b9bcec`); and whether Ocarina's mesh shader does publish after all
   (`radv_ms_publishes`).
3. **Reproduce outside the game.** `mesh-repro/` has one captured draw. A small Vulkan title from
   Mihawk's template (`mihawk-99/PS5_VulkanTemplate`, or PS5_Vulkan's smoke title with
   `radv/shaders/mesh_ticket.mesh`) running that draw on the console would let you bisect the
   emulation (task dispatch alone, then plus the prepass, then the draw).
4. **Fix in `eden-radv`**, move the pin, rebuild, retest. Keep the fix as a commit Mihawk could take
   upstream. A fallback if the fix is out of reach: make `EDEN_NX2_NO_MESH=1` the PS5 default
   (`headless/main.cpp`, beside the other `setenv(..., 0)` defaults), with the user's agreement:
   it changes gameplay rendering for every game that draws meshes.

## 6. Building and installing

```bash
EDEN_CATCH2_BUILD=/home/esotericizm/exploitizmPS5/.local/nx2-core-check make install PS5_HOST=192.168.50.39 PS5_PORT=2121
```

Builds the driver if its pin changed, then the app with every release source check, packages it
and copies only changed files over FTP. `EDEN_CATCH2_BUILD` is needed by `check-nx2-core.py` in a
worktree. A RADV rebuild takes a while; the app alone is quick. Run it in the background and wait
for it to finish.

Pitfalls met in this session:
- **An interrupted build can leave a 0-byte archive** that ninja then treats as current (here
  `native-local/src/audio_core/libaudio_core.a` in the build cache, `cat .local/headless-cache`):
  the link then fails with hundreds of undefined symbols. Delete the empty file and rebuild.
- **The package check refuses undefined imports**, including a weak symbol the driver references
  and the app does not define (`eden_gpu_stalled` was one; the app defines it now).
- **The driver's symbols are renamed** by `tools/isolate-radv.py` (`eden_radv_private_*`) except
  `vk*` entry points and `radv_GetInstanceProcAddr`: to call into the driver, have the driver set
  a pointer the app defines (as `eden_radv_stall_dump` does), or add the name to the public list.
- **Use `git -C <path>` with `set -e`** for anything in the driver checkouts: a failed `cd` once
  ran driver commands in this repository and changed its `origin` remote (restored).
- Do not change the shared `../mihawk-*-review` checkouts: other worktrees build from them.
- `make dev DEV_TITLE=0400486024dec000` builds a development package that reads
  `/data/homebrew/PPSA98515/dev-settings.txt` (e.g. `env=EDEN_NX2_NO_MESH=1`,
  `env=EDEN_GRAPHICS_SPIRV_DUMP=<dir>`); installing it can replace that file.

## 7. Also on this branch (not part of the mesh work)

- `f03fd23`: PS5 Pro memory mode (`attribute3` bit 22) and a 320 MiB flexible pool in
  `sce_sys/param.json`; on a base PS5 the direct pool reads 12,416 MiB.
- `2401f1e`: memory work ported from Oliv3rmoon/Nightshade (pool fixes in `src/direct_memory.c` and
  `src/memory_pages.cpp`, idle staging released on a refused image or buffer, transfers over 64 MiB
  staged in pieces: `headless/staging_chunks.h`). Not yet measured on the console, because every
  Ocarina run since has stopped at this hang.
- `236c942`: merge of `claude/lichii-update` (core `c2f9811073`, Lichii nx2 `99ac3d5955`).
- The main checkout (`/home/esotericizm/exploitizmPS5`, branch `codex/mkw-memory-lifetimes`) has
  uncommitted memory work from Nov 4-5; most of it is in the current core already. Do not modify it.
