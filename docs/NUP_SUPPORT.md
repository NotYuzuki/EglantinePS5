# NUP containers and conversion

Ports original core patches 0055–0060, 0063–0064 and 0066 together with the
launcher integration. Containers hold the extracted base, updates and DLC trees
in one file, with raw or zstd member extents and metadata/icons. The loader picks
the appropriate base/update ExeFS and merges the packaged RomFS layers. NUP-based
Switch 2 detection retains the current PS5 guest-core placement behavior.

## Using the launcher

Select a local extracted game in Library, then press R1 or open Game settings >
Convert to NUP. Choose Raw or Compressed, optionally select a PNG/JPEG icon, and
start conversion. The target is a sibling .nup file. Existing archives are refused.
The UI always keeps the extracted source folder, writes a .part during conversion,
and verifies the completed archive before reporting success. Circle requests
cancellation. Cancellation during verification leaves a complete but unverified
archive and reports that distinction. The library rescans after completion.

The launcher compresses small members at zstd level 1 and keeps members larger
than 8 MiB raw to avoid costly repeated decompression on random reads. Raw mode
keeps all members uncompressed. Both modes preserve package contents. A new copy
requires free disk space; the console's unavailable space query is shown as unknown,
and write failures are reported without deleting source files. Network/NTFS source
conversion is not offered by this native-filesystem converter.

The core writer retains its separate low-storage/resume API from the original
patches, but source deletion is not exposed or enabled by the launcher.

## Bounds and validation

The original follow-ups limit decoder windows to 8 MiB, individual cached decoded
members to 8 MiB and the shared cache to 32 MiB; larger compressed members stream
on demand. Additional parser checks bound the TOC to 128 MiB, each package metadata
block to 16 MiB and aggregate copied metadata to 128 MiB. Arithmetic is checked
before allocation/slicing; raw sizes must match their extents. Directory graphs
must be acyclic trees with at most 256 levels before recursive VFS construction.
These are implementation limits, not a claim that every external NUP is supported.

Host checks use synthetic files only:

- `python3 tools/check-nup.py`: actual writer/container/VFS round trips for raw and
  compressed output, base/update/DLC trees, large-member random reads, integrity
  verification, cancellation, overwrite refusal, write-failure cleanup, source
  retention and malformed layouts. It also exercises conversion with realpath
  denied and confirms the small-file optimization produces byte-identical output
  with fewer source reads. A registered core subset covers standalone updates,
  bare dumps, low-storage resume, refusal cases and malformed layouts when the
  existing local Catch2 libraries are present.
- `python3 tools/check-nup-space.py`: known/unknown free space, boundaries and
  partial-file credit in host and PS5-native configurations.

The isolated host conversion harness stubs control-metadata loading. Full-core
title/icon/version probing and its golden archive hash are not validated by that
harness; those registered tests remain unchanged for the full core suite. This
also does not exercise actual PS5 storage or launch a packaged game.

## Validation status

The isolated raw/compressed round trips, random reads, integrity checks,
cancellation, overwrite/write-failure handling, malformed-layout cases, source
retention and realpath-denied run passed with address/undefined-behavior sanitizers.
The registered host subset passed all five cases (61 assertions); the metadata
dependent cases and golden archive hash remain outside that isolated subset.
Small-file reuse reduced observed source reads from 2,574 to 1,380 and source
bytes from 44,947,336 to 42,502,024 on the synthetic fixture, with byte-identical
raw/fast/compressed output versus the optimization-disabled control.

The host/native free-space check passed. PS5 syntax checks passed for both new
container/writer units, the NUP/general/deconstructed loaders, metadata bridge,
services, launcher/library/settings/conversion UI, main and remote file filtering.
The actual CMake-derived core passed syntax checks and retained NUP Switch 2
detection plus current guest-core placement. Existing extracted-content,
load-failure/lifetime and launcher/autoboot checks passed.

The standalone host launcher rendered setup, icon picker, conversion, verification
and completion screens with zero GL errors. Setup and conversion layout screenshots
were inspected. No emulator build or console run was performed by the assistant.

Console validation is partial. The user exercised raw conversion on the PS5 and
reports that the descriptor writer is faster, but conversion still slows
substantially as it progresses. Completed console verification, metadata/icon/
version display and booting a newly converted NUP have not been confirmed.
Compressed conversion and console cancellation/refusal checks also remain pending.
The user elected to publish the current implementation and defer further
performance investigation. The source-retaining UI remains in place.


## Raw conversion slowdown follow-up

The user reported extremely slow raw conversion even after a concurrent FTP upload
finished. The initial console logs did not contain pack-stage I/O timings, so the
dominant console cost is not established yet.

The writer now tracks its output position and omits seeks that request the current
position. It still performs compressed-output rollback and final header backpatch
seeks, and initializes its position correctly when resuming. On the updated host
fixture this reduced raw output seeks from 428 to 1 and compressed output seeks
from 224 to 2, with byte-identical archives against a seek-cache-disabled control.
The fixture contains incompressible data that forces an actual compression rollback.
Integrity, cancellation, write failure, source retention, malformed layout and
registered resume cases continue to pass under sanitizers. Updated PS5 writer and
service syntax checks passed. No console speed improvement is claimed yet.

PackOptions.measure_io optionally collects wall-clock timings; the launcher enables
it and emits `[EglantinePS5] nup:` records roughly every five seconds, plus phase
transitions. Records include file/byte counts, elapsed/discovery/counting/file time,
open/stat/read/write/output-seek/hash-update time and operation counts. file_ms
includes close and other per-file work; hash_ms measures digest updates, and
seek_ms covers output seeks rather than source rewinds. Overlapping timer totals
are diagnostic categories and should not be summed. Verification start/result
records distinguish it from conversion. Source files are still kept.

The subsequent conversion-only run established the large-file output-write
bottleneck described below.

## Measured large-file write bottleneck

The next console run had only 27 source files. At 229 seconds, the profiling
record showed 214 seconds inside output writes, 15 seconds inside reads and less
than one second hashing. Around 19% the writer took roughly one minute per 64 MiB.
The no-op seek fix was active (`seeks=0` during the body), so small-file seek
overhead does not explain this measured stall.

The launcher now selects an alternate POSIX descriptor output path, issuing
bounded 256 KiB writes instead of fwrite. It preserves partial-write/EINTR handling,
compression rollback, truncation, final fsync, rename and integrity verification.
PackOptions.descriptor_output defaults off, retaining stdio for other callers and
comparisons; unsupported Windows descriptor mode falls back to stdio. Console
start records identify `output=descriptor chunk_kib=256` so the comparison can be
verified. Host raw/fast/compressed archives match the stdio path byte for byte.
The user reports faster conversion after this change, with substantial slowdown
still present. This is qualitative console feedback, not a controlled throughput
benchmark or proof that stdio alone caused the stall.

Progress reports now occur every 4 MiB rather than 64 MiB. The UI samples speed
only when bytes advance and divides by the entire interval since the prior sample.
It no longer decays its estimate toward zero on frames with unchanged progress;
after five seconds without a new sample it shows that it is processing the current
file instead of presenting a stale ETA. `python3 tools/check-nup-progress.py`
executes the actual sampler with synthetic sparse updates, phase changes and resets.
The descriptor path has been exercised on the console; conversion performance
remains an open issue. The revised ETA behavior has not been separately confirmed
by the user.

The descriptor comparison, realpath-denied run, cancellation/write-failure checks,
and all five registered host cases now pass under sanitizers (61 assertions),
including low-storage resume on both output backends. The actual sparse-update
sampler test passes. PS5 syntax checks pass for the descriptor writer, launcher
service selection and conversion UI. Metrics count logical sink writes; one sink
write may issue multiple bounded native writes. No emulator build was run.
