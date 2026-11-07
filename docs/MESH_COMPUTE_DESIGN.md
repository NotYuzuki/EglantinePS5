# Mesh shaders as compute on the PS5 (2026-11-06, done 2026-11-07)

## Why

Ocarina of Time's emulated task+mesh draws hang the PS5's GPU (GFX10.1, `CHIP_GFX1013`) in the
NGG mesh fast-launch path. Over about 40 console runs (docs/MESH_HANG_HANDOFF.md and the run
directories under `.local/mesh-hang/`), what was established:

- Every mesh workgroup that starts runs all its waves to the end (in-shader counters), and the
  hardware launches exactly the workgroups the records ask for.
- The draw never completes: the kernel's unit status at the hang (klog) has PA, SX and SPI busy,
  GE, SC and CB idle. Rasterization killed, it still hangs.
- It depends on history, not on one meshlet: a meshlet that passes in one chunk hangs in a later
  one, and a single mesh workgroup in its own draw, with the GPU idle before and after, can hang.
  Fewer exported vertices (cutting the ones no primitive names) or null primitives move the hang
  much later (15 -> 212 workgroups) without removing it.
- Not the cause: occlusion counting, late alloc, the CU mask, VERT_GRP_SIZE, the 126/128-lane
  workgroup, a parameter export, the multi-draw packet, empty records, export order, counts,
  positions, backward vertex references, the fully-culled workaround.

The reading: GFX10.1's NGG GS-mode (which mesh fast launch uses) keeps state across draws that
deadlocks primitive assembly and the shader exporter under mesh-shaped subgroups. Upstream enables
mesh shaders on GFX10.3 only. No register setting found avoids it.

## How

Never run a mesh shader on the NGG mesh path, with an emulated task shader or without one: run it
as compute writing
its outputs to memory, and rasterize them through an ordinary vertex shader (the path every other
draw uses). Mesa's Asahi driver and this fork's `RADV_GS_COMPUTE` (geometry shaders NGG cannot
run) do the same. The driver change is `eden-radv` 0008 (`tools/eden-radv-patches`,
`src/amd/vulkan/radv_mesh_compute.c`), with 0010 to 0014 after it.

A mesh draw without a task shader (on this GPU a DRAW_INDEX_AUTO of a vertex a mesh workgroup,
the same NGG launch: it hung Ocarina on the Lichii 09b78bd2df core, eden-radv 0012) goes the same
way: the records pass reads the draws' own commands, the direct draw's grid uploaded as one, a
record a draw with its draw ID, and the passes below are the same.

Per emulated task chunk (`radv_mesh_compute_chunk`):

1. The task shader as compute (unchanged, `radv_task_emu_dispatch`).
2. The records pass (`radv_meta_mesh_compute_records`): one record per task workgroup, with its
   grid, its task draw and the count of mesh workgroups before it, then in the buffer's header
   the count of records and of mesh workgroups, and each pass's dispatch and draw arguments.
3. Per pass of as many mesh workgroups as the slots hold (`capacity`, 16 MiB over the
   workgroup's slot size, at most 65536), the command processor writes the pass's first mesh
   workgroup to the header, then (a pass past the chunk's mesh workgroups is skipped by the
   command processor, a COND_EXEC on its dispatch's workgroup count):
   - the mesh shader as compute, DISPATCH_INDIRECT over the pass's workgroups. Each one finds its
     task workgroup's record by binary search, and writes a slot: its vertex and primitive
     counts, its per-vertex outputs (a vec4 each, max_vertices of them), its triangles' indices;
   - CS partial flush and cache invalidation, then one DRAW_INDIRECT of 3 vertices per primitive
     slot through the generated copy vertex shader (vertex ID to slot, primitive and corner; a
     zero-area triangle past the slot's primitive count), with the application's fragment shader;
   - a VS partial flush before the next pass or chunk writes the slots.

A chunk holds up to 65536 mesh workgroups; any past that are left out. Direct, indirect and
indirect count draws all take this path (indirect ones chunk as `radv_task_emu_draw_indirect`
does).

The compute mesh shader runs with the graphics pipeline's descriptors and push constants
(`radv_emit_graphics_stage_for_compute`), as the task shader does.

## Pipeline

In `radv_graphics_shaders_compile`, beside the GS-compute split, a pipeline with a mesh shader on a
GPU that emulates task shaders (`radv_task_emulated`) splits the mesh stage
(`radv_mesh_compute_split`); its compute pass is compiled in the graphics stages' order (indirect
derefs lowered after the first optimization loop, eden-radv 0013):

- the mesh NIR, lowered (outputs and SetMeshOutputs to stores into the workgroup's slot,
  workgroup ID, grid and draw ID from its record, task payload from the payload ring at its task
  workgroup's entry), is compiled as the pipeline's extra compute binary;
- the vertex stage becomes the generated copy shader, in waves of the vertex stages' size,
  topology triangle list;
- the mesh stage is removed from the hardware stages. The pipeline binds the copy as its vertex
  shader, and its dynamic state leaves out what mesh pipelines do not have.

The device's buffer (a 16 KiB header, then 16 MiB of slots) is made when the first pipeline
with a task shader is created. The shaders hold no address: each draw uploads a block with the
task emulation's records and payload ring and the buffer's header and slots
(`radv_mesh_compute_block`), which both shaders take as their `gs_compute_draw` argument, as a
geometry shader's passes do. So these pipelines are cached like any other: the pipeline cache
keeps the compute pass after the other shaders, with its vertices a workgroup and capacity in
the object's data, and whether this path is on is part of the cache's UUID. Ocarina's first frame
came at 273 s with an empty cache and at 21 s with a full one (eden-radv 0010).

## Status

Ocarina's mesh shaders take slots of 1.6 to 10.8 KB, 1,551 to 10,485 mesh workgroups a pass. The
buffer was 128 MiB until empty passes were skipped
(eden-radv 0011); the driver's own memory went from 220 to 108 MiB.

On by default. `/data/eglantineps5/radv-mesh-compute.txt` holding `0` turns it off (the NGG mesh
path, which hangs Ocarina).

Ocarina of Time (`0400486024dec000`) plays past the intro and into gameplay, and textures that
were missing with `EDEN_NX2_NO_MESH` draw. It runs out of memory after a few minutes of gameplay
at 1x with no mods (`VK_ERROR_OUT_OF_DEVICE_MEMORY`), as it did before this work: the guest's own
memory (about 6 GB), graphics (about 3.9 GB) and the heap fill the console's 12.4 GB pool. That
is a separate problem, the Switch 2 memory one (the texture cache holds only its working set, 1.5
to 2 GB).

On the Lichii 09b78bd2df core, which shows its first frame in 20 to 40 s and builds pipelines
while the game plays, that limit comes about 80 s into gameplay, in a pipeline build ("heap
exhausted"; every mesh compute pass had compiled). The console builds pipelines on at most 4
threads (`tools/prepare-vulkan-port.py`), which lowers the peak but does not avoid it.

Limits, where a pipeline keeps the hardware path:

- triangles only (no points or lines);
- per-vertex outputs that are positions and generic varyings; no per-primitive outputs but the
  indices, no primitive ID, layer or viewport for the fragment shader;
- no multiview;
- at most 256 vertices and 256 primitives.

Not done yet:

- measuring the cost against `EDEN_NX2_NO_MESH`;
- pipeline statistics and mesh queries for these draws.
