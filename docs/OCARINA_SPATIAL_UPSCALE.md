# Ocarina spatial replacement

## Isolation

Outer and core branches are both `codex/ocarina-spatial-fsr`, in
`/home/esotericizm/exploitizmPS5-ocarina-fsr`, based on
`claude/lichii-update` at `23cde59` and `e457cfd52a`.
The other session's uncommitted mesh/HMMA files were neither copied nor changed.
Both repositories publish this work on `codex/ocarina-spatial-fsr`.

## Captured movie contract

The first console capture is saved in `.local/console-trace/eden_log.txt`.
It records 167 dispatches over 12 renderer present boundaries. Complete
repetitions have a video conversion followed by 13 neural dispatches:

```
f9e5b02b5fabff78  video conversion, RETAINED
2f5791cea2a85397  network prepass
313d724f3e214203
df2c83cf2ad70790
1fdb95ea2565f49c
0be2f024aea21a6c
0618cc4a3ac8f33a
0618cc4a3ac8f33a
e27336d38f1cc8f4
1fdb95ea2565f49c
ecebb02dec6faa17
313d724f3e214203
2a366b38546a4d5e
6093c9143ba0904d  output/resolve
```

All network keys are NativeProgramKey values and contain HMMA.
Offline source dumps, SHA-256 values, complete nvdisasm output and tool version
are indexed in `.local/disassembly`. These are ordinary tool observations,
not REA Evidence; the installed REA launcher could not locate Node in WSL.

| Pass | cbuf0 byte slot | Observed resource |
| --- | --- | --- |
| Video conversion | 0x58 | R8 luma, 1920x1080 |
| Video conversion | 0x60 | G8R8 chroma, 960x540 |
| Video conversion | 0x68 | RGBA8 UNORM converted RGB, 1920x1080 |
| Neural prepass | 0x58 | The converted RGB image, 1920x1080 |
| Neural resolve | 0xa0 / 0xc0 | RGB10A2 network intermediate, 1920x1080 |
| Neural resolve | 0xa8 | Original converted RGB image, 1920x1080 |
| Neural resolve | 0xc8 | Display output RGBA8 UNORM, 2560x1440 |

Disassembly confirms the prepass TLD slot 0x16 (byte 0x58) and resolve SUST
slot 0x32 (byte 0xc8). The original colour at resolve 0xa8 is a bound descriptor
whose address and raw contents match the prepass image. Replacement reads it
instead of the network's intermediate at 0xa0. No jitter is required.

The observed 960x540 resource is chroma, not the movie's RGB source. This
capture alone did not establish gameplay's resolution or resource formats; the
separate gameplay capture below does.

## Replacement and guards

CapturedPasses has per-game offsets, optional jitter and format fields.
Ocarina's movie pair accepts only the captured 1920x1080-to-2560x1440 RGBA8
UNORM geometry, distinct addresses, identity swizzles, non-sparse, unmultisampled,
single-mip 2D surfaces. It reuses TextureViewHeader normalization: native
descriptors already address the selected view, so legacy layer bookkeeping
does not add an array prefix. Array texture types remain rejected.

The latest built candidate recognizes the captured programs before shader
translation. A bounded cache checks the complete original program bytes before
reusing a key. The first validated prepass suppresses the captured neural chain
and resolves the actual output descriptor at its final pass. Console validation confirms all 13 skips from the first evaluation. Previously installed
candidates ran one native warm-up evaluation.

Subsequent prepasses validate the input and cached output TIC and prepare a
spatial draw before suppressing neural work. The captured 13-pass sequence is
checked; unrelated compute remains active. The resolve draws the spatial output
again from the original colour, without history, motion vectors or jitter.

A sequence mismatch prevents priming the next evaluation. Once any pass was
suppressed, the current evaluation cannot fall back to an incomplete native
network. It retains the prepared image and suppresses remaining known network
programs. A failed output contract disables subsequent skips and logs an error.
Unprepared incomplete evaluations expire after 64 dispatches.

The Vulkan path uses AccelerateTemporalUpscale with use_history=false:
bilinear spatial sampling followed by the existing FSR 1 RCAS sharpening.
It is the existing Star Fox spatial route, not EASU or FSR 3 frame generation.
RGBA8 output alpha is explicitly initialized to one by the spatial pass;
the existing RGB-only write behavior remains for Star Fox's float output.

`EDEN_NX2_DLSS_REPLACEMENT=native` runs the original network.
`fsr` selects the spatial candidate. With no override, the normal DLSS-network
setting/blocker governs replacement.

## Capture and validation

```
env=EDEN_NX2_NO_MESH=1
env=EDEN_NX2_DLSS_REPLACEMENT=fsr
env=EDEN_NX2_OOT_DLSS_TRACE_FRAMES=12
env=EDEN_NX2_OOT_DLSS_TRACE_TRIGGER=/data/eglantineps5/logs/oot-trace.now
```

Discovery runs before lowering or rejection, logs ordered keys, launch geometry,
cbuf0 words and mapped TIC candidates (up to 4096 cbuf bytes), and ends after
the requested present boundaries or 4096 dispatches. Handle-shaped constants
are labelled candidates; shader accesses and resource identity must corroborate
their role. Zero/unset frame count disables discovery.

After the initial window, uploading the trigger file starts another bounded
capture at the next observed dispatch, including unknown gameplay programs.
The file is consumed. The trace itself never changes replacement decisions.
Performance samples must be taken after the trace window closes.

Expected candidate evidence is `Ocarina DLSS spatial: applied=true` and
`skipped=13/13 native_output_dispatch=false`. First evaluations log each
suppressed native key with `GPU dispatch=false`.

The saved native baseline is `.local/console-trace/native-movie-baseline.txt`.
Steady movie samples between 60 and 420 seconds have median 38476 us/frame,
25.99 FPS. The earlier 37-38.4 ms historical estimate is consistent with these
samples, but is not used as the controlled comparison.

Host validation: 16 test cases, 564 assertions passed, including logged TIC
validation, chroma rejection, exact sequence membership, spatial-only RGBA8
support, and prevention of incomplete-native-output fallback.
The candidate builds with `make dev DEV_TITLE=0400486024dec000 JOBS=12`.

## Verified movie result

The owner confirmed that the replacement movie image looks correct.
The console log in .local/console-after/eden_log.txt records all 13 neural
programs as suppressed after the initial warm-up, including prepass and resolve:
```
Ocarina DLSS spatial: applied=true temporal=false ... skipped=13/13 native_output_dispatch=false
Ocarina DLSS suppressed ... GPU dispatch=false
```

Initial fully post-trace movie samples were 16923 us/frame (59.09 FPS).
The longer saved after-window currently has median 17415.0 us/frame;
samples and window bounds are recorded in .local/movie-timing-summary.json.
No partial-chain contract error was reported in that saved movie capture.
The owner subsequently confirmed working gameplay and a movie presented at a
locked 60 FPS with eight AV1 workers. Audio still moves ahead of the movie:
present FPS does not prove decoded movie frames are keeping pace.

## Captured gameplay contract

A separate console capture in `.local/console-gameplay` identifies the standalone
neural program `f48db7481be856b9`: 1410 instructions, 22560 bytes, 188 HMMA.
Its source is cbuf0 byte slot 0xc0 (854x480 E5B9G9R9 FLOAT observed); output is
slot 0xd0 (2560x1440 B10G11R11 FLOAT). The guard permits bounded source-size
variation and aspect rounding, with the captured output size and exact formats.
It validates the complete program before skipping its dispatch. Other gameplay
compute remains active. All 23 saved program dumps match their canonical
NativeProgramKey and complete capture length.

## Gameplay progress fix

The first gameplay hang was a GPU-thread sparse-memory callback waiting on the
same registry mutex held by its caller. The adapted existing fix originates at
`4cd10d83f1346035cb4f1fa962277bf67df63fb7` and the earlier
`0013-nx2-no-gpu-thread-lock-held-across-waits.patch`.
Registry callbacks, GPU waits and region retirement run outside registry and
address-space locks. Active callback counters protect listener lifetime.
An extracted host harness reproduced the original deadlock and passed 20
stress runs with the change. The owner then confirmed gameplay works.

## Audio drift investigation and latest result

With automatic AV1 threading, late decode calls cost 17 19 ms per output.
Opt-in eight-worker AV1 decode with the full allowed CPU mask reduced measured
decode calls to roughly 0.5 1.3 ms and the owner reported stable 60 FPS presents.
The movie still took about 167 seconds for 154.217 seconds of PTS progression;
most excess time occurred in the first 1200 outputs.

Forcing AV1 max_frame_delay=1 did not fix sync. Split counters show waiting for
free output images rather than input packets; release-fence waits themselves
were negligible. No frame dropping or clock adjustment has been introduced.

The latest verified package adds a bounded queue trace:
`EDEN_NX2_VDEC_TRACE_AT_FRAME=600`,
`EDEN_NX2_VDEC_TRACE_FRAMES=120`. It records submit, release, acquire, completion
and queue sizes to distinguish slow client image return from decoder starvation.
This package is installed and its settings were read back and verified. The
next console run restores automatic frame delay (0), keeps eight AV1 workers
and captures only a short window. The owner confirmed the movie picture is correct and audio stays in sync on
this installed build. This is a successful observed run, not proof that the
remaining early image-handoff delays have been eliminated.

The threading, affinity and profiling overrides are opt-in; default codec and
other-worker behavior is preserved. The three protected mesh/HMMA source files
remain unchanged. The work is published on the matching feature branches in both repositories.


## Latest console evidence

Saved run: `.local/console-queue/eden_log-complete.txt`.
The first movie replacement logs skipped=13/13 at 29.615 seconds. No
`Ocarina spatial output unavailable` error occurs in the complete saved run.
Main movie reaches EOS at PTS 154217000 us; gameplay later logs
`f48db7481be856b9`, 188 HMMA, GPU dispatch=false.

The bounded trace covers two separate decoder sessions, each with frames
600 719; frame numbers reset between sessions. In the main movie window,
write-to-acquire median is 0.695 ms, acquire-to-return median 44.653 ms,
and return-to-next-write median 0.805 ms. The worker is ready quickly after
the client returns an image. Thousands of compressed packets are queued.
These observations do not establish a readiness-event or clock bug.
The earlier lossless-input fix (#49/add7922124) must be preserved.

Owner report for this build: picture correct, audio stays in sync.
First playable scene verification remains pending. A later log contains
out-of-range TIC/unknown-format assertions, so this run alone cannot establish
that all gameplay rendering is correct.


## Reproduce the successful console configuration

In addition to the existing mesh-disable and graphics-dump settings, the
successful installed candidate used:

```
env=EDEN_NX2_DLSS_REPLACEMENT=fsr
env=EDEN_NX2_VDEC_THREADS=8
env=EDEN_NX2_VDEC_ALL_CORES=1
env=EDEN_NX2_VDEC_FRAME_DELAY=0
```

The bounded decoder/dispatch tracing and profiling settings above were also
enabled for that run. Those diagnostics are optional; changes take effect
after restarting the application. This is a checkpoint with the owner's movie
validation complete and the final playable-scene check pending, not a claim
that the later texture assertions are fixed.


## Wrapper PR merge resolution

Wrapper PR #23 resolves the divergent submodule pointers by selecting
`25b75c6786d4e943158afa1747af6edea6e5637e`, the existing core PR #1 merge
commit. It contains both the spatial checkpoint (`0c419316e9`) and the base's
debug aids (`98c40ec84e`). The base handoff document is retained.
No manual edits were made to the protected mesh/HMMA source files during this
resolution. The console validation above applies to the earlier installed
checkpoint; this combined core revision has not yet been console-tested.

Combined-revision checks: PS5 build/package inventory, alignment and imports PASS;
worker affinity checks and git diff checks PASS.
