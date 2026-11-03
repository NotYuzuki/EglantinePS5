# Release per-pipeline Vulkan shader modules after creation

Adapted from the original PS5 port's patch 0017 (the PR142 shader-module release change).
Compute pipelines release their owned module after successful creation or a caught Vulkan
creation failure. Graphics pipelines release their shared module-set reference at those same
points. Release happens before publishing `is_built` or notifying waiters on both paths.

The graphics configuration function is selected before queuing the build: it inspects the module
set, which a synchronous or fast asynchronous build may then release. The remaining graphics
module accesses are inside pipeline creation, before the reset. Statistics collection uses the
created pipeline rather than the input modules.

This core shares graphics modules through ShaderReuseCache. Resetting a pipeline's shared
pointer does not invalidate the cache or another pending build's reference. Cached graphics
modules remain retained for reuse; no reduction in that cache's retained memory is claimed.
When there are no other owners, the normal shader-module wrapper destroys the modules.

PS5 syntax-only compilation passed for both adapted pipeline wrappers; Vulkan adaptation
anchors and whitespace checks passed. The user confirmed Mario Kart World is working on PS5 firmware 12.70. The captured run
continued presenting frames (recent windows approximately 19–21 FPS), with no graphics/compute
pipeline-build failures or allocation errors found in the log. This does not prove coverage of
every cache-replay or asynchronous-compilation path; prolonged play and other games remain
unvalidated. No memory or
FPS gain has been measured, and Vulkan allocation/build failure paths have not been exercised
on the console. No shader translation, pipeline cache keys or runtime defaults are changed.
