# Optional texture layer upload batches

The core already updates large plain textures through its partial-row refresh path. This change
keeps that implementation and adds an opt-in staging target to the separate layer-upload path.

In a development build, set the following in `/data/homebrew/PPSA98515/dev-settings.txt`:

```text
env=EDEN_TEXTURE_UPLOAD_BATCH_KIB=8192
env=EDEN_TEXTURE_TRIM_KIB=0
```

Apply these settings after installation: the installer can replace dev-settings.txt.
Keep Fast GPU enabled for the comparison, and fully close/reopen the application. Zero or an
unset upload-batch setting preserves the prior paths. Nonzero values are clamped to 256–65536
KiB. Release builds do not read these development overrides; no launcher default is added.

Full uploads of eligible layered 2D images are routed through layer uploads. Existing dirty-layer
uploads are also grouped by the actual retained bytes per layer. Plain and accelerated upload
code continues to handle mip offsets and selected layer IDs. Each split group finishes before
the next staging allocation, allowing storage reuse. A single oversized layer remains one upload:
the setting is a group target rather than a hard allocation cap. Single-layer rectangle batching
and changes to row-based partial refresh are outside this change.

Additional completion waits may reduce throughput even when they lower transient staging memory.
No FPS or console memory improvement has been established. Runtime logs identify images actually
split into batches. The host check exercises the real grouping method with sparse layer IDs,
completion ordering, opt-out cases and extreme per-layer sizes; PS5 syntax checks cover cache
instantiation.

Console comparison on PS5 firmware 12.70 with Mario Kart World confirmed startup and gameplay
with batching enabled; the user reported no noticeable issues. Logs show a 101-layer image
with 696320 bytes per layer grouped in batches of 12: approximately 67.1 MiB of selected layers
becomes approximately 8 MiB per staging group. A separate three-layer image with 18 MiB per
layer uses one layer per group, illustrating the oversized-layer exception. These values describe
staging requests, not a measurement of total graphics memory. Recent enabled windows were
approximately 16–20 FPS; no matched-scene FPS improvement has been established. No allocation
or upload failures were found in the captured log. Prolonged play and other games remain untested.
