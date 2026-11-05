#!/usr/bin/env python3
"""Exercise actual native topology discovery/pinning with mocked kernel APIs."""
from pathlib import Path
import os,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
s=(root/'headless/performance.cpp').read_text();a=s.index('std::array<int, names.size()> worker_cpus');body=s[a:s.index('\n#endif',a)]
code=r'''
#include <array>
#include <algorithm>
#include <atomic>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#include <cerrno>
#include <stdexcept>
#include <cassert>
#include <cstdint>
using cpuset_t=uint64_t;
#undef CPU_ISSET
#undef CPU_SET
#define CPU_ISSET(c,p) ((*(p)>>(c))&1)
#define CPU_SET(c,p) (*(p)|=uint64_t(1)<<(c))
#define CPU_LEVEL_WHICH 1
#define CPU_LEVEL_CPUSET 2
#define CPU_WHICH_TID 1
#define CPU_WHICH_PID 2
static uint64_t current=0x1fff, process=0x1fff, fail_mask=0;
static unsigned max_leaf=0xb, level_type=1, apics[64];
static bool fail_restore=false, process_fails=false;
static constexpr std::array names{"CPUCore_0","CPUCore_1","CPUCore_2","CPUCore_3","GPU","HostTiming","VSyncThread",
                                  "CPUCore_4","CPUCore_5"};
static unsigned __get_cpuid_max(unsigned,void*) {return max_leaf;}
#define __cpuid_count(l,s,a,b,c,d) do {a=1;b=2;c=level_type<<8;d=apics[__builtin_ctzll(current)];} while(0)
static int cpuset_getaffinity(int level,int,int,size_t n,cpuset_t* out){
 assert(n==8);
 if(level==CPU_LEVEL_CPUSET){if(process_fails){errno=22;return -1;}*out=process;return 0;}
 *out=current;return 0;
}
static int cpuset_setaffinity(int,int,int,size_t n,const cpuset_t* in){
 assert(n==8 && *in && !(*in&~process));
 if(*in==fail_mask || (fail_restore && *in!=(*in&-*in))) {errno=22;return -1;}
 current=*in;return 0;
}
using Cpus=std::array<int,9>;
'''+body+r'''
int main(){
 // FreeBSD-style numbering: SMT siblings adjacent (APIC ID = CPU), 13 CPUs, 7 physical cores.
 for(unsigned i=0;i<64;++i) apics[i]=i;
 CheckWorkerTopology();assert(worker_topology_ready && current==0x1fff);
 assert((worker_cpus==Cpus{0,2,4,6,8,-1,-1,10,12}));
 // Secondary: not cores 0-2 (CPUs 0-5), not guest core 3 or GPU CPUs (6, 8); Switch 2 also not 10, 12.
 assert(secondary_switch1==0x1e80 && secondary_switch2==0x1e80 && secondary_cpus==0x1e80);
 cpuset_t allowed=current;PinWorker(4,allowed);assert(current==256 && allowed==256);
 // The probe's CPU set decides, not a narrower mask the thread inherited.
 current=0x1fff;allowed=3;PinWorker(4,allowed);assert(current==256 && allowed==256);
 current=0x1fff;
 PinWorker(5,allowed);assert(current==0x1fff);
 // Guest cores 4 and 5 stay unpinned for a Switch 1 title, take their CPUs for a Switch 2 title.
 allowed=current;PinWorker(7,allowed);assert(current==0x1fff);
 switch2_cores=true;PinWorker(7,allowed);assert(current==1024);
 current=0x1fff;allowed=current;PinWorker(8,allowed);assert(current==4096);
 switch2_cores=false;current=0x1fff;
 // Windows-style numbering: siblings in the upper half.
 for(unsigned i=0;i<7;++i) apics[i]=i*2;
 for(unsigned i=7;i<13;++i) apics[i]=(i-7)*2+1;
 CheckWorkerTopology();assert((worker_cpus==Cpus{0,1,2,3,4,-1,-1,5,6}) && current==0x1fff);
 assert(secondary_switch1==0x1c60 && secondary_switch2==0x1c60);
 for(unsigned i=0;i<64;++i) apics[i]=i;
 // A main thread started on fewer CPUs than its process still probes the process's set.
 current=3;CheckWorkerTopology();assert(worker_topology_ready && current==3);
 // ... unless the process set cannot be read.
 process_fails=true;CheckWorkerTopology();assert(!worker_topology_ready && current==3);
 process_fails=false;current=0x1fff;
 // A CPU the thread cannot move to is skipped, not the end of the probe.
 fail_mask=4;CheckWorkerTopology();assert(worker_topology_ready && current==0x1fff);
 assert((worker_cpus==Cpus{0,3,4,6,8,-1,-1,10,12}) && !(topology_allowed&4));
 fail_mask=0;
 // No usable CPUID topology (wrong level type, or one APIC ID for every CPU): adjacent pairs.
 level_type=0;CheckWorkerTopology();assert(worker_topology_ready && (worker_cpus==Cpus{0,2,4,6,8,-1,-1,10,12}));
 level_type=1;
 for(unsigned i=0;i<64;++i) apics[i]=0;
 CheckWorkerTopology();assert(worker_topology_ready && (worker_cpus==Cpus{0,2,4,6,8,-1,-1,10,12}));
 max_leaf=4;CheckWorkerTopology();assert(worker_topology_ready && (worker_cpus==Cpus{0,2,4,6,8,-1,-1,10,12}));
 max_leaf=0xb;
 for(unsigned i=0;i<64;++i) apics[i]=i;
 // Four physical cores are too few; six pin guest core 4 but not 5.
 process=current=0xff;CheckWorkerTopology();assert(!worker_topology_ready && secondary_switch1==0);
 process=current=0xfff;CheckWorkerTopology();assert(worker_topology_ready && (worker_cpus==Cpus{0,2,4,6,8,-1,-1,10,-1}));
 assert(secondary_switch1==0xe80 && secondary_switch2==0xe80);
 process=current=0x1fff;
 CheckWorkerTopology();fail_mask=256;allowed=current;PinWorker(4,allowed);assert(allowed==0x1fff && current==0x1fff);
 fail_mask=0;fail_restore=true;bool threw=false;
 try{CheckWorkerTopology();}catch(const std::runtime_error&){threw=true;}
 assert(threw);
 // Video can opt into the process CPU set; ordinary secondary workers remain restricted.
 fail_restore=false; process=current=0x1fff; CheckWorkerTopology();
 placement_secondary=false; PlaceSecondary("nx2:vdec"); assert(current==process);
 placement_secondary=true;
 const char* video_option=std::getenv("EDEN_NX2_VDEC_ALL_CORES");
 const bool video_all=video_option && video_option[0]=='1';
 PlaceSecondary("nx2:vdec");
 assert(current==(video_all ? process : secondary_switch1));
 current=process; PlaceSecondary("TextureDecoder"); assert(current==secondary_switch1);
 return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    exe=Path(tmp)/'affinity'
    subprocess.run(['c++','-std=c++20','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-x','c++','-','-o',str(exe)],input=code,text=True,check=True)
    for enabled in ("0", "1"):
        env=dict(os.environ, EDEN_NX2_VDEC_ALL_CORES=enabled)
        subprocess.run([str(exe)],check=True,env=env)
print('Worker topology: PASS physical sibling exclusion, OS/APIC permutations, process and narrow masks, '
      'skipped CPUs, pair fallback, Switch 2 guest cores 4-5, restoration failures and opt-in video placement')
