# Optional sampled-texture trimming

Sampled-texture trimming is disabled by default. It trades texture detail for lower memory
use by omitting the highest-resolution mip of eligible textures. Normalized texture coordinates
continue to address the retained mip chain; explicit mip selection can look different.

## Enable for a development comparison

Use a dev build (`make dev DEV_TITLE=0400C3F00006E000`) and install `build/dev/PPSA98515`.
Add the following to `/data/homebrew/PPSA98515/dev-settings.txt`:

```text
env=EDEN_TEXTURE_TRIM_KIB=512
```

The value is a minimum guest texture size in KiB, clamped to 256–32768 when nonzero. Use
`env=EDEN_TEXTURE_TRIM_KIB=0`, or remove the line, to disable trimming. Fully close and reopen
the app after changing it. Release builds do not read these development environment overrides;
the PS5 launcher does not set a trimming default.

Only sampled, single-layer 2D color textures with a mip chain, one sample and a guest size
below 32 MiB qualify. The upload path must support the format. Render-target and DMA lookup
paths reject sampled-only trimmed images; image joins avoid copying their missing mip. Plain
formats use the existing mip-skipping layer upload path. The newer cache already excludes
trimmed levels from partial-write tracking.

Adapted from `0022-ps5-trim-large-sampled-textures.patch` in the original EglantinePS5 port.
The target's existing large-array and native ASTC trimming behavior remains available independently.

## Validation and limits

- PS5 firmware 12.70, Mario Kart World, with Fast GPU enabled.
- User confirmed startup with trimming disabled and with the 512 KiB setting enabled.
- User reported broadly comparable FPS after comparison; no FPS improvement is claimed.
- The enabled log reported 256 trimmed textures and approximately 158 MiB of estimated savings.
  This counter is an estimate from guest texture sizes, not a measurement of total GPU memory.
- PS5 syntax-only checks passed for image metadata, Vulkan texture-cache code and rasterizer
  cache instantiation; Vulkan adaptation anchors and whitespace checks passed.
- Other games, prolonged play and every supported format have not been validated.

Earlier startup hangs also occurred with the original source baseline. Restoring Fast GPU
allowed it to load. Those confounded trials do not establish a trimming regression or a defect
in the image metadata. The single-GPU-thread loading-screen hang remains a separate issue.
