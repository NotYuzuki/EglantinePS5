# Mario Kart World CPU readback fix

The PS5 run using the ordered CPU readback path slowed to approximately 0.9–1.1 FPS.
Restoring the original core's readback behavior resolved the reported slowdown. The latest
sampled windows reached 18.974, 19.370, 18.836 and 18.705 FPS. These samples are from different
moments in the run; they do not establish full performance parity with the original tree.

Core commit: `b9eb499e5028066e2bafd7c3e927b5516b7d1541` on `codex/mkw-readback-fix`.
Base: `68d925c97ee0adde8a31f30f14f3547c893c7ffe`.

## Behavior

For NX2 reads from outside the GPU workers, the fix restores the original
`GetFlushArea` / preemptive return and
`RequestSyncOperation` / `TickGPU` / `WaitForSyncOperation` route, instead of sending an
ordered `CpuReadCommand`. GPU-owner handling is preserved. Returned `completed_generation`
is zero, so the newer readback cache cannot reuse this route as a certified completion.

The source change is isolated to `src/video_core/gpu.cpp` in the core. The core selects this
behavior for NX2; runtime validation so far covers the PS5 only. The experiment changed both
the preemptive return and the nonpreemptive synchronization route. It does not establish which
part, or which interaction with the newer cache, caused the slowdown.

## Comparison switch

`EDEN_NX2_ORIGINAL_READBACK=0` restores the ordered path without changing source. In a dev
installation, add this line to `/data/homebrew/PPSA98515/dev-settings.txt`:

```text
env=EDEN_NX2_ORIGINAL_READBACK=0
```

Remove the line to restore the working default. Fully close and reopen the app after changing
the setting: the core reads it once per process. Keep further isolation on a separate branch
or use this switch so the working revision remains available.

## Validation

- User built, installed and confirmed the slowdown resolved on PS5 firmware 12.70.
- PS5 syntax-only compilation, Vulkan adaptation anchors and whitespace checks passed.
- The original experiment's reverse patch was checked before committing.
- A full race, a course transition, and checks for hangs or visual corruption remain pending.
- No performance claim has been made for other games or platforms.

The successful source was committed unchanged. No build or push was performed when recording
the fix. When publishing, publish the core branch before the port branch that references it.
