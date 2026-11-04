# VFS read and buffer lookup bounds

Groups two independent correctness fixes from original PS5 patches 0065 and 0042. Keep the
core fixes as separate commits even when publishing one port PR for their combined pointer.

- VectorVfsFile::Read returns zero before subtracting offsets or forming a source pointer when
  the requested length is zero or the offset is at/past EOF. Valid reads remain clipped at EOF.
- BufferCache::FindBuffer rejects zero addresses and page indices outside page_table.size()
  before indexing. Valid existing-buffer lookup, synchronization, sparse compatibility and
  buffer-creation behavior are unchanged. This guards the lookup index, not every possible
  malformed address/size range in the renderer.

The host check extracts and executes the real methods with small backing data and a checked
page-table/creation harness. It covers empty files, zero-length reads, EOF and maximal offsets,
clipped reads with destination canaries, invalid device addresses with no table access, and
valid lookup/create/sparse fallback. Each method with its guard removed must fail a negative control. Sanitizers
cover the host exercise; PS5 syntax validation separately covers actual core integration.

No FPS improvement is expected. The host boundary checks and both guard-removal negative
controls passed under address/undefined-behavior sanitizers. PS5 syntax checks passed for VFS,
Vulkan buffer-cache and rasterizer integration, along with adaptation anchors and whitespace
checks. User confirmed Mario Kart World is running on PS5 firmware 12.70. The captured run continued
presenting frames, with no relevant VFS/buffer-cache assertions or allocation failures found
in the log. This is a normal-operation smoke test, not evidence that malformed input reached
either guard on the console; those boundaries are covered by the host tests. Prolonged play
and other games remain unvalidated. The related ArrayVfsFile and broader VFS write-range behavior are not changed here.
