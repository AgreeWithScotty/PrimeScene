// Real bounded IPC test against an unchanged official Chrome utility process.
// No CDM load, provider traffic, session creation, protected data or playback.
#include <cstdint>
#include <cstring>
#include <iostream>
#include <vector>
#include <windows.h>

#include "base/at_exit.h"
#include "base/command_line.h"
#include "base/feature_list.h"
#include "base/files/file_path.h"
#include "base/process/launch.h"
#include "base/process/process.h"
#include "base/threading/platform_thread.h"
#include "base/threading/thread.h"
#include "base/task/single_thread_task_executor.h"
#include "base/task/thread_pool/thread_pool_instance.h"
#include "base/win/scoped_handle.h"
#include "base/time/time.h"
#include "mojo/core/embedder/configuration.h"
#include "mojo/core/embedder/embedder.h"
#include "mojo/core/embedder/scoped_ipc_support.h"
#include "mojo/public/c/system/message_pipe.h"
#include "mojo/public/cpp/platform/platform_channel.h"
#include "mojo/public/cpp/system/invitation.h"

namespace {
base::TimeTicks start;
bool logging_failed = false;
void Event(const char* stage, const char* result, int code = 0) {
  std::cout << "{\"stage\":\"" << stage << "\",\"result\":\"" << result
            << "\",\"code\":" << code << ",\"elapsed_ms\":"
            << (base::TimeTicks::Now() - start).InMilliseconds() << "}\n";
  std::cout.flush();
  if (!std::cout.good()) logging_failed = true;
}
uint32_t U32(const uint8_t* p) { uint32_t v; std::memcpy(&v,p,4); return v; }
void Put32(uint8_t* p,uint32_t v) { std::memcpy(p,&v,4); }

// The exact fixture's ChildProcessHost has only one request expecting a reply:
// Ping() => (). Other calls bind remote interfaces without responses. Validate
// that shape; preserve its actual ordinal and request ID rather than guessing.
bool ReplyToPing(MojoHandle pipe, const void* raw, uint32_t size) {
  if (size<40) return false;
  const auto* p=static_cast<const uint8_t*>(raw);
  uint32_t header=U32(p),version=U32(p+4),flags=U32(p+16);
  if (header<32 || header>size || version<1 || flags!=1 || U32(p+8)!=0) return false;
  uint64_t payload=header;
  if (version>=2) {
    if (header<48) return false;
    uint64_t offset; std::memcpy(&offset,p+32,8);
    if (offset>size-32) return false;
    payload=32+offset;
  }
  if (payload>size || size-payload!=8 || U32(p+payload)!=8 || U32(p+payload+4)!=0)
    return false;
  uint8_t reply[40]={};
  Put32(reply,32); Put32(reply+4,1); Put32(reply+8,0);
  Put32(reply+12,U32(p+12)); Put32(reply+16,2); Put32(reply+20,U32(p+20));
  std::memcpy(reply+24,p+24,8); Put32(reply+32,8);
  MojoMessageHandle message=MOJO_MESSAGE_HANDLE_INVALID;
  MojoResult rc=MojoCreateMessage(nullptr,&message);
  if (rc!=MOJO_RESULT_OK) { Event("ping_reply_create","failure",rc); return false; }
  MojoAppendMessageDataOptions options={sizeof(options),MOJO_APPEND_MESSAGE_DATA_FLAG_COMMIT_SIZE};
  void* buffer=nullptr; uint32_t capacity=0;
  rc=MojoAppendMessageData(message,sizeof(reply),nullptr,0,&options,&buffer,&capacity);
  if (rc==MOJO_RESULT_OK) {
    std::memcpy(buffer,reply,sizeof(reply));
    rc=MojoWriteMessage(pipe,message,nullptr);
    if (rc==MOJO_RESULT_OK) message=MOJO_MESSAGE_HANDLE_INVALID;
  }
  if (message!=MOJO_MESSAGE_HANDLE_INVALID) MojoDestroyMessage(message);
  Event("actual_child_host_ping_reply",rc==MOJO_RESULT_OK?"sent":"failure",rc);
  return rc==MOJO_RESULT_OK;
}
}  // namespace

#include "wire_protocol.h"
#include "remote_cdm_probe.h"
#include "content_session_probe.h"
#include "decoder_probe.h"
#include "native_bridge_server.h"

int main(int argc,char** argv) {
  start=base::TimeTicks::Now();
  base::AtExitManager exit_manager;
  base::CommandLine::Init(argc,argv);
  if (!base::CommandLine::ForCurrentProcess()->HasSwitch("transport-only")) return 64;
  auto profile=base::CommandLine::ForCurrentProcess()->GetSwitchValuePath("probe-profile");
  if (profile.empty() || !profile.IsAbsolute()) return 64;
  Event("client_initialization","starting");
  base::FeatureList::InitInstance("","");
  base::SingleThreadTaskExecutor main_executor;
  base::ThreadPoolInstance::CreateAndStartWithDefaultParams("IPC probe");
  mojo::core::InitFeatures();
  mojo::core::Configuration configuration;
  configuration.is_broker_process=true;
  mojo::core::Init(configuration);
  base::Thread io("IPC client IO");
  if (!io.StartWithOptions(base::Thread::Options(base::MessagePumpType::IO,0))) return 65;
  bool ping_replied=false,alive_after_child_timeout=false;
  bool protocol_matches=false,control_version=false,broker_version=false,service_bound=false;
  bool server_mode=base::CommandLine::ForCurrentProcess()->HasSwitch("native-cdm-server");
  base::win::ScopedHandle frame_clock(server_mode?CreateWaitableTimerExW(nullptr,nullptr,
      CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,TIMER_MODIFY_STATE|SYNCHRONIZE):nullptr);
  if(server_mode)Event("native_transport_clock",frame_clock.is_valid()?"high_resolution":"fallback",GetLastError());
  bool content_probe=base::CommandLine::ForCurrentProcess()->HasSwitch("content-session");
  bool initialize_cdm=server_mode||content_probe||base::CommandLine::ForCurrentProcess()->HasSwitch("cdm-initialize");
  bool service_probe=initialize_cdm||base::CommandLine::ForCurrentProcess()->HasSwitch("service-rpc");
  auto cdm_path=base::CommandLine::ForCurrentProcess()->GetSwitchValuePath("cdm-path");
  if(initialize_cdm&&(cdm_path.empty()||!cdm_path.IsAbsolute()))return 64;
  RemoteCdmProbe cdm_probe;
  ContentSessionProbe session_probe;
  DecoderProbe decoder_probe;
  NativeBridgeServer native_server;
  auto mapping_text=base::CommandLine::ForCurrentProcess()->GetSwitchValueASCII("frame-mapping");
  HANDLE frame_mapping=reinterpret_cast<HANDLE>(static_cast<uintptr_t>(std::strtoull(mapping_text.c_str(),nullptr,10)));
  if(server_mode&&!frame_mapping){Event("native_frame_mapping_argument","invalid");return 64;}
  bool decode_init=base::CommandLine::ForCurrentProcess()->HasSwitch("decode-init");
  if(content_probe&&(!ReadBlob(session_probe.certificate)||!ReadBlob(session_probe.pssh))) {
    Event("private_cdm_input","failure");return 68;
  }
  if(decode_init&&!ReadBlob(decoder_probe.initialize_message)){Event("private_decoder_input","failure");return 68;}
  if(base::CommandLine::ForCurrentProcess()->HasSwitch("decode-frame")&&
      (!ReadBlob(decoder_probe.sample_message)||!ReadBlob(decoder_probe.sample_data))) {
    Event("private_encrypted_sample_input","failure");return 68;
  }
  unsigned messages=0,errors=0;
  {
    mojo::core::ScopedIPCSupport support(io.task_runner(),
        mojo::core::ScopedIPCSupport::ShutdownPolicy::FAST);
    mojo::PlatformChannel channel;
    mojo::OutgoingInvitation invitation;
    auto child_control=invitation.AttachMessagePipe(0u);
    auto child_host=invitation.AttachMessagePipe(1u);
    base::CommandLine command(base::FilePath(L"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"));
    command.AppendSwitchASCII("type","utility");
    command.AppendSwitchASCII("utility-sub-type","media.mojom.CdmServiceBroker");
    command.AppendSwitchASCII("service-sandbox-type","cdm");
    command.AppendSwitchASCII("lang","en-US");
    command.AppendSwitchPath("user-data-dir",profile);
    command.AppendSwitch("noerrdialogs");
    base::win::ScopedHandle job(::CreateJobObjectW(nullptr,nullptr));
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits={};
    limits.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!job.is_valid() || !::SetInformationJobObject(job.get(),JobObjectExtendedLimitInformation,
                                                     &limits,sizeof(limits))) return 67;
    base::LaunchOptions launch;
    launch.start_hidden=true;
    launch.job_handle=job.get();
    channel.PrepareToPassRemoteEndpoint(&launch,&command);
    base::Process child=base::LaunchProcess(command,launch);
    channel.RemoteProcessLaunchAttempted();
    if (!child.IsValid()) { Event("official_chrome_utility_launch","failure"); return 66; }
    Event("official_chrome_utility_launch","created");
    Event("owned_chrome_utility_pid","observed",child.Pid());
    if(initialize_cdm)ChildSecurity(child.Handle(),"actual_preload_child_security");
    mojo::OutgoingInvitation::Send(std::move(invitation),child.Handle(),channel.TakeLocalEndpoint());
    Event("mojo_invitation","sent");
    mojo::ScopedMessagePipeHandle service;
    if (service_probe) QueryVersion(child_control.get().value(),41);
    auto deadline=start+base::Seconds(server_mode?6*60*60:18);
    while (base::TimeTicks::Now()<deadline) {
      int exit_code;
      if (child.WaitForExitWithTimeout(base::TimeDelta(),&exit_code)) {
        Event("actual_child_exit_before_cleanup","exited",exit_code); break;
      }
      MojoMessageHandle message=MOJO_MESSAGE_HANDLE_INVALID;
      MojoResult rc=MojoReadMessage(child_host.get().value(),nullptr,&message);
      if (rc==MOJO_RESULT_OK) {
        ++messages; void* data=nullptr; uint32_t count=0;
        MojoGetMessageDataOptions options={sizeof(options),MOJO_GET_MESSAGE_DATA_FLAG_IGNORE_HANDLES};
        rc=MojoGetMessageData(message,&options,&data,&count,nullptr,nullptr);
        if (rc==MOJO_RESULT_OK && count<=1024*1024) {
          if (count>=24 && U32(static_cast<const uint8_t*>(data)+16)==1) {
            uint32_t ordinal=U32(static_cast<const uint8_t*>(data)+12);
            Event("actual_child_host_request_ordinal","observed",ordinal);
            protocol_matches=ordinal==kExpectedPing;
            Event("public_source_protocol_matches_binary",protocol_matches?"matched":"mismatch");
          }
          ping_replied=ReplyToPing(child_host.get().value(),data,count)||ping_replied;
        } else { ++errors; Event("host_message_decode","failure",rc); }
        MojoDestroyMessage(message);
      } else if (rc!=MOJO_RESULT_SHOULD_WAIT) {
        ++errors; Event("host_message_pipe","failure",rc); break;
      }
      if (service_probe) {
        auto inspect_version=[&](MojoHandle pipe,uint64_t id,const char* stage,bool& seen) {
          if (seen || pipe==MOJO_HANDLE_INVALID) return;
          MojoMessageHandle response=MOJO_MESSAGE_HANDLE_INVALID;
          MojoResult result=MojoReadMessage(pipe,nullptr,&response);
          if (result==MOJO_RESULT_SHOULD_WAIT) return;
          if (result!=MOJO_RESULT_OK) { ++errors;Event(stage,"pipe_failure",result);return; }
          void* raw=nullptr;uint32_t size=0;
          MojoGetMessageDataOptions options={sizeof(options),MOJO_GET_MESSAGE_DATA_FLAG_IGNORE_HANDLES};
          result=MojoGetMessageData(response,&options,&raw,&size,nullptr,nullptr);
          if (result==MOJO_RESULT_OK && size<=1024*1024) {
            std::vector<uint8_t> bytes(static_cast<uint8_t*>(raw),static_cast<uint8_t*>(raw)+size);
            seen=IsVersionResponse(bytes,id);
            Event(stage,seen?"response_valid":"unexpected_response",size);
            if (!seen) ++errors;
          } else { ++errors;Event(stage,"decode_failure",result); }
          MojoDestroyMessage(response);
        };
        inspect_version(child_control.get().value(),41,"actual_child_control_query",control_version);
        if (ping_replied && protocol_matches && control_version && !service_bound && !errors) {
          MojoHandle local=MOJO_HANDLE_INVALID,remote=MOJO_HANDLE_INVALID;
          rc=MojoCreateMessagePipe(nullptr,&local,&remote);
          if (rc==MOJO_RESULT_OK) {
            service.reset(mojo::MessagePipeHandle(local));
            service_bound=BindService(child_control.get().value(),remote,"media.mojom.CdmServiceBroker");
            Event("actual_cdm_broker_bind",service_bound?"sent":"failure");
            if (service_bound) QueryVersion(service.get().value(),42);
          } else { ++errors;Event("service_pipe_create","failure",rc); }
        }
        if (service_bound) inspect_version(service.get().value(),42,"actual_cdm_broker_query",broker_version);
        if(initialize_cdm&&broker_version&&!cdm_probe.requested&&!errors)
          cdm_probe.Begin(service.get().value(),cdm_path);
        if(initialize_cdm&&cdm_probe.requested)cdm_probe.Tick(child.Handle());
        if(content_probe||server_mode) {
          DWORD available=0;
          bool input_alive=PeekNamedPipe(GetStdHandle(STD_INPUT_HANDLE),nullptr,0,nullptr,&available,nullptr);
          if(server_mode&&!input_alive){Event("native_adapter_parent_pipe","closed");break;}
          if(input_alive&&available>=12) {
            uint32_t header[3]={};
            if(!ReadInput(header,12)||header[2]>4*1024*1024) {Event("runtime_input","invalid");++errors;break;}
            std::vector<uint8_t> payload(header[2]);
            if(header[2]&&!ReadInput(payload.data(),header[2])) {Event("runtime_input","truncated");++errors;break;}
            if(header[0]==1) {session_probe.pending_license=std::move(payload);session_probe.license_available=true;}
            else if(header[0]==2)cdm_probe.OutputReply(header[1],payload);
            else if(server_mode&&header[0]>=10&&header[0]<=14)
              native_server.Command(header[0],payload,cdm_probe.cdm.get().value(),cdm_probe.decryptor.get().value());
            else if(server_mode&&header[0]==99){Event("native_adapter_shutdown","requested");break;}
            else {Event("runtime_input","unknown_type",header[0]);++errors;break;}
          }
        }
        if(server_mode&&cdm_probe.initialized&&cdm_probe.decryptor.is_valid()) {
          if(!native_server.ready)native_server.Begin(cdm_probe.cdm.get().value(),cdm_probe.decryptor.get().value(),frame_mapping);
          native_server.Tick(cdm_probe.cdm.get().value(),cdm_probe.decryptor.get().value());
          if(native_server.failed){Event("native_adapter_server","failure");++errors;break;}
        }
        if(content_probe&&cdm_probe.initialized&&!session_probe.requested)
          session_probe.Begin(cdm_probe.cdm.get().value());
        if(content_probe&&session_probe.requested)
          session_probe.Tick(cdm_probe.cdm.get().value());
        if(decode_init&&session_probe.license_accepted&&session_probe.usable_keys>1&&cdm_probe.decryptor.is_valid()) {
          if(!decoder_probe.requested)decoder_probe.Begin(cdm_probe.decryptor.get().value());
          decoder_probe.Tick(cdm_probe.decryptor.get().value());
        }
      }
      if (ping_replied && base::TimeTicks::Now()-start>base::Seconds(17))
        alive_after_child_timeout=true;
      if(server_mode&&frame_clock.is_valid()) {
        LARGE_INTEGER interval;interval.QuadPart=-10000;
        if(SetWaitableTimer(frame_clock.get(),&interval,0,nullptr,nullptr,FALSE))WaitForSingleObject(frame_clock.get(),10);
        else Sleep(1);
      }else base::PlatformThread::Sleep(base::Milliseconds(server_mode?1:10));
    }
    Event("host_messages_observed","count",static_cast<int>(messages));
    Event("child_alive_after_default_ping_timeout",alive_after_child_timeout?"observed":"not_observed");
    int final_exit;
    if(child.WaitForExitWithTimeout(base::Milliseconds(100),&final_exit))Event("actual_child_exit_at_cleanup","exited",final_exit);
    if (child.IsRunning()) { child.Terminate(0,true); Event("owned_child_cleanup","terminated"); }
  }
  io.Stop();
  mojo::core::ShutDown();
  base::ThreadPoolInstance::Get()->Shutdown();
  Event("logging",logging_failed?"failure":"success");
  Event("transport_probe",ping_replied&&alive_after_child_timeout&&!errors?"connected":"not_connected");
  if (service_probe) Event("cdm_service_rpc",broker_version?"connected":"not_connected");
  if(initialize_cdm)Event("cdm_initialization_probe",cdm_probe.initialized?"initialized":"not_initialized");
  if(content_probe)Event("content_session_probe",session_probe.challenge_sent?"challenge_generated":"no_challenge");
  if(decode_init)Event("hd_decoder_probe",decoder_probe.initialized?"initialized":"not_initialized");
  return ping_replied&&alive_after_child_timeout&&!errors&&!logging_failed&&(!service_probe||broker_version)&&
      (!initialize_cdm||cdm_probe.initialized)?0:1;
}
