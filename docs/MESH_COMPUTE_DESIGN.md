# Mesh shaders as compute on the PS5 (design, 2026-11-06)

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

Never run a mesh shader on the NGG mesh path: run it as compute writing its outputs to memory,
and rasterize them through an ordinary vertex shader (the path every other draw uses). Mesa's
Asahi driver and this fork's `RADV_GS_COMPUTE` (geometry shaders NGG cannot run) do the same.

Per emulated task chunk (radv_task_emu_draw_chunk):

1. The task shader as compute (unchanged).
2. The records prepass, extended: besides the records, one entry per mesh workgroup (its
   record and its index in its grid, as experiment 0x2000000 writes them), the total, and the
   dispatch size of step 3 and the vertex count of step 4.
3. The mesh shader as compute, DISPATCH_INDIRECT over those workgroups. Each workgroup writes a
   slot: its vertex and primitive counts, its per-vertex outputs (vec4 each, max_vertices of
   them), its triangles' indices, its per-primitive outputs.
4. CS partial flush and cache invalidation, then one non-indexed DRAW_INDIRECT of 3 vertices per
   primitive slot (workgroups x max_primitives x 3) through a generated vertex shader: vertex
   id -> slot, primitive, corner; past the slot's primitive count it emits a zero-area triangle;
   otherwise it reads the corner's vertex outputs (and the primitive's outputs, flat) and passes
   them on to the application's fragment shader.

The compute mesh shader is compiled as a stage the task path already runs as compute with the
graphics pipeline's descriptors and push constants (radv_task_emu_dispatch,
radv_emit_graphics_stage_for_compute), so descriptors need no new path.

## Pipeline

In radv_graphics_shaders_compile, beside the GS-compute split: a pipeline with a task and a mesh
shader on a GPU that emulates task shaders (radv_task_emulated) splits the mesh stage:

- the mesh NIR, lowered (outputs and SetMeshOutputs to stores into the workgroup's slot,
  workgroup ID from the per-workgroup entry, task payload from the payload ring at the entry's
  task workgroup), is compiled as the extra compute binary;
- the vertex stage becomes the generated copy shader, topology triangle list;
- the mesh stage is removed from the hardware stages.

First slice limits: triangles only, no per-primitive outputs (fall back to the hardware path
otherwise), monolithic pipelines (no libraries or shader objects), no multiview, no mesh queries.
Ocarina's mesh shaders fit.

## Slices

1. Compile split + lowering, behind an experiment bit, checked on the console that the compute
   pass runs and writes sane slots (the capture tooling reads them).
2. The copy draw, with the game's fragment shader; Ocarina past the intro.
3. Default on for emulated task draws; remove the experiments; per-primitive outputs, multiview,
   indirect task draws as needed.
