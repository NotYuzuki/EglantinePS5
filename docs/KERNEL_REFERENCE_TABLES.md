# Size kernel reference-count tables to each managed region

Adapted from the reference-count sizing hunk of original PS5 patch 0014. Each kernel memory
manager previously allocated a u16 entry for every page of intended emulated RAM. Its reference
count indices are offsets into that manager's own heap, so it only needs `size / PageSize`
entries. This changes the table allocation without changing page ownership or allocation policy.

`OpenFirst`, `Open` and `Close` are the three indexing sites. Each starts at `GetPageOffset`,
which is relative to the manager's KPageHeap. The outer memory-manager operations split runs
at `GetPageOffsetToEnd` before passing them to the region manager. The allocation is initialized
to zero by vector resize as before. Heap address and size are page-aligned by the existing
initialization contract.

The table storage per manager changes from intended-DRAM-pages times sizeof(u16) to
managed-region-pages times sizeof(u16). Actual memory savings depend on the number and sizes
of managers; no measured total or FPS improvement is claimed.

The remaining old patch's freed-page bitmap and zero-fill bypass are not ported. The PS5
HostMemory adapter delegates clears to ClearGuestMemory, which leaves SlotZero untouched,
returns fully cleared slots to zero backing and clears nonzero partial slots. The target's
non-PS5 host-memory implementation has separate used/dirty-granule tracking. Both remain intact.

PS5 syntax-only compilation, Vulkan adaptation anchors and whitespace checks passed. The user confirmed Mario Kart World is working on PS5 firmware 12.70. The captured run
continued presenting frames (recent windows approximately 20 FPS), with no allocation failures
or kernel reference-count assertions found in the log. Repeated session reuse, prolonged play
and other games remain unvalidated; actual memory savings and FPS gains remain unmeasured.
No PS5 emulator build, commit or push was performed while preparing this change.
