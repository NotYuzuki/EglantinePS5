/* What the driver's GPU-visible memory is for (EglantinePS5's adaptation): live bytes and counts by
 * kind, reported to stderr each time the total passes another 512 MiB and when a mapping is
 * refused. A buffer's kind comes from RADV's priority for it: the application's memory (0-28), query
 * pools (29), descriptors, upload buffers and fences (30), shaders, scratch and command buffers (31).
 * Sparse ranges are mapped whole to the shared zero block until memory is bound into them, so they
 * take GPU mappings without taking memory. */
/* Live device memory by the caller of vkAllocateMemory (radv_device_memory.c). */
void radv_eden_memory_sites_report(void);

static const char *const radv_ps5_tally_names[RADV_PS5_TALLY_KINDS] = {
   "application", "query pools", "descriptors/upload/fences", "shaders/scratch/command buffers",
   "sparse ranges (zero block)", "large submissions",
};
static int64_t radv_ps5_tally_bytes[RADV_PS5_TALLY_KINDS];
static int64_t radv_ps5_tally_count[RADV_PS5_TALLY_KINDS];
/* The application's buffers by size: to 64 KiB, to 2 MiB, to 64 MiB, larger. */
static int64_t radv_ps5_tally_app_bytes[4];
static int64_t radv_ps5_tally_app_count[4];
/* Every live mapping radv_ps5_memory_alloc made, whatever it is for. */
static int64_t radv_ps5_tally_direct_bytes;
static int64_t radv_ps5_tally_mark;
static int32_t radv_ps5_tally_refusals;

unsigned
radv_ps5_tally_kind(unsigned priority)
{
   return priority <= 28 ? RADV_PS5_TALLY_APPLICATION : priority == 29 ? RADV_PS5_TALLY_QUERY
                       : priority == 30                          ? RADV_PS5_TALLY_DESCRIPTOR
                                                                  : RADV_PS5_TALLY_SHADER;
}

static unsigned
radv_ps5_tally_size_class(uint64_t bytes)
{
   return bytes <= (UINT64_C(64) << 10) ? 0 : bytes <= (UINT64_C(2) << 20) ? 1 : bytes <= (UINT64_C(64) << 20) ? 2 : 3;
}

void
radv_ps5_tally_report(const char *why)
{
   char line[1024];
   const int64_t direct = p_atomic_read(&radv_ps5_tally_direct_bytes);
   /* Direct memory no buffer or large submission accounts for: the queues' rings and the like. */
   int64_t other = direct;
   for (unsigned k = 0; k < RADV_PS5_TALLY_KINDS; ++k)
      if (k != RADV_PS5_TALLY_SPARSE)
         other -= p_atomic_read(&radv_ps5_tally_bytes[k]);
   int at = snprintf(line, sizeof(line), "radv/ps5 memory (%s): direct %" PRId64 " MiB (other %" PRId64 " MiB)",
                     why, direct >> 20, other >> 20);
   for (unsigned k = 0; k < RADV_PS5_TALLY_KINDS && at > 0 && at < (int)sizeof(line); ++k)
      at += snprintf(line + at, sizeof(line) - at, "; %s %" PRId64 " MiB in %" PRId64, radv_ps5_tally_names[k],
                     p_atomic_read(&radv_ps5_tally_bytes[k]) >> 20, p_atomic_read(&radv_ps5_tally_count[k]));
   if (at > 0 && at < (int)sizeof(line))
      snprintf(line + at, sizeof(line) - at,
               "; application by size: to 64 KiB %" PRId64 " (%" PRId64 " MiB), to 2 MiB %" PRId64 " (%" PRId64
               " MiB), to 64 MiB %" PRId64 " (%" PRId64 " MiB), larger %" PRId64 " (%" PRId64 " MiB)",
               p_atomic_read(&radv_ps5_tally_app_count[0]), p_atomic_read(&radv_ps5_tally_app_bytes[0]) >> 20,
               p_atomic_read(&radv_ps5_tally_app_count[1]), p_atomic_read(&radv_ps5_tally_app_bytes[1]) >> 20,
               p_atomic_read(&radv_ps5_tally_app_count[2]), p_atomic_read(&radv_ps5_tally_app_bytes[2]) >> 20,
               p_atomic_read(&radv_ps5_tally_app_count[3]), p_atomic_read(&radv_ps5_tally_app_bytes[3]) >> 20);
   fprintf(stderr, "%s\n", line);
   radv_eden_memory_sites_report();
}

/* Reports when the GPU-visible total (direct memory and sparse ranges) first passes each 512 MiB. */
static void
radv_ps5_tally_check(void)
{
   const int64_t total = p_atomic_read(&radv_ps5_tally_direct_bytes) +
                         p_atomic_read(&radv_ps5_tally_bytes[RADV_PS5_TALLY_SPARSE]);
   const int64_t mark = total >> 29;
   int64_t seen = p_atomic_read(&radv_ps5_tally_mark);
   while (mark > seen) {
      const int64_t was = p_atomic_cmpxchg(&radv_ps5_tally_mark, seen, mark);
      if (was == seen) {
         char why[48];
         snprintf(why, sizeof(why), "passed %" PRId64 " MiB", mark << 9);
         radv_ps5_tally_report(why);
         return;
      }
      seen = was;
   }
}

void
radv_ps5_tally_add(unsigned kind, int64_t bytes)
{
   if (kind >= RADV_PS5_TALLY_KINDS)
      return;
   p_atomic_add(&radv_ps5_tally_bytes[kind], bytes);
   p_atomic_add(&radv_ps5_tally_count[kind], bytes < 0 ? -1 : 1);
   if (kind == RADV_PS5_TALLY_APPLICATION) {
      const unsigned size_class = radv_ps5_tally_size_class((uint64_t)(bytes < 0 ? -bytes : bytes));
      p_atomic_add(&radv_ps5_tally_app_bytes[size_class], bytes);
      p_atomic_add(&radv_ps5_tally_app_count[size_class], bytes < 0 ? -1 : 1);
   }
   if (bytes > 0)
      radv_ps5_tally_check();
}

static void
radv_ps5_tally_direct(int64_t bytes)
{
   p_atomic_add(&radv_ps5_tally_direct_bytes, bytes);
   if (bytes > 0)
      radv_ps5_tally_check();
}

/* After a refused mapping: the first four. */
static void
radv_ps5_tally_refused(void)
{
   if (p_atomic_inc_return(&radv_ps5_tally_refusals) <= 4)
      radv_ps5_tally_report("a mapping was refused");
}

