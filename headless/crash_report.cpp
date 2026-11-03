// SPDX-License-Identifier: GPL-3.0-or-later
// The crash report (crash_report.h). What the handler runs is in this file and uses only system
// calls, atomics and the buffers below.
#include "crash_report.h"

#include <signal.h>
#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <fstream>
#include <thread>
#include <unistd.h>
#include <vector>
#ifdef __linux__
#include <ucontext.h>
#endif

extern "C" {
// src/lifecycle.c: both return only when the system refuses.
int eden_restart_app(void);
int eden_exit_app(void);
#ifdef PS5_NATIVE
int sceKernelDebugOutText(int, const char*);
int sceKernelUsleep(unsigned);
std::int64_t sceKernelGetDirectMemorySize();
std::int32_t sceKernelAvailableDirectMemorySize(std::int64_t, std::int64_t, std::size_t, std::int64_t*, std::size_t*);
std::size_t eden_heap_committed(void);
std::size_t eden_heap_large_held(unsigned* blocks);
#endif
// The app's code, from the link (tools/unwind.ld).
extern const char __eden_text_start[];
extern const char __eden_text_end[];
}

namespace Eden::Crash {
namespace {

struct Kind {
    int signal;
    const char* name;
    const char* meaning;
    bool fault;  // the signal comes with the address that caused it
};
constexpr Kind kKinds[] = {
    {SIGSEGV, "SIGSEGV", "invalid memory access", true},
    {SIGBUS, "SIGBUS", "invalid memory access", true},
    {SIGILL, "SIGILL", "illegal instruction", true},
    {SIGFPE, "SIGFPE", "arithmetic error", true},
    {SIGABRT, "SIGABRT", "abort", false},
    {SIGSYS, "SIGSYS", "system call refused", false},
    {SIGTRAP, "SIGTRAP", "trap", true},
};

// The interrupted thread's registers, as the report prints them.
struct Registers {
    std::uint64_t rip, rsp, rbp, rax, rbx, rcx, rdx, rsi, rdi, r8, r9, r10, r11, r12, r13, r14, r15, rflags;
    std::uint64_t trap, error;
    bool whole;  // false: only rip and rsp are known (Fail)
};

enum : int { kIdle, kReporting, kGivingUp };
std::atomic<int> state{kIdle};
std::atomic<std::uintptr_t> reporter{0};  // the thread writing the report
std::atomic<bool> helper_failed{false};   // the system refused both the restart and the exit
std::atomic<bool> leave{false};           // close the app instead of starting it again
std::atomic<bool> game_started{false};
std::atomic<bool> game_running{false};  // the session is a game (the hang watch)

bool installed = false;
bool restarted_process = false;
std::time_t started_at = 0;
long utc_offset = 0;
char logs[200];
char version[40];
char sessions[2][400];
std::atomic<unsigned> session_now{0};
std::atomic<bool> reported{false};  // the report is written: the helper threads act
int probe_pipe[2] = {-1, -1};       // memory is read through it (Copy)
int hang_pipe[2] = {-1, -1};        // the hang watch's own (WriteHangReport)

std::uintptr_t CodeStart() noexcept { return reinterpret_cast<std::uintptr_t>(__eden_text_start); }
std::uintptr_t CodeEnd() noexcept { return reinterpret_cast<std::uintptr_t>(__eden_text_end); }
bool InCode(std::uint64_t address) noexcept { return address >= CodeStart() && address < CodeEnd(); }

void Sleep(unsigned microseconds) noexcept {
#ifdef PS5_NATIVE
    sceKernelUsleep(microseconds);
#else
    usleep(microseconds);
#endif
}

// The kernel log on the console (stderr may be what broke), stderr elsewhere.
void Note(const char* line) noexcept {
#ifdef PS5_NATIVE
    sceKernelDebugOutText(0, line);
#else
    (void)!write(2, line, std::strlen(line));
#endif
}

// Text put together without the C library's formatting, which allocates and takes locks.
template <std::size_t N>
struct Text {
    char data[N];
    std::size_t size;

    void Clear() noexcept {
        size = 0;
        data[0] = '\0';
    }
    void Put(char c) noexcept {
        if (size + 1 < N) data[size++] = c;
        data[size] = '\0';
    }
    void Put(const char* text) noexcept {
        for (; text && *text; ++text) Put(*text);
    }
    void Hex(std::uint64_t value, int digits = 1) noexcept {
        char buffer[16];
        int count = 0;
        do {
            buffer[count++] = "0123456789abcdef"[value & 15];
            value >>= 4;
        } while (value != 0 || count < digits);
        while (count > 0) Put(buffer[--count]);
    }
    void Dec(std::uint64_t value, int digits = 1) noexcept {
        char buffer[20];
        int count = 0;
        do {
            buffer[count++] = static_cast<char>('0' + value % 10);
            value /= 10;
        } while (value != 0 || count < digits);
        while (count > 0) Put(buffer[--count]);
    }
    // An address as the report writes it: an offset into the app's code, which is its address in
    // the build's llvm-pie.elf, or the bare address of anything else (a system library, code the
    // emulator generated, data).
    void Address(std::uint64_t address) noexcept {
        if (InCode(address)) {
            Put("eboot+0x");
            Hex(address - CodeStart());
        } else {
            Put("0x");
            Hex(address, 16);
        }
    }
};
// One report per process: the state above lets a single thread in.
Text<24 * 1024> report;
Text<64> report_name;
Text<300> report_path, note_path;
Text<80> note;
Text<600> summary;

// Days since 1970-01-01 to a calendar date and back (Howard Hinnant's algorithms).
void CivilFromDays(long long days, int& year, unsigned& month, unsigned& day) noexcept {
    days += 719468;
    const long long era = (days >= 0 ? days : days - 146096) / 146097;
    const auto doe = static_cast<unsigned>(days - era * 146097);
    const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const unsigned mp = (5 * doy + 2) / 153;
    day = doy - (153 * mp + 2) / 5 + 1;
    month = mp < 10 ? mp + 3 : mp - 9;
    year = static_cast<int>(yoe + era * 400) + (month <= 2);
}
long long DaysFromCivil(int year, unsigned month, unsigned day) noexcept {
    year -= month <= 2;
    const long long era = (year >= 0 ? year : year - 399) / 400;
    const auto yoe = static_cast<unsigned>(year - era * 400);
    const unsigned doy = (153 * (month > 2 ? month - 3 : month + 9) + 2) / 5 + day - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + static_cast<long long>(doe) - 719468;
}

struct Stamp {
    int year;
    unsigned month, day, hour, minute, second;
};
Stamp LocalNow() noexcept {
    const long long now = static_cast<long long>(std::time(nullptr)) + utc_offset;
    const long long days = (now >= 0 ? now : now - 86399) / 86400;
    const auto rest = static_cast<unsigned>(now - days * 86400);
    Stamp stamp{};
    CivilFromDays(days, stamp.year, stamp.month, stamp.day);
    stamp.hour = rest / 3600;
    stamp.minute = rest / 60 % 60;
    stamp.second = rest % 60;
    return stamp;
}

// Memory that may not be readable is copied by the kernel, which answers a bad address with an
// error where the handler itself would fault: a write into a pipe, read back. Never more than a
// page at once, so the pipe always has room.
bool CopyWith(const int (&fds)[2], const void* address, void* out, std::size_t bytes) noexcept {
    if (fds[1] < 0 || bytes == 0 || bytes > 4096) return false;
    const ssize_t written = write(fds[1], address, bytes);
    std::size_t got = 0;
    char* target = static_cast<char*>(out);
    while (written == static_cast<ssize_t>(bytes) && got < bytes) {
        const ssize_t count = read(fds[0], target + got, bytes - got);
        if (count > 0) got += static_cast<std::size_t>(count);
        else if (count == 0 || errno != EINTR) break;
    }
    if (got == bytes) return true;
    // Whatever a failed write left in the pipe goes (the read end does not block).
    char rest[256];
    while (read(fds[0], rest, sizeof(rest)) > 0) {}
    return false;
}
bool Copy(const void* address, void* out, std::size_t bytes) noexcept {
    return CopyWith(probe_pipe, address, out, bytes);
}

// Whether the eight bytes that end at a code address end with a call instruction: then the
// address is where that call returns to.
bool AfterCall(const unsigned char* bytes) noexcept {
    if (bytes[3] == 0xE8) return true;  // call rel32
    for (const int length : {2, 3, 4, 6, 7}) {
        const unsigned char* at = bytes + 8 - length;  // a call through a register or memory: FF /2
        if (at[0] == 0xFF && (at[1] & 0x38) == 0x10) return true;
        if (length >= 3 && (at[0] & 0xF0) == 0x40 && at[1] == 0xFF && (at[2] & 0x38) == 0x10) return true;
    }
    return false;
}

// The app's code addresses on the stack, most recent first: the build has no frame pointers, so
// these are the calls that led to the fault, and now and then one left by an earlier call. The
// console does not let its code be read, so there every value that points into the code is
// listed with a "?", and tools/symbolize-crash.py drops those that do not follow a call.
template <std::size_t N>
void PutCallsWith(Text<N>& out, const int (&fds)[2], std::uint64_t (&words)[512], std::uint64_t stack,
                  unsigned most) noexcept {
    constexpr std::size_t kWords = 16 * 1024;  // 128 KiB of stack
    unsigned found = 0;
    std::uint64_t at = stack & ~std::uint64_t{7};
    for (std::size_t seen = 0; seen < kWords && found < most;) {
        // Up to the end of the page: a read is whole or not at all.
        const std::size_t bytes = std::min<std::size_t>(4096 - (at & 4095), sizeof(words));
        if (!CopyWith(fds, reinterpret_cast<const void*>(at), words, bytes)) break;
        for (std::size_t i = 0; i < bytes / 8 && found < most; ++i) {
            const std::uint64_t value = words[i];
            if (!InCode(value) || value < CodeStart() + 8) continue;
            unsigned char before[8];
            const bool checked = CopyWith(fds, reinterpret_cast<const void*>(value - 8), before, sizeof(before));
            if (checked && !AfterCall(before)) continue;
            out.Put("  ");
            out.Address(value);
            out.Put(checked ? "\n" : " ?\n");
            ++found;
        }
        at += bytes;
        seen += bytes / 8;
    }
    if (found == 0) out.Put("  none found\n");
}
void PutCalls(std::uint64_t stack) noexcept {
    static std::uint64_t words[512];
    PutCallsWith(report, probe_pipe, words, stack, 64);
}

Registers FromContext(void* context) noexcept {
    Registers r{};
    if (!context) return r;
#ifdef __linux__
    const auto& g = static_cast<ucontext_t*>(context)->uc_mcontext.gregs;
    const auto reg = [&g](int index) { return static_cast<std::uint64_t>(g[index]); };
    r = {reg(REG_RIP), reg(REG_RSP), reg(REG_RBP), reg(REG_RAX), reg(REG_RBX), reg(REG_RCX),    reg(REG_RDX),
         reg(REG_RSI), reg(REG_RDI), reg(REG_R8),  reg(REG_R9),  reg(REG_R10), reg(REG_R11),    reg(REG_R12),
         reg(REG_R13), reg(REG_R14), reg(REG_R15), reg(REG_EFL), reg(REG_TRAPNO), reg(REG_ERR), true};
#else
    // The console's context: FreeBSD's machine context after 64 bytes (src/fastmem.h has the two
    // words the JIT's handler uses; rip at 0xe0 and rsp at 0xf8 were qualified on hardware).
    const auto* m = static_cast<const std::uint64_t*>(context) + 8;
    r = {m[20], m[23], m[9],  m[7],  m[8],  m[4],  m[3],  m[2],                 m[1],  m[5], m[6],
         m[10], m[11], m[12], m[13], m[14], m[15], m[22], m[16] & 0xffffffffu, m[19], true};
#endif
    return r;
}

const char* ThreadName(std::uintptr_t thread) noexcept {
    for (const NamedThread& entry : named_threads)
        if (entry.thread.load(std::memory_order_acquire) == thread) return entry.name;
    return "not named";
}

void PutRegister(const char* before, const char* name, std::uint64_t value) noexcept {
    report.Put(before);
    report.Put(name);
    report.Put(' ');
    report.Hex(value, 16);
}

// The report, complete in its buffer.
void Compose(const Kind* kind, int code, std::uint64_t address, const Registers& r, const char* reason,
             const Stamp& stamp, bool restart) noexcept {
    auto& t = report;
    t.Clear();
    t.Put("EglantinePS5 crash report\nversion: ");
    t.Put(version);
    t.Put(" (code size 0x");
    t.Hex(CodeEnd() - CodeStart());
    t.Put(")\ntime: ");
    t.Dec(static_cast<std::uint64_t>(stamp.year), 4);
    t.Put('-');
    t.Dec(stamp.month, 2);
    t.Put('-');
    t.Dec(stamp.day, 2);
    t.Put(' ');
    t.Dec(stamp.hour, 2);
    t.Put(':');
    t.Dec(stamp.minute, 2);
    t.Put(':');
    t.Dec(stamp.second, 2);
    t.Put(", ");
    t.Dec(static_cast<std::uint64_t>(std::time(nullptr) - started_at));
    t.Put(" s after the app started\nwhat: ");
    if (kind) {
        t.Put(kind->name);
        t.Put(" (");
        t.Put(kind->meaning);
        t.Put(')');
        if (kind->fault) {
            t.Put(", code ");
            t.Dec(static_cast<std::uint64_t>(code));
            t.Put(", address 0x");
            t.Hex(address, 16);
        }
    } else {
        t.Put("the app stopped itself");
    }
    if (reason && *reason) {
        t.Put("\nreason: ");
        t.Put(reason);
    }
    t.Put("\nwhere: ");
    t.Address(r.rip);
    if (!InCode(r.rip)) t.Put(" (outside the app's code: a system library or generated code)");
    const std::uintptr_t self = reporter.load(std::memory_order_relaxed);
    t.Put("\nthread: ");
    t.Put(ThreadName(self));
    t.Put(" (0x");
    t.Hex(self);
    t.Put(")\nrunning: ");
    t.Put(sessions[session_now.load(std::memory_order_acquire) & 1]);
    t.Put('\n');
#ifdef PS5_NATIVE
    {
        std::int64_t start = 0;
        std::size_t largest = 0;
        const std::int64_t total = sceKernelGetDirectMemorySize();
        const bool known = total > 0 && sceKernelAvailableDirectMemorySize(0, total, 0x4000, &start, &largest) == 0;
        unsigned blocks = 0;
        const std::size_t large = eden_heap_large_held(&blocks);
        t.Put("memory: largest free block ");
        if (known) t.Dec(largest >> 20);
        else t.Put("unknown");
        t.Put(" MiB of ");
        t.Dec(static_cast<std::uint64_t>(total > 0 ? total : 0) >> 20);
        t.Put(" MiB, heap ");
        t.Dec(eden_heap_committed() >> 20);
        t.Put(" MiB and ");
        t.Dec(large >> 20);
        t.Put(" MiB in ");
        t.Dec(blocks);
        t.Put(" large blocks\n");
    }
#endif
    t.Put("registers:");
    PutRegister("\n  ", "rip", r.rip);
    PutRegister("  ", "rsp", r.rsp);
    if (r.whole) {
        PutRegister("  ", "rbp", r.rbp);
        PutRegister("\n  ", "rax", r.rax);
        PutRegister("  ", "rbx", r.rbx);
        PutRegister("  ", "rcx", r.rcx);
        PutRegister("\n  ", "rdx", r.rdx);
        PutRegister("  ", "rsi", r.rsi);
        PutRegister("  ", "rdi", r.rdi);
        PutRegister("\n  ", "r8 ", r.r8);
        PutRegister("  ", "r9 ", r.r9);
        PutRegister("  ", "r10", r.r10);
        PutRegister("\n  ", "r11", r.r11);
        PutRegister("  ", "r12", r.r12);
        PutRegister("  ", "r13", r.r13);
        PutRegister("\n  ", "r14", r.r14);
        PutRegister("  ", "r15", r.r15);
        PutRegister("  ", "flags", r.rflags);
        t.Put("\n  trap ");
        t.Dec(r.trap);
        t.Put("  error 0x");
        t.Hex(r.error);
    }
    t.Put("\ncode: 0x");
    t.Hex(CodeStart(), 16);
    t.Put(" to 0x");
    t.Hex(CodeEnd(), 16);
    t.Put(" (eboot+offset is an address in the build's llvm-pie.elf)\n");
    t.Put("calls found on the stack, most recent first (\"?\": not checked against a call instruction):\n");
    PutCalls(r.rsp);
    t.Put(restart ? "then: the app starts again\n" : "then: the app closes (it had just started again after a crash)\n");
}

bool WriteFile(const char* path, const char* data, std::size_t size) noexcept {
    const int file = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0666);
    if (file < 0) return false;
    std::size_t written = 0;
    while (written < size) {
        const ssize_t count = write(file, data + written, size - written);
        if (count > 0) written += static_cast<std::size_t>(count);
        else if (count == 0 || errno != EINTR) break;
    }
    return close(file) == 0 && written == size;
}

// From here the signal takes its usual course: the system ends the app as it always did.
void GiveUp() noexcept {
    state.store(kGivingUp, std::memory_order_release);
    struct sigaction standard {};
    standard.sa_handler = SIG_DFL;
    sigemptyset(&standard.sa_mask);
    for (const Kind& kind : kKinds) sigaction(kind.signal, &standard, nullptr);
}

// One thread gets to write the report. Another thread that crashes meanwhile waits for the
// restart; a second fault on the reporting thread (the handler itself failed) gives up.
bool Enter() noexcept {
    const auto self = (std::uintptr_t)pthread_self();
    int expected = kIdle;
    if (state.compare_exchange_strong(expected, kReporting, std::memory_order_acq_rel)) {
        reporter.store(self, std::memory_order_release);
        return true;
    }
    if (expected == kGivingUp || reporter.load(std::memory_order_acquire) == self) {
        GiveUp();
        return false;
    }
    for (;;) Sleep(100000);
}

// Writes the report and its note, then waits for the app to start again (or close).
void WriteReport(const Kind* kind, int code, std::uint64_t address, const Registers& registers,
                 const char* reason) noexcept {
    // Right after a restart, with no game started since: the crash comes with starting the app,
    // and starting it again would only repeat it.
    const bool restart = !(restarted_process && !game_started.load(std::memory_order_relaxed) &&
                           std::time(nullptr) - started_at < 60);
    leave.store(!restart, std::memory_order_release);
    const Stamp stamp = LocalNow();
    Compose(kind, code, address, registers, reason, stamp, restart);

    report_name.Clear();
    report_name.Put("crash-");
    report_name.Dec(static_cast<std::uint64_t>(stamp.year), 4);
    report_name.Dec(stamp.month, 2);
    report_name.Dec(stamp.day, 2);
    report_name.Put('-');
    report_name.Dec(stamp.hour, 2);
    report_name.Dec(stamp.minute, 2);
    report_name.Dec(stamp.second, 2);
    report_name.Put(".txt");
    report_path.Clear();
    report_path.Put(logs);
    report_path.Put('/');
    report_path.Put(report_name.data);
    const bool saved = WriteFile(report_path.data, report.data, report.size);
    // The note the next start reads: the report's name, and whether the app started itself again.
    note.Clear();
    note.Put(report_name.data);
    note.Put(restart ? " 1\n" : " 0\n");
    note_path.Clear();
    note_path.Put(logs);
    note_path.Put("/crash-note.txt");
    if (saved) WriteFile(note_path.data, note.data, note.size);

    summary.Clear();
    summary.Put("EDEN_CRASH what=");
    summary.Put(kind ? kind->name : "stopped");
    summary.Put(" where=");
    summary.Address(registers.rip);
    summary.Put(" thread=");
    summary.Put(ThreadName(reporter.load(std::memory_order_relaxed)));
    summary.Put(saved ? " report=" : " report_not_written=");
    summary.Put(report_path.data);
    summary.Put(restart ? " restart=1\n" : " restart=0\n");
    Note(summary.data);

    reported.store(true, std::memory_order_release);
    // The restart ends this process. Six seconds without it: the system's own handling takes over.
    for (int i = 0; i < 60 && !helper_failed.load(std::memory_order_acquire); ++i) Sleep(100000);
    Note("EDEN_CRASH the app did not start again; the system handles the crash\n");
}

void Handle(int signal, siginfo_t* info, void* context) {
    if (!Enter()) return;
    const Kind* kind = &kKinds[0];
    for (const Kind& candidate : kKinds)
        if (candidate.signal == signal) kind = &candidate;
    WriteReport(kind, info ? info->si_code : 0, info ? reinterpret_cast<std::uintptr_t>(info->si_addr) : 0,
                FromContext(context), nullptr);
    // Returning repeats the fault with the standard action in place.
    GiveUp();
}

// The helper threads exist from startup on: the heap or a lock may be unusable when they are
// needed. They look at a flag four times a second, which nothing can keep the handler from
// setting (a pipe's first write can fail on the console when memory is short).
void WaitForReport() noexcept {
    while (!reported.load(std::memory_order_acquire)) Sleep(250000);
}

// Starts the app again (or closes it), calling only the system.
void Restarter() {
    WaitForReport();
    Sleep(300000);  // what was printed reaches the log files (log_flusher.h)
    if (!leave.load(std::memory_order_acquire)) eden_restart_app();
    eden_exit_app();
    helper_failed.store(true, std::memory_order_release);
}

// The buffered log stream: flushing it can wait forever on a lock the crashed thread holds,
// which is why the thread above does not do it.
void Flusher() {
    WaitForReport();
    std::fflush(stderr);
    std::fflush(stdout);
}

void CopyText(char* target, std::size_t size, const char* text) noexcept {
    std::size_t length = 0;
    for (; text && text[length] && length + 1 < size; ++length) target[length] = text[length];
    target[length] = '\0';
}

// ---- The hang watch: a game that shows no frame for kHangSeconds gets a report of where every
// named thread is. Each one is asked with SIGUSR1; its handler only stores its instruction and
// stack pointers, and the watch reads the stacks itself (CopyWith, through its own pipe). It
// writes with system calls only: a stopped thread may hold the C library's stream locks. A thread
// that does not answer (signals blocked, or gone) is listed as such. ----
constexpr unsigned kHangSeconds = 30;
// Before a game's first frame (loading: Mario Kart World takes ~60 s) the watch waits longer.
constexpr unsigned kFirstFrameSeconds = 120;
constexpr unsigned kHangReports = 3;  // per session
struct Sample {
    std::atomic<std::uint64_t> rip{0}, rsp{0};
    std::atomic<bool> answered{false};
};
Sample samples[kNamedThreads];
Text<160 * 1024> hang_report;
Text<64> hang_name;
Text<300> hang_path;
Text<400> hang_note;

void Sampled(int, siginfo_t*, void* context) {
    const auto self = (std::uintptr_t)pthread_self();
    for (unsigned i = 0; i < kNamedThreads; ++i) {
        if (named_threads[i].thread.load(std::memory_order_acquire) != self) continue;
        const Registers r = FromContext(context);
        samples[i].rip.store(r.rip, std::memory_order_relaxed);
        samples[i].rsp.store(r.rsp, std::memory_order_relaxed);
        samples[i].answered.store(true, std::memory_order_release);
        return;
    }
}

void PutStamp(Text<160 * 1024>& t, const Stamp& stamp) noexcept {
    t.Dec(static_cast<std::uint64_t>(stamp.year), 4);
    t.Put('-');
    t.Dec(stamp.month, 2);
    t.Put('-');
    t.Dec(stamp.day, 2);
    t.Put(' ');
    t.Dec(stamp.hour, 2);
    t.Put(':');
    t.Dec(stamp.minute, 2);
    t.Put(':');
    t.Dec(stamp.second, 2);
}

void WriteHangReport(unsigned seconds, unsigned long long frames) noexcept {
    const auto self = (std::uintptr_t)pthread_self();
    unsigned entries = 0, asked = 0, answered = 0;
    for (unsigned i = 0; i < kNamedThreads; ++i) {
        samples[i].answered.store(false, std::memory_order_relaxed);
        const std::uintptr_t thread = named_threads[i].thread.load(std::memory_order_acquire);
        if (thread == 0 || thread == self) continue;
        ++entries;
        if (pthread_kill((pthread_t)thread, SIGUSR1) == 0) ++asked;
    }
    for (int wait = 0; wait < 20; ++wait) Sleep(10000);  // 200 ms to answer
    const Stamp stamp = LocalNow();
    auto& t = hang_report;
    t.Clear();
    t.Put("EglantinePS5 hang report\nversion: ");
    t.Put(version);
    t.Put(" (code size 0x");
    t.Hex(CodeEnd() - CodeStart());
    t.Put(")\ntime: ");
    PutStamp(t, stamp);
    t.Put(", ");
    t.Dec(static_cast<std::uint64_t>(std::time(nullptr) - started_at));
    t.Put(" s after the app started\nwhat: no game frame shown for ");
    t.Dec(seconds);
    t.Put(" s (");
    t.Dec(frames);
    t.Put(" frames shown before)\nrunning: ");
    t.Put(sessions[session_now.load(std::memory_order_acquire) & 1]);
    t.Put("\ncode: 0x");
    t.Hex(CodeStart(), 16);
    t.Put(" to 0x");
    t.Hex(CodeEnd(), 16);
    t.Put(" (eboot+offset is an address in the build's llvm-pie.elf)\n");
    static std::uint64_t words[512];
    for (unsigned i = 0; i < kNamedThreads; ++i) {
        const std::uintptr_t thread = named_threads[i].thread.load(std::memory_order_acquire);
        if (thread == 0 || thread == self) continue;
        t.Put("\nthread: ");
        t.Put(named_threads[i].name);
        t.Put(" (0x");
        t.Hex(thread);
        t.Put(")\n");
        if (!samples[i].answered.load(std::memory_order_acquire)) {
            t.Put("  did not answer (signals blocked, or the thread is gone)\n");
            continue;
        }
        ++answered;
        const std::uint64_t rip = samples[i].rip.load(std::memory_order_relaxed);
        t.Put("where: ");
        t.Address(rip);
        if (!InCode(rip)) t.Put(" (outside the app's code: a system library, often a wait)");
        t.Put("\ncalls found on the stack, most recent first (\"?\": not checked against a call instruction):\n");
        PutCallsWith(t, hang_pipe, words, samples[i].rsp.load(std::memory_order_relaxed), 40);
    }
    hang_name.Clear();
    hang_name.Put("hang-");
    hang_name.Dec(static_cast<std::uint64_t>(stamp.year), 4);
    hang_name.Dec(stamp.month, 2);
    hang_name.Dec(stamp.day, 2);
    hang_name.Put('-');
    hang_name.Dec(stamp.hour, 2);
    hang_name.Dec(stamp.minute, 2);
    hang_name.Dec(stamp.second, 2);
    hang_name.Put(".txt");
    hang_path.Clear();
    hang_path.Put(logs);
    hang_path.Put('/');
    hang_path.Put(hang_name.data);
    const bool saved = WriteFile(hang_path.data, t.data, t.size);
    hang_note.Clear();
    hang_note.Put("EDEN_HANG seconds=");
    hang_note.Dec(seconds);
    hang_note.Put(" threads=");
    hang_note.Dec(entries);
    hang_note.Put(" asked=");
    hang_note.Dec(asked);
    hang_note.Put(" answered=");
    hang_note.Dec(answered);
    hang_note.Put(saved ? " report=" : " report_not_written=");
    hang_note.Put(hang_path.data);
    hang_note.Put('\n');
    Note(hang_note.data);
    (void)!write(2, hang_note.data, hang_note.size);  // stderr.log, past any stream lock
}

void HangWatch() {
    unsigned long long last = frames_displayed.load(std::memory_order_relaxed);
    unsigned still = 0, reports = 0;
    bool reported_this_stop = false, was_running = false;
    for (;;) {
        Sleep(1000000);
        const bool running = game_running.load(std::memory_order_acquire);
        if (running && !was_running) {
            reports = 0;  // a new game session
            still = 0;
        }
        was_running = running;
        const unsigned long long frames = frames_displayed.load(std::memory_order_relaxed);
        if (!running || frames != last) {
            last = frames;
            still = 0;
            reported_this_stop = false;
            continue;
        }
        if (++still < (frames == 0 ? kFirstFrameSeconds : kHangSeconds) || reported_this_stop ||
            reports >= kHangReports ||
            state.load(std::memory_order_acquire) != kIdle)
            continue;
        WriteHangReport(still, frames);
        reported_this_stop = true;
        ++reports;
    }
}

} // namespace

bool Readable(const void* address, std::size_t bytes) noexcept {
    char buffer[4096];
    return Copy(address, buffer, bytes);
}

void Install(const std::string& logs_folder, const char* app_version, bool restarted) {
    if (installed) return;
    CopyText(logs, sizeof(logs), logs_folder.c_str());
    CopyText(version, sizeof(version), app_version);
    CopyText(sessions[0], sizeof(sessions[0]), "starting");
    restarted_process = restarted;
    started_at = std::time(nullptr);
    if (const std::tm* local = std::localtime(&started_at)) {
        const long long seconds = DaysFromCivil(local->tm_year + 1900, static_cast<unsigned>(local->tm_mon + 1),
                                                static_cast<unsigned>(local->tm_mday)) * 86400 +
                                  local->tm_hour * 3600 + local->tm_min * 60 + local->tm_sec;
        utc_offset = static_cast<long>(seconds - static_cast<long long>(started_at));
    }
    NameThread("main");
    // The pipe memory is read through, used once now: its buffer exists before it is needed.
    // Without it the report has no calls list.
    bool probe = pipe(probe_pipe) == 0;
    if (probe) {
        fcntl(probe_pipe[0], F_SETFL, O_NONBLOCK);
        fcntl(probe_pipe[1], F_SETFL, O_NONBLOCK);
        probe = Readable(&started_at, sizeof(started_at));
    }
    if (!probe) probe_pipe[0] = probe_pipe[1] = -1;
    if (pipe(hang_pipe) == 0) {
        fcntl(hang_pipe[0], F_SETFL, O_NONBLOCK);
        fcntl(hang_pipe[1], F_SETFL, O_NONBLOCK);
    } else {
        hang_pipe[0] = hang_pipe[1] = -1;
    }
    std::thread(Restarter).detach();
    std::thread(Flusher).detach();
    {
        struct sigaction sample {};
        sample.sa_sigaction = Sampled;
        sample.sa_flags = SA_SIGINFO | SA_RESTART;
        sigemptyset(&sample.sa_mask);
        if (sigaction(SIGUSR1, &sample, nullptr) == 0) std::thread(HangWatch).detach();
    }
    struct sigaction action {};
    action.sa_sigaction = Handle;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    unsigned handled = 0;
    for (const Kind& kind : kKinds) handled += sigaction(kind.signal, &action, nullptr) == 0;
    installed = true;
    std::fprintf(stderr, "EDEN_CRASH_REPORT installed=1 signals=%u stack_reads=%d folder=%s after_crash=%d\n", handled,
                 probe ? 1 : 0, logs, restarted ? 1 : 0);
}

void SetSession(const std::string& text, bool game) noexcept {
    const unsigned next = (session_now.load(std::memory_order_relaxed) + 1) & 1;
    CopyText(sessions[next], sizeof(sessions[next]), text.c_str());
    session_now.store(next, std::memory_order_release);
    if (game) game_started.store(true, std::memory_order_relaxed);
    game_running.store(game, std::memory_order_release);
}

void Fail(const char* reason) noexcept {
    if (installed && Enter()) {
        Registers registers{};
        registers.rip = reinterpret_cast<std::uintptr_t>(__builtin_return_address(0));
        registers.rsp = reinterpret_cast<std::uintptr_t>(__builtin_frame_address(0));
        WriteReport(nullptr, 0, 0, registers, reason);
        GiveUp();
    }
    std::abort();
}

Last TakeLast(const std::string& logs_folder, const std::string& eden_log) {
    Last last;
    const std::string note_file = logs_folder + "/crash-note.txt";
    std::string written;
    int restart = 0;
    {
        std::ifstream file(note_file);
        if (!file.good()) return last;
        file >> written >> restart;
    }
    std::remove(note_file.c_str());  // announced once
    // crash-YYYYMMDD-HHMMSS.txt
    if (written.size() != 25 || !written.starts_with("crash-") || !written.ends_with(".txt") ||
        written.find_first_not_of("crash-0123456789.tx") != std::string::npos)
        return last;
    const std::string stem = written.substr(0, written.size() - 4);
    const std::string report_file = logs_folder + "/" + stem + ".txt";
    if (!std::ifstream(report_file).good()) return last;
    // That run's logs, beside its report.
    static constexpr const char* kParts[] = {".txt", "-stderr.log", "-heap.log", "-eden_log.txt"};
    (void)std::rename((logs_folder + "/stderr.prev.log").c_str(), (logs_folder + "/" + stem + kParts[1]).c_str());
    (void)std::rename((logs_folder + "/heap.prev.log").c_str(), (logs_folder + "/" + stem + kParts[2]).c_str());
    if (!eden_log.empty()) (void)std::rename(eden_log.c_str(), (logs_folder + "/" + stem + kParts[3]).c_str());
    // The five newest reports stay (crash-index.txt lists them, oldest first).
    const std::string index_file = logs_folder + "/crash-index.txt";
    std::vector<std::string> kept;
    {
        std::ifstream index(index_file);
        for (std::string entry; index >> entry;)
            if (entry != stem && entry.size() == stem.size() && entry.starts_with("crash-")) kept.push_back(entry);
    }
    kept.push_back(stem);
    while (kept.size() > 5) {
        for (const char* part : kParts) (void)std::remove((logs_folder + "/" + kept.front() + part).c_str());
        kept.erase(kept.begin());
    }
    {
        std::ofstream index(index_file, std::ios::trunc);
        for (const std::string& entry : kept) index << entry << '\n';
    }
    last.report = report_file;
    last.restarted = restart == 1;
    return last;
}

} // namespace Eden::Crash
