# Deferred instance continuation

Adapts the missing continuation handling from original PS5 patch 0061. The core
already flushes deferred instance draws before unrelated register writes, but it
treated every resulting batch as the beginning of the loop. After a split, this
reused the initial base instance and included the initial instance again.

Register-write flushes now retain the number of deferred instances already
submitted. Following batches advance the base and omit the initial-instance
allowance. Macro completion, a new first-instance draw, and the current immediate
array draw path clear continuation. The newer immediate array implementation is
preserved rather than replaced by the original patch's batching implementation.

Run `python3 tools/check-deferred-instances.py` for the host-only regression check.
It extracts the actual draw methods and records submitted base/count/indexed
values. Cases cover unsplit loops, multiple splits, empty intermediate flushes,
indexed draws with index state restated, loop termination, a new first instance,
and immediate array draws. Removing either the count correction or the base
advance must independently fail. Register-writing and macro completion callers
are checked separately. This does not execute Vulkan or emulate game macros.

The host regression cases and both negative controls passed under address and
undefined-behavior sanitizers. PS5 syntax checks and adaptation anchors passed.
The user confirmed the dev build is working on the PS5 with Mario Kart World.
This is a normal-operation smoke test; it does not establish that the game
exercised a split instance loop. No FPS improvement is claimed.
