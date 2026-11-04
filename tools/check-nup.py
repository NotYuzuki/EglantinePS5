from pathlib import Path
import subprocess, tempfile, shutil, os
os.environ["UBSAN_OPTIONS"] = "halt_on_error=1"
from concurrent.futures import ThreadPoolExecutor
root=Path(__file__).resolve().parents[1]; src=root/'Eglantine/src'
work=root/'build/diagnostics/nup-host'; work.mkdir(parents=True,exist_ok=True)
# Metadata probing is compiled in the native build; this small host executable
# isolates the real packer/container/VFS and filesystem implementation from the emulator runtime.
shadow=work/'include/core/loader'; shadow.mkdir(parents=True,exist_ok=True)
(shadow/'deconstructed_rom_directory.h').write_text('''#pragma once
#include "core/loader/loader.h"
#include "core/file_sys/control_metadata.h"
namespace Loader { class AppLoader_DeconstructedRomDirectory { public:
explicit AppLoader_DeconstructedRomDirectory(FileSys::VirtualFile) {}
ResultStatus ReadControlData(FileSys::NACP&) { return ResultStatus::ErrorNoControl; }
ResultStatus ReadIcon(std::vector<u8>&) { return ResultStatus::ErrorNoControl; }
ResultStatus ReadDisplayVersion(std::string&) { return ResultStatus::ErrorNoControl; }
}; }
''')
shadow=work/'include/core/file_sys'; shadow.mkdir(parents=True,exist_ok=True)
(shadow/'control_metadata.h').write_text('''#pragma once
#include <string>
#include <vector>
#include "common/common_types.h"
namespace FileSys { class NACP { public:
std::vector<u8> GetRawBytes() const { return {}; }
std::string GetApplicationName() const { return {}; }
std::string GetDeveloperName() const { return {}; }
}; }
''')
fixture=(src/'tests/core/nup_writer.cpp').read_text().split('TEST_CASE(')[0].replace('#include <catch2/catch_test_macros.hpp>','').replace('fs::relative(entry.path(), dir)', 'entry.path().lexically_relative(dir)')
main=r'''
#include <iostream>
#include <sys/resource.h>
#include <unistd.h>
#include <csignal>
#include "common/logging.h"
namespace Common::Log { void FmtLogMessageImpl(Class, Level, const char*, unsigned int, const char*, fmt::string_view, const fmt::format_args&) {} }
[[noreturn]] void AssertFatalImpl() { std::abort(); }
void AssertFailSoftImpl() { std::abort(); }
static std::string observed_root;
static std::uint64_t source_reads=0, source_bytes=0;
extern "C" size_t __real_fread(void*,size_t,size_t,FILE*);
extern "C" size_t __wrap_fread(void* data,size_t size,size_t count,FILE* file) {
 const auto result=__real_fread(data,size,count,file);
 if (!observed_root.empty()) {
  char descriptor[64], path[4096];
  std::snprintf(descriptor,sizeof(descriptor),"/proc/self/fd/%d",fileno(file));
  const auto length=readlink(descriptor,path,sizeof(path)-1);
  if (length>0) { path[length]=0; if(std::string_view(path).starts_with(observed_root)) { ++source_reads; source_bytes+=result*size; } }
 }
 return result;
}
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while(0)
int main() {
 FileSys::VectorVfsFile empty(std::vector<u8>{});
 CHECK(empty.Read(nullptr,0,0)==0); u8 byte=0; CHECK(empty.Read(&byte,1,10)==0);
 TempDir temp; auto source=temp.path/"DK Bonanza"; MakeDump(source);
 std::string large(12*1024*1024, 'q'); for(size_t i=0;i<large.size();++i) large[i]='a'+i%23;
 Put(source/"Base/RomFS/large.bin", large);
 for (int i=0;i<200;++i) { std::string bytes(4096,'a'); bytes[0]=char(i); bytes[1]=char(i>>8); Put(source/"Base/RomFS/small"/std::to_string(i),bytes); }
 std::string incompressible(384*1024, '\0');
 std::uint32_t random_state=0x62c0ffee;
 for (auto& byte : incompressible) { random_state=random_state*1664525U+1013904223U; byte=char(random_state>>24); }
 Put(source/"Base/RomFS/incompressible.bin",incompressible);
 observed_root=source.string()+"/";
 for(int level: {0,1,3}) {
  const bool compressed=level!=0;
  auto output=temp.path/(level==0?"raw.nup":level==1?"fast.nup":"compressed.nup");
  FileSys::Nup::PackOptions options; options.zstd_level=level; options.measure_io=true; options.descriptor_output=true;
  FileSys::Nup::PackProgress pack_progress;
  // Exercise external archives whose compressed member exceeds the PS5 cache-entry limit.
  options.raw_threshold=32ULL<<20;
  options.metadata.icon={1,2,3,4,5,6};
  std::string error;
  CHECK(FileSys::Nup::PackDump(source,output,options,[&](const auto& p){ pack_progress=p; },nullptr,&error)==FileSys::Nup::PackResult::Success);
  CHECK(pack_progress.io.open_calls==pack_progress.files_total);
  CHECK(pack_progress.io.read_calls>0 && pack_progress.io.write_calls>0);
  std::cout<<"PACK_IO "<<level<<" "<<pack_progress.io.seek_calls<<" "<<pack_progress.io.skipped_seeks<<"\n";
  FileSys::RealVfsFilesystem fs;
  NupContainer nup(fs.OpenFile(output.string(),FileSys::OpenMode::Read));
  CHECK(nup.GetStatus()==Loader::ResultStatus::Success);
  CHECK(nup.GetPackages().size()==4);
  CHECK(nup.GetPackages().front().icon==options.metadata.icon);
  std::string archive_hash; for(auto b: Sha256(ReadAll(output))) archive_hash+=fmt::format("{:02x}",b);
  std::cout<<"ARCHIVE_HASH "<<(level==0?"raw":level==1?"fast":"compressed")<<" "<<archive_hash<<"\n";
  auto dir=nup.GetRomFSDir(*nup.FindBasePackage());
  CHECK(PackedTree(dir)==SourceTree(source/"Base/RomFS"));
  CHECK(Read(dir->GetFile("incompressible.bin"))==incompressible);
  const auto output_records=RawFiles(ReadAll(output));
  const auto fallback=std::find_if(output_records.begin(),output_records.end(),[](const auto& f){ return f.name=="incompressible.bin"; });
  CHECK(fallback!=output_records.end() && fallback->codec==0);
  if (compressed) CHECK(pack_progress.io.seek_calls>=2);
  auto member=dir->GetFile("large.bin"); CHECK(member);
  for(size_t offset: {0UL,1UL,65530UL,large.size()-1000}) {
   std::array<u8,999> bytes{}; CHECK(member->Read(bytes.data(),bytes.size(),offset)==bytes.size());
   CHECK(std::string(reinterpret_cast<char*>(bytes.data()),bytes.size())==large.substr(offset,bytes.size()));
  }
  std::uint64_t previous_bytes=0, all_bytes=0;
  CHECK(nup.VerifyIntegrity({},[&](auto done,auto total){ CHECK(done>=previous_bytes); CHECK(done<=total); previous_bytes=done; all_bytes=total; })==Loader::ResultStatus::Success);
  CHECK(previous_bytes==all_bytes && all_bytes>large.size());
  CHECK(nup.VerifyIntegrity([](size_t,size_t){return false;})==Loader::ResultStatus::ErrorIntegrityVerificationFailed);
  auto bad=temp.path/("bad-"+output.filename().string());
  fs::copy_file(output,bad);
  const auto records=RawFiles(ReadAll(output));
  const auto damaged=std::find_if(records.begin(),records.end(),[](const auto& r){ return r.name=="large.bin"; });
  CHECK(damaged!=records.end());
  { std::fstream f(bad,std::ios::binary|std::ios::in|std::ios::out);
    f.seekg(damaged->extent_offset+damaged->extent_size/2); char b=0; f.get(b);
    f.seekp(damaged->extent_offset+damaged->extent_size/2); f.put(b^0x5a); }
  NupContainer corrupt(fs.OpenFile(bad.string(),FileSys::OpenMode::Read));
  CHECK(corrupt.GetStatus()==Loader::ResultStatus::Success);
  CHECK(corrupt.VerifyIntegrity({})==Loader::ResultStatus::ErrorIntegrityVerificationFailed);
  CHECK(FileSys::Nup::PackDump(source,output,options)==FileSys::Nup::PackResult::IoError);
  CHECK(fs::exists(source/"Base/ExeFS/main"));
 }
 CHECK(RejectsMalformedLayouts(ReadAll(temp.path/"raw.nup")));
 std::atomic_bool stop{true};
 CHECK(FileSys::Nup::PackDump(source,temp.path/"cancel.nup",{}, {}, &stop)==FileSys::Nup::PackResult::Cancelled);
 CHECK(fs::exists(source/"Base/ExeFS/main"));
 stop=false;
 CHECK(FileSys::Nup::PackDump(source,temp.path/"mid-cancel.nup",{},
    [&](const auto& p){ if(p.files_done>0) stop=true; },&stop)==FileSys::Nup::PackResult::Cancelled);
 CHECK(!fs::exists(temp.path/"mid-cancel.nup"));
 CHECK(!fs::exists(temp.path/"mid-cancel.nup.part"));
 CHECK(fs::exists(source/"Base/ExeFS/main"));
 // Force an actual short filesystem write without filling the test machine's disk.
 struct rlimit previous{}; CHECK(getrlimit(RLIMIT_FSIZE,&previous)==0);
 struct rlimit limited{65536,previous.rlim_max};
 const auto handler=std::signal(SIGXFSZ,SIG_IGN);
 CHECK(setrlimit(RLIMIT_FSIZE,&limited)==0);
 FileSys::Nup::PackOptions raw; raw.zstd_level=0; raw.descriptor_output=true;
 const auto failed=FileSys::Nup::PackDump(source,temp.path/"write-failure.nup",raw);
 CHECK(setrlimit(RLIMIT_FSIZE,&previous)==0);
 std::signal(SIGXFSZ,handler);
 CHECK(failed==FileSys::Nup::PackResult::IoError);
 CHECK(!fs::exists(temp.path/"write-failure.nup"));
 CHECK(!fs::exists(temp.path/"write-failure.nup.part"));
 CHECK(fs::exists(source/"Base/ExeFS/main"));
 FileSys::NupDecompressCache cache;
 for(size_t i=0;i<8;++i) { std::vector<u8> bytes(8<<20); cache.Insert(i,bytes); CHECK(bytes.empty()); }
 CHECK(cache.bytes==(32<<20)); CHECK(cache.lru.size()==4);
 std::vector<u8> duplicate(1024); cache.Insert(7,duplicate);
 CHECK(cache.bytes==(32<<20)); CHECK(cache.lru.size()==4); CHECK(duplicate.size()==1024);
 std::cout<<"SOURCE_IO "<<source_reads<<" "<<source_bytes<<"\n";
 std::cout<<"PASS: raw/compressed conversion, 4 package trees, >8 MiB compressed random reads, SHA256 verification, overwrite rejection, cancellation, short-write cleanup, source retention and malformed layout rejection\n";
}
'''
(work/'main.cpp').write_text(fixture+main)
cache=Path((root/'.local/headless-cache').read_text().strip())
json=next((cache/'source/.cache/cpm/nlohmann_json').glob('*/include'))
shutil.copytree(cache/'cdeps/include/openssl',work/'include/openssl',dirs_exist_ok=True)
for name in ('zstd.h','zstd_errors.h'):
 shutil.copyfile(cache/'cdeps/include'/name,work/'include'/name)
boost=['-I'+str(p) for p in (cache/'source/.cache/cpm/boost').glob('*/libs/*/include')]
files=['core/file_sys/nup_writer.cpp','core/file_sys/nup_container.cpp','common/fs/file.cpp','common/fs/fs.cpp','common/fs/fs_util.cpp','common/fs/path_util.cpp','common/string_util.cpp','core/file_sys/vfs/vfs.cpp','core/file_sys/vfs/vfs_real.cpp','core/file_sys/vfs/vfs_vector.cpp','core/file_sys/vfs/vfs_offset.cpp']
cmd=['clang++-18','-std=c++23','-O1','-g','-fsanitize=address,undefined','-DFMT_HEADER_ONLY','-ffunction-sections','-fdata-sections','-I'+str(work/'include'),'-I'+str(src),'-I'+str(root/'.deps/fmt-12.1.0/include'),'-I'+str(json),*boost,str(work/'main.cpp'),*[str(src/f) for f in files],'-Wl,--gc-sections','-Wl,--wrap=fread','/usr/lib/x86_64-linux-gnu/libcrypto.so.3','/usr/lib/x86_64-linux-gnu/libzstd.so.1','-pthread','-o',str(work/'test')]
prefix=cmd[:cmd.index(str(work/'main.cpp'))]
def compile_one(path):
 obj=work/(path.name+'.o')
 result=subprocess.run(['ccache',*prefix,'-c',str(path),'-o',str(obj)],capture_output=True,text=True)
 if result.returncode: raise RuntimeError(result.stderr[-6000:])
 return str(obj)
with ThreadPoolExecutor(max_workers=4) as pool:
 objects=list(pool.map(compile_one,[work/'main.cpp',*[src/f for f in files]]))
subprocess.run(['clang++-18','-fsanitize=address,undefined',*objects,'-Wl,--gc-sections','-Wl,--wrap=fread','/usr/lib/x86_64-linux-gnu/libcrypto.so.3','/usr/lib/x86_64-linux-gnu/libzstd.so.1','-pthread','-o',str(work/'test')],check=True)
optimized=subprocess.check_output([str(work/'test')],text=True)
print(optimized)
# Reproduce the console's unavailable canonical/realpath operation in the host C++ runtime.
shim=work/'deny-realpath.c'
shim.write_text('#include <errno.h>\n#include <stddef.h>\nchar *realpath(const char *p, char *out) { (void)p; (void)out; errno=EPERM; return NULL; }\n')
subprocess.run(['clang-18','-shared','-fPIC',str(shim),'-o',str(work/'deny-realpath.so')],check=True)
import os
env=dict(os.environ,LD_PRELOAD=str(work/'deny-realpath.so'),ASAN_OPTIONS='verify_asan_link_order=0')
subprocess.run([str(work/'test')],check=True,env=env)
print('NUP conversion with realpath denied (EPERM) and a spaced dump name PASS')

# Compare byte-identical archives and observed source reads with the optimization disabled.
import re
baseline=work/'nup_writer_no_cache.cpp'
writer=(src/'core/file_sys/nup_writer.cpp').read_text()
anchor='source_cached = known_hash.has_value() && extent.size <= ReadBufferSize;'
assert writer.count(anchor)==1
baseline.write_text(writer.replace(anchor,'source_cached = false;'))
baseline_object=compile_one(baseline)
baseline_objects=[baseline_object if o==str(work/'nup_writer.cpp.o') else o for o in objects]
subprocess.run(['clang++-18','-fsanitize=address,undefined',*baseline_objects,'-Wl,--gc-sections','-Wl,--wrap=fread','/usr/lib/x86_64-linux-gnu/libcrypto.so.3','/usr/lib/x86_64-linux-gnu/libzstd.so.1','-pthread','-o',str(work/'baseline-test')],check=True)
unoptimized=subprocess.check_output([str(work/'baseline-test')],text=True)
assert re.findall(r'ARCHIVE_HASH.*',optimized)==re.findall(r'ARCHIVE_HASH.*',unoptimized)
fast=list(map(int,re.search(r'SOURCE_IO (\d+) (\d+)',optimized).groups()))
slow=list(map(int,re.search(r'SOURCE_IO (\d+) (\d+)',unoptimized).groups()))
assert fast[0]<slow[0] and fast[1]<slow[1],(fast,slow)
print(f'Small-file reuse: source reads {slow[0]} -> {fast[0]}, source bytes {slow[1]} -> {fast[1]}; raw/compressed archives byte-identical PASS')


# Compare against real redundant seeks; retain actual rollbacks and header backpatches.
seek_baseline=work/'nup_writer_no_seek_cache.cpp'
seek_guard='if (output_position && *output_position == offset) {'
assert writer.count(seek_guard)==1
seek_baseline.write_text(writer.replace(seek_guard,'if (false) {'))
seek_object=compile_one(seek_baseline)
seek_objects=[seek_object if o==str(work/'nup_writer.cpp.o') else o for o in objects]
subprocess.run(['clang++-18','-fsanitize=address,undefined',*seek_objects,'-Wl,--gc-sections',
    '-Wl,--wrap=fread','/usr/lib/x86_64-linux-gnu/libcrypto.so.3','/usr/lib/x86_64-linux-gnu/libzstd.so.1',
    '-pthread','-o',str(work/'seek-baseline-test')],check=True)
unsequential=subprocess.check_output([str(work/'seek-baseline-test')],text=True)
assert re.findall(r'ARCHIVE_HASH.*',optimized)==re.findall(r'ARCHIVE_HASH.*',unsequential)
fast_seeks=[tuple(map(int,x)) for x in re.findall(r'PACK_IO (\d+) (\d+) (\d+)',optimized)]
slow_seeks=[tuple(map(int,x)) for x in re.findall(r'PACK_IO (\d+) (\d+) (\d+)',unsequential)]
assert len(fast_seeks)==len(slow_seeks)==3
for (level,fast_count,skipped),(old_level,slow_count,_) in zip(fast_seeks,slow_seeks):
 assert level==old_level and skipped>0 and fast_count<slow_count
 print(f'Output seeks level {level}: {slow_count} -> {fast_count}; byte-identical archive PASS')

# Compare descriptor output against stdio for the exact same synthetic archives.
stdio_main=work/'stdio-main.cpp'
native_main=(work/'main.cpp').read_text()
assert 'options.descriptor_output=true;' in native_main
stdio_main.write_text(native_main.replace('options.descriptor_output=true;', 'options.descriptor_output=false;'))
stdio_object=compile_one(stdio_main)
stdio_objects=[stdio_object if o==str(work/'main.cpp.o') else o for o in objects]
subprocess.run(['clang++-18','-fsanitize=address,undefined',*stdio_objects,'-Wl,--gc-sections',
    '-Wl,--wrap=fread','/usr/lib/x86_64-linux-gnu/libcrypto.so.3','/usr/lib/x86_64-linux-gnu/libzstd.so.1',
    '-pthread','-o',str(work/'stdio-output-test')],check=True)
stdio_output=subprocess.check_output([str(work/'stdio-output-test')],text=True)
assert re.findall(r'ARCHIVE_HASH.*',optimized)==re.findall(r'ARCHIVE_HASH.*',stdio_output)
print('Descriptor versus stdio output: raw/fast/compressed archives byte-identical PASS')

# Run registered core tests that do not require real control-metadata probing.
# The metadata-dependent cases and golden hash stay with the full core suite: the host
# isolation above deliberately substitutes that loader dependency.
catch=root/'.local/nx2-core-check/_deps'
if (catch/'catch2-build/src/libCatch2Main.a').exists():
 extra=['-I'+str(catch/'catch2-src/src'),'-I'+str(catch/'catch2-build/generated-includes')]
 core_object=work/'registered-tests.o'
 subprocess.run([*prefix,*extra,'-c',str(src/'tests/core/nup_writer.cpp'),'-o',str(core_object)],check=True)
 stubs=work/'stubs.cpp'
 stubs.write_text('''#include "common/logging.h"
#include "core/loader/loader.h"
#include <ostream>
#include <cstdlib>
namespace Loader { std::ostream& operator<<(std::ostream& out, ResultStatus status) { return out << static_cast<unsigned>(status); } }
namespace Common::Log { void FmtLogMessageImpl(Class, Level, const char*, unsigned int, const char*, fmt::string_view, const fmt::format_args&) {} }
[[noreturn]] void AssertFatalImpl() { std::abort(); }
void AssertFailSoftImpl() { std::abort(); }
''')
 stub_object=compile_one(stubs)
 executable=work/'registered-tests'
 subprocess.run(['clang++-18','-fsanitize=address,undefined',str(core_object),stub_object,
     *[o for o in objects if o!=str(work/'main.cpp.o')],
     str(catch/'catch2-build/src/libCatch2Main.a'),str(catch/'catch2-build/src/libCatch2.a'),
     '-Wl,--gc-sections','/usr/lib/x86_64-linux-gnu/libcrypto.so.3',
     '/usr/lib/x86_64-linux-gnu/libzstd.so.1','-pthread','-o',str(executable)],check=True)
 selected=','.join(['NUP marks updates standalone*','NUP packs a bare*','NUP low storage*',
     'NUP rejects overwrite*','NUP rejects overflowing*'])
 subprocess.run([str(executable),selected,'--reporter','compact'],check=True)

 print('Registered host subset excludes real control/version probing and its golden archive hash; those require the full core loader.')
else:
 print('Registered core subset skipped: existing local Catch2 libraries are unavailable.')
