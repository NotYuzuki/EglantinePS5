# Native shader safeguards from the original PS5 patches

This change adapts the remaining native graphics handling from original patch
`0036-native-shader-fixes-from-swizard.patch` and early task/mesh refusal from
`0037-native-task-mesh-programs-refused.patch`. Patch 0036 credits
swizardlv/Eglantine `dc18aa2eef` for its native graphics fixes.

## Adapted behavior

- Permit native Vertex A programs through the graphics-environment stage validation. The
  environment already maps Vertex A to its shader stage.
- Represent header-described passthrough geometry with a synthetic EXIT and native address map,
  rather than trying to lower a native body. Preserve the newer environment's cached-range
  convention and its normal code-reuse path for other geometry programs.
- Identify active ISBEWR stage-buffer outputs without active AST attribute stores and reject
  them before emitting a partial program. Report the first stage-buffer access. Never-executed
  instructions do not affect classification. Other ISBEWR programs retain a specific write
  refusal. Existing geometry ISBERD behavior is preserved.

These guards do not implement task/mesh rendering. Such programs were already unsupported;
the change identifies their failure explicitly rather than pretending they are vertex programs.

## Existing or deliberately deferred behavior

The texture-gradient encoding fix from patch 0036 is already present. The target also has a
newer optional cooperative-matrix HMMA implementation and an explicit stub mode. This change
does not replace its tensor-mode defaults with the original patch's default stub behavior.
Reviewing that approximation requires a separate correctness/performance comparison.

## Validation

- Targeted host regression execution: 23 test cases, 1897 assertions passed, including the new
  refusal cases and existing return, geometry-lowering and geometry-exit tests.
- PS5 syntax-only compilation passed for shader environments and native Vulkan pipeline-cache
  integration. Vulkan adaptation anchors and whitespace checks passed.
- User confirmed Mario Kart World is working on PS5 firmware 12.70. Logs confirm the new
  task/mesh refusal reason is active and frames continue to be presented. Latest sampled
  windows were approximately 20–23 FPS; no matched-scene FPS improvement is claimed.
- This run does not establish that native Vertex A or passthrough geometry was exercised.
  Other games, those shader forms, and prolonged play remain unvalidated.

No PS5 emulator build or push was performed while preparing the change.
