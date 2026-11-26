// Smooth Prime standard CDM10 adapter for unmodified Kodi/InputStream Adaptive.
// Actual Google CDM/signatures remain in genuine Chrome. No host proof is made
// for this DLL, and no content keys are exposed or cryptography reimplemented.
#include <windows.h>
#include <wincrypt.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <map>
#include <mutex>
#include <thread>
#include "native_proxy_wire.h"
#include "native_windows_output.h"
#include "runtime_fixture.h"
using prime_wire::Bytes;
namespace {
std::wstring ModuleDirectory(){
  HMODULE module=nullptr;GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
      reinterpret_cast<LPCWSTR>(&ModuleDirectory),&module);
  wchar_t path[32768]={};DWORD length=GetModuleFileNameW(module,path,32768);
  std::wstring result(path,length);size_t slash=result.find_last_of(L"\\/");return result.substr(0,slash);
}
void Log(const char* stage,uint32_t code=0){
  static std::mutex lock;std::lock_guard<std::mutex> guard(lock);
  auto path=ModuleDirectory()+L"\\prime-cdm-proxy-diagnostics.jsonl";
  HANDLE file=CreateFileW(path.c_str(),FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
  if(file==INVALID_HANDLE_VALUE){OutputDebugStringW(L"Smooth Prime CDM diagnostic write failed\n");return;}
  std::string value="{\"operation\":\"normal_kodi_cdm_proxy\",\"stage\":\""+std::string(stage)+
      "\",\"code\":"+std::to_string(code)+",\"process\":"+std::to_string(GetCurrentProcessId())+
      ",\"tick_ms\":"+std::to_string(GetTickCount64())+",\"cdm_abi\":10,\"chrome_abi_fixture\":\""+kChromeRuntimeVersion+"\"}\n";
  DWORD written=0;if(!WriteFile(file,value.data(),static_cast<DWORD>(value.size()),&written,nullptr)||written!=value.size())
    OutputDebugStringW(L"Smooth Prime CDM diagnostic append failed\n");
  CloseHandle(file);
}
bool WriteAll(HANDLE pipe,const void* data,size_t size){
  const auto* next=static_cast<const uint8_t*>(data);
  while(size){DWORD count=0;if(!WriteFile(pipe,next,static_cast<DWORD>(std::min<size_t>(size,65536)),&count,nullptr)||!count)return false;next+=count;size-=count;}return true;
}
Bytes Base64(std::string_view line,std::string_view key){
  std::string marker="\""+std::string(key)+"\":\"";size_t start=line.find(marker);
  if(start==std::string_view::npos)return {};start+=marker.size();size_t end=line.find('"',start);
  if(end==std::string_view::npos||end-start>8*1024*1024)return {};
  DWORD size=0;if(!CryptStringToBinaryA(line.data()+start,static_cast<DWORD>(end-start),CRYPT_STRING_BASE64,nullptr,&size,nullptr,nullptr))return {};
  Bytes value(size);if(!CryptStringToBinaryA(line.data()+start,static_cast<DWORD>(end-start),CRYPT_STRING_BASE64,value.data(),&size,nullptr,nullptr))return {};value.resize(size);return value;
}
uint32_t JsonNumber(std::string_view line,const char* key){
  auto at=line.find(std::string("\"")+key+"\":");if(at==std::string_view::npos)return 0;
  at+=std::strlen(key)+3;while(at<line.size()&&line[at]==' ')++at;
  std::string numeric(line.substr(at,24));return static_cast<uint32_t>(std::strtoll(numeric.c_str(),nullptr,10));
}
bool ChromeFixture(){
  const auto path=ChromeRuntimeExecutable(ModuleDirectory());
  DWORD unused=0,size=GetFileVersionInfoSizeW(path.c_str(),&unused);if(!size)return false;Bytes bytes(size);
  if(!GetFileVersionInfoW(path.c_str(),0,size,bytes.data()))return false;
  VS_FIXEDFILEINFO* info=nullptr;UINT length=0;
  if(!VerQueryValueW(bytes.data(),L"\\",reinterpret_cast<void**>(&info),&length)||length<sizeof(*info))return false;
  return info->dwFileVersionMS==kChromeVersionMS&&info->dwFileVersionLS==kChromeVersionLS;
}
cdm::Status Status(uint32_t value,bool decode){return value==0?cdm::kSuccess:value==1?cdm::kNoKey:value==2?cdm::kNeedMoreData:decode?cdm::kDecodeError:cdm::kDecryptError;}
struct Pending{uint32_t promise=0,kind=0;};

class Proxy final:public cdm::ContentDecryptionModule_10 {
  cdm::Host_10* host;
  HANDLE input=nullptr,output=nullptr,process=nullptr,job=nullptr,mapping=nullptr,shutdown_event=nullptr;
  void* pixels=nullptr;
  std::thread reader;
  std::mutex write_mutex,state_mutex;
  std::condition_variable changed;
  std::map<uint64_t,Pending> promises;
  std::map<uint64_t,Bytes> replies;
  std::atomic<uint64_t> next_id{100};
  std::atomic<uint64_t> key_callbacks{0};
  std::atomic<bool> closed{false},destroyed{false};
  bool ready=false;
  uint64_t rendered=0,decrypted=0;
 public:
  explicit Proxy(cdm::Host_10* value):host(value){}
  ~Proxy() override {Shutdown();}
  bool Send(uint32_t op,const Bytes& data,uint32_t correlation=0){
    if(closed||destroyed||!input)return false;
    std::lock_guard<std::mutex> guard(write_mutex);uint32_t header[3]={op,correlation,static_cast<uint32_t>(data.size())};
    bool ok=WriteAll(input,header,sizeof(header))&&WriteAll(input,data.data(),data.size());if(!ok)Log("private_pipe_write_failed",GetLastError());return ok;
  }
  bool Start(){
    if(!ChromeFixture()){Log("chrome_abi_fixture_mismatch");return false;}
    SECURITY_ATTRIBUTES security={sizeof(security),nullptr,TRUE};
    HANDLE child_input=nullptr,child_output=nullptr;
    if(!CreatePipe(&child_input,&input,&security,65536)||!CreatePipe(&output,&child_output,&security,65536)){Log("private_pipe_create_failed",GetLastError());return false;}
    SetHandleInformation(input,HANDLE_FLAG_INHERIT,0);SetHandleInformation(output,HANDLE_FLAG_INHERIT,0);
    mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,&security,PAGE_READWRITE,0,16*1024*1024,nullptr);
    if(!mapping)return false;pixels=MapViewOfFile(mapping,FILE_MAP_READ,0,0,16*1024*1024);if(!pixels)return false;
    shutdown_event=CreateEventW(&security,TRUE,FALSE,nullptr);if(!shutdown_event)return false;
    job=CreateJobObjectW(nullptr,nullptr);JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits={};limits.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if(!job||!SetInformationJobObject(job,JobObjectExtendedLimitInformation,&limits,sizeof(limits)))return false;
    auto directory=ModuleDirectory();auto helper=directory+L"\\transport_probe.exe";
    auto profile=directory+L"\\native-profile-"+std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(GetTickCount64());
    if(!CreateDirectoryW(profile.c_str(),nullptr)&&GetLastError()!=ERROR_ALREADY_EXISTS)return false;
    std::wstring command=L"\""+helper+L"\" --transport-only --native-cdm-server --probe-profile=\""+profile+
        L"\" --cdm-path=\""+ChromeRuntimeCdm(directory)+L"\" --frame-mapping="+
        std::to_wstring(reinterpret_cast<uintptr_t>(mapping))+L" --shutdown-event="+
        std::to_wstring(reinterpret_cast<uintptr_t>(shutdown_event));
    STARTUPINFOEXW startup={};startup.StartupInfo.cb=sizeof(startup);startup.StartupInfo.dwFlags=STARTF_USESTDHANDLES|STARTF_USESHOWWINDOW;
    startup.StartupInfo.wShowWindow=SW_HIDE;startup.StartupInfo.hStdInput=child_input;startup.StartupInfo.hStdOutput=child_output;
    HANDLE errors=CreateFileW(L"NUL",GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,&security,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    startup.StartupInfo.hStdError=errors;
    SIZE_T bytes=0;InitializeProcThreadAttributeList(nullptr,1,0,&bytes);Bytes attributes(bytes);
    startup.lpAttributeList=reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributes.data());
    HANDLE inherited[5]={child_input,child_output,mapping,errors,shutdown_event};
    bool configured=InitializeProcThreadAttributeList(startup.lpAttributeList,1,0,&bytes)&&
        UpdateProcThreadAttribute(startup.lpAttributeList,0,PROC_THREAD_ATTRIBUTE_HANDLE_LIST,inherited,sizeof(inherited),nullptr,nullptr);
    PROCESS_INFORMATION info={};
    bool launched=configured&&CreateProcessW(helper.c_str(),command.data(),nullptr,nullptr,TRUE,
        CREATE_NO_WINDOW|CREATE_SUSPENDED|EXTENDED_STARTUPINFO_PRESENT,nullptr,directory.c_str(),&startup.StartupInfo,&info);
    DWORD error=GetLastError();if(configured)DeleteProcThreadAttributeList(startup.lpAttributeList);
    CloseHandle(child_input);CloseHandle(child_output);if(errors!=INVALID_HANDLE_VALUE)CloseHandle(errors);
    if(!launched){Log("native_helper_launch_failed",error);return false;}
    process=info.hProcess;
    if(!AssignProcessToJobObject(job,process)){Log("native_helper_job_failed",GetLastError());TerminateProcess(process,1);CloseHandle(info.hThread);return false;}
    ResumeThread(info.hThread);CloseHandle(info.hThread);
    reader=std::thread([this]{Read();});
    std::unique_lock<std::mutex> lock(state_mutex);
    changed.wait_for(lock,std::chrono::seconds(15),[this]{return ready||closed.load();});
    return ready&&!closed;
  }
  void Shutdown(){
    if(destroyed.exchange(true))return;
    auto started=GetTickCount64();Log("native_proxy_shutdown_started");changed.notify_all();
    // A dedicated event cannot queue behind media bytes in the synchronous pipe.
    if(shutdown_event)SetEvent(shutdown_event);
    if(input){CloseHandle(input);input=nullptr;}
    if(process&&WaitForSingleObject(process,1000)!=WAIT_OBJECT_0){
      Log("native_helper_shutdown_forced");
      if(job){CloseHandle(job);job=nullptr;}else TerminateProcess(process,0);
    }
    Log("native_helper_shutdown_elapsed",static_cast<uint32_t>(GetTickCount64()-started));
    if(reader.joinable()){CancelSynchronousIo(reader.native_handle());reader.join();}
    if(output)CloseHandle(output);if(process)CloseHandle(process);if(job)CloseHandle(job);
    if(pixels)UnmapViewOfFile(pixels);if(mapping)CloseHandle(mapping);
    if(shutdown_event)CloseHandle(shutdown_event);
    output=process=job=mapping=shutdown_event=nullptr;pixels=nullptr;
    Log("native_proxy_shutdown_elapsed",static_cast<uint32_t>(GetTickCount64()-started));
    Log("native_proxy_closed",static_cast<uint32_t>(rendered));
  }
  void Read(){
    WindowsOutput protection;std::string buffered;char chunk[16384];DWORD count=0;
    while(ReadFile(output,chunk,sizeof(chunk),&count,nullptr)&&count){
      buffered.append(chunk,count);if(buffered.size()>8*1024*1024){Log("private_record_size_limit");break;}
      size_t newline;
      while((newline=buffered.find('\n'))!=std::string::npos){std::string line=buffered.substr(0,newline);buffered.erase(0,newline+1);
        if(line.find("\"host_rpc\"")!=std::string::npos){
          auto started=GetTickCount64();
          auto facts=protection.Query(line.find("enable_output")!=std::string::npos,JsonNumber(line,"desired_mask"),&destroyed);
          Log("native_output_query_elapsed",static_cast<uint32_t>(GetTickCount64()-started));
          Log("actual_windows_output_result",static_cast<uint32_t>(protection.last));Send(2,facts,JsonNumber(line,"rpc_id"));
        }else if(line.find("\"private_cdm_ready\"")!=std::string::npos){std::lock_guard<std::mutex> lock(state_mutex);ready=true;changed.notify_all();}
        else if(line.find("\"private_cdm_raw\"")!=std::string::npos){auto message=Base64(line,"private_cdm_raw");if(!destroyed)CdmMessage(message);}
        else if(line.find("\"private_decryptor_raw\"")!=std::string::npos){auto message=Base64(line,"private_decryptor_raw");if(message.size()>=32){std::lock_guard<std::mutex> lock(state_mutex);replies[prime_wire::U64(message.data()+24)]=std::move(message);changed.notify_all();}}
        else if(line.find("\"private_video_frame\"")!=std::string::npos){Reply(Base64(line,"private_video_frame"));}
        else if(line.find("\"private_decrypted_buffer\"")!=std::string::npos){Reply(Base64(line,"private_decrypted_buffer"));}
        else if(line.find("\"private_")==std::string::npos){
          size_t stage=line.find("\"stage\":\"");
          if(stage!=std::string::npos){stage+=9;size_t end=line.find('"',stage);if(end!=std::string::npos&&end-stage<=80){
            std::string name=line.substr(stage,end-stage);
            if(name.find_first_not_of("abcdefghijklmnopqrstuvwxyz_0123456789")==std::string::npos)Log(name.c_str(),JsonNumber(line,"code"));
          }}
        }
        // Never log raw private records, challenge/license data or session IDs.
      }
    }
    auto closing=GetTickCount64();protection.Close();
    Log("native_output_close_elapsed",static_cast<uint32_t>(GetTickCount64()-closing));
    closed=true;changed.notify_all();Log("native_helper_pipe_closed");
  }
  void Reply(Bytes message){if(message.size()<8)return;std::lock_guard<std::mutex> lock(state_mutex);replies[prime_wire::U64(message.data())]=std::move(message);changed.notify_all();}
  Bytes Wait(uint64_t id){std::unique_lock<std::mutex> lock(state_mutex);changed.wait_for(lock,std::chrono::seconds(12),[&]{return replies.contains(id)||closed.load()||destroyed.load();});auto found=replies.find(id);if(found==replies.end()){Log(destroyed?"native_operation_cancelled":"native_operation_timeout");return {};}auto data=std::move(found->second);replies.erase(found);return data;}
  uint64_t Promise(uint32_t promise,uint32_t kind){uint64_t id=next_id++;std::lock_guard<std::mutex> lock(state_mutex);promises[id]={promise,kind};return id;}
  void CdmMessage(const Bytes& bytes){
    auto body=prime_wire::Body(bytes);if(body==UINT32_MAX||bytes.size()<32)return;
    uint32_t method=prime_wire::U32(bytes.data()+12),flags=prime_wire::U32(bytes.data()+16);
    if(flags&2){
      uint64_t id=prime_wire::U64(bytes.data()+24);Pending operation;
      {std::lock_guard<std::mutex> lock(state_mutex);auto found=promises.find(id);if(found==promises.end())return;operation=found->second;promises.erase(found);}
      size_t result=0;if(!prime_wire::Target(bytes,body+8,32,result))return;
      if(!bytes[result+8]){auto error=prime_wire::BlobAt(bytes,result+24);host->OnRejectPromise(operation.promise,cdm::kExceptionInvalidStateError,prime_wire::U32(bytes.data()+result+16),reinterpret_cast<const char*>(error.data()),static_cast<uint32_t>(error.size()));Log("actual_cdm_promise_rejected",prime_wire::U32(bytes.data()+result+12));return;}
      if(operation.kind==1){auto session=prime_wire::BlobAt(bytes,body+16);host->OnResolveNewSessionPromise(operation.promise,reinterpret_cast<const char*>(session.data()),static_cast<uint32_t>(session.size()));}
      else if(operation.kind==2&&bytes.size()-body>=24)host->OnResolveKeyStatusPromise(operation.promise,static_cast<cdm::KeyStatus>(prime_wire::U32(bytes.data()+body+16)));
      else host->OnResolvePromise(operation.promise);
    }else if(prime_wire::U32(bytes.data()+8)==0x80000001u){
      auto session=prime_wire::BlobAt(bytes,body+8);const char* sid=reinterpret_cast<const char*>(session.data());auto sid_size=static_cast<uint32_t>(session.size());
      if(method==kCdmOnMessage&&bytes.size()-body>=32){auto message=prime_wire::BlobAt(bytes,body+24);host->OnSessionMessage(sid,sid_size,static_cast<cdm::MessageType>(prime_wire::U32(bytes.data()+body+16)),reinterpret_cast<const char*>(message.data()),static_cast<uint32_t>(message.size()));Log("actual_cdm_message_forwarded",static_cast<uint32_t>(message.size()));}
      else if(method==kCdmOnKeysChange&&bytes.size()-body>=32){
        size_t array=0;if(!prime_wire::Target(bytes,body+24,8,array))return;uint32_t n=prime_wire::U32(bytes.data()+array+4);if(n>64||bytes.size()-array-8<n*8)return;
        std::vector<Bytes> ids;std::vector<cdm::KeyInformation> keys;ids.reserve(n);keys.reserve(n);
        for(uint32_t i=0;i<n;++i){size_t key=0;if(!prime_wire::Target(bytes,array+8+i*8,24,key))return;uint32_t status=prime_wire::U32(bytes.data()+key+16);if(status>6)continue;ids.push_back(prime_wire::BlobAt(bytes,key+8));keys.push_back({ids.back().data(),static_cast<uint32_t>(ids.back().size()),static_cast<cdm::KeyStatus>(status),prime_wire::U32(bytes.data()+key+20)});}
        host->OnSessionKeysChange(sid,sid_size,bytes[body+16]!=0,keys.data(),static_cast<uint32_t>(keys.size()));Log("actual_cdm_key_status_forwarded",static_cast<uint32_t>(keys.size()));
        ++key_callbacks;changed.notify_all();
      }else if(method==kCdmOnClosed)host->OnSessionClosed(sid,sid_size);
      else if(method==kCdmOnExpiry&&bytes.size()-body>=24){double expiry;std::memcpy(&expiry,bytes.data()+body+16,8);host->OnExpirationChange(sid,sid_size,expiry);}
    }
  }
  void Initialize(bool,bool,bool secure) override {bool ok=!secure&&Start();Log("actual_native_cdm_initialized",ok?0:1);host->OnInitialized(ok);}
  void GetStatusForPolicy(uint32_t promise,const cdm::Policy& policy) override {auto id=Promise(promise,2);auto b=prime_wire::Header(kCdmPolicy,1,id,48);prime_wire::Put32(b,32,16);prime_wire::Put32(b,40,static_cast<uint32_t>(policy.min_hdcp_version));Send(10,b);}
  void SetServerCertificate(uint32_t promise,const uint8_t* data,uint32_t size) override {auto id=Promise(promise,0);Send(10,prime_wire::Certificate(id,data,size));}
  void CreateSessionAndGenerateRequest(uint32_t promise,cdm::SessionType session,cdm::InitDataType type,const uint8_t* data,uint32_t size) override {auto id=Promise(promise,1);Send(10,prime_wire::Generate(id,session,type,data,size));}
  void LoadSession(uint32_t promise,cdm::SessionType type,const char* session,uint32_t size) override {auto id=Promise(promise,1);auto b=prime_wire::Header(kCdmLoadSession,1,id,56);prime_wire::Put32(b,32,24);prime_wire::Put32(b,40,type);prime_wire::Ptr(b,48,prime_wire::String(b,{session,size}));Send(10,b);}
  void UpdateSession(uint32_t promise,const char* session,uint32_t size,const uint8_t* data,uint32_t length) override {
    auto before=key_callbacks.load();auto id=Promise(promise,0);
    if(!Send(10,prime_wire::Session(kCdmUpdateSession,id,{session,size},data,length)))return;
    // Stock ISA reads its key map immediately on return. Preserve the native
    // callback ordering across the asynchronous Chrome pipe.
    std::unique_lock<std::mutex> lock(state_mutex);
    bool received=changed.wait_for(lock,std::chrono::seconds(12),[&]{return key_callbacks.load()!=before||closed.load()||destroyed.load();});
    Log(destroyed?"native_license_wait_cancelled":received&&key_callbacks.load()!=before?"actual_license_keys_ready_on_return":"native_license_key_callback_timeout");
  }
  void CloseSession(uint32_t promise,const char* session,uint32_t size) override {auto id=Promise(promise,0);Send(10,prime_wire::Session(kCdmCloseSession,id,{session,size}));}
  void RemoveSession(uint32_t promise,const char* session,uint32_t size) override {auto id=Promise(promise,0);Send(10,prime_wire::Session(kCdmRemoveSession,id,{session,size}));}
  void TimerExpired(void*) override {}
  cdm::Status Decrypt(const cdm::InputBuffer_2& input_buffer,cdm::DecryptedBlock* block) override {
    uint64_t id=next_id++;Bytes data;if(input_buffer.data_size)data.assign(input_buffer.data,input_buffer.data+input_buffer.data_size);
    if(!Send(13,data)||!Send(11,prime_wire::Buffer(kDecryptorDecrypt,id,input_buffer)))return cdm::kDecryptError;
    auto result=Wait(id);if(result.size()<24)return cdm::kDecryptError;auto status=Status(prime_wire::U32(result.data()+8),false);if(status!=cdm::kSuccess)return status;
    uint32_t size=prime_wire::U32(result.data()+20);if(size>result.size()-24)return cdm::kDecryptError;
    cdm::Buffer* buffer=host->Allocate(size);if(!buffer)return cdm::kDecryptError;std::memcpy(buffer->Data(),result.data()+24,size);buffer->SetSize(size);block->SetDecryptedBuffer(buffer);block->SetTimestamp(static_cast<int64_t>(prime_wire::U64(result.data()+12)));
    if(++decrypted%256==1)Log("actual_native_decrypted_buffer",size);return cdm::kSuccess;
  }
  cdm::Status InitializeAudioDecoder(const cdm::AudioDecoderConfig_2&) override {return cdm::kInitializationError;}
  cdm::Status InitializeVideoDecoder(const cdm::VideoDecoderConfig_2& config) override {
    Log("native_decoder_codec",config.codec);Log("native_decoder_profile",config.profile);
    Log("native_decoder_width",config.coded_size.width);Log("native_decoder_height",config.coded_size.height);
    Log("native_decoder_extra_bytes",config.extra_data_size);Log("native_decoder_encryption",static_cast<uint32_t>(config.encryption_scheme));
    uint64_t id=next_id++;if(!Send(11,prime_wire::VideoConfig(id,config)))return cdm::kInitializationError;auto response=Wait(id);auto body=prime_wire::Body(response);
    bool success=body!=UINT32_MAX&&response.size()-body>=16&&response[body+8];Log("actual_native_video_decoder_initialize",success?config.coded_size.width:0);return success?cdm::kSuccess:cdm::kInitializationError;
  }
  void DeinitializeDecoder(cdm::StreamType stream) override {uint64_t id=next_id++;auto b=prime_wire::Header(kDecryptorDeinitialize,0,id,48);prime_wire::Put32(b,32,16);prime_wire::Put32(b,40,stream);Send(11,b);}
  void ResetDecoder(cdm::StreamType stream) override {uint64_t id=next_id++;auto b=prime_wire::Header(kDecryptorReset,0,id,48);prime_wire::Put32(b,32,16);prime_wire::Put32(b,40,stream);Send(11,b);}
  cdm::Status DecryptAndDecodeFrame(const cdm::InputBuffer_2& input_buffer,cdm::VideoFrame* frame) override {
    uint64_t id=next_id++;Bytes data;if(input_buffer.data_size)data.assign(input_buffer.data,input_buffer.data+input_buffer.data_size);
    if((!data.empty()&&!Send(12,data))||!Send(11,prime_wire::Buffer(kDecryptorDecodeVideo,id,input_buffer)))return cdm::kDecodeError;
    auto result=Wait(id);if(result.size()<60)return cdm::kDecodeError;auto status=Status(prime_wire::U32(result.data()+8),true);if(status!=cdm::kSuccess)return status;
    uint32_t size=prime_wire::U32(result.data()+12),width=prime_wire::U32(result.data()+16),height=prime_wire::U32(result.data()+20),format=prime_wire::U32(result.data()+24);
    if(!size||size>16*1024*1024||!width||!height)return cdm::kDecodeError;
    cdm::VideoFormat native_format=format==1?cdm::kI420:format==2?cdm::kYv12:cdm::kUnknownVideoFormat;
    if(native_format==cdm::kUnknownVideoFormat)return cdm::kDecodeError;
    cdm::Buffer* buffer=host->Allocate(size);if(!buffer)return cdm::kDecodeError;std::memcpy(buffer->Data(),pixels,size);buffer->SetSize(size);frame->SetFrameBuffer(buffer);frame->SetFormat(native_format);frame->SetSize({static_cast<int32_t>(width),static_cast<int32_t>(height)});
    for(unsigned i=0;i<3;++i){frame->SetPlaneOffset(static_cast<cdm::VideoPlane>(i),prime_wire::U32(result.data()+28+i*4));frame->SetStride(static_cast<cdm::VideoPlane>(i),prime_wire::U32(result.data()+40+i*4));}
    frame->SetTimestamp(static_cast<int64_t>(prime_wire::U64(result.data()+52)));if(++rendered%120==1)Log("actual_native_video_frame",width);return cdm::kSuccess;
  }
  cdm::Status DecryptAndDecodeSamples(const cdm::InputBuffer_2&,cdm::AudioFrames*) override {return cdm::kDecodeError;}
  void OnPlatformChallengeResponse(const cdm::PlatformChallengeResponse&) override {}
  void OnQueryOutputProtectionStatus(cdm::QueryResult,uint32_t,uint32_t) override {}
  void OnStorageId(uint32_t,const uint8_t*,uint32_t) override {}
  void Destroy() override {delete this;}
};
} // namespace
extern "C" {
CDM_API void INITIALIZE_CDM_MODULE(){Log("native_proxy_module_loaded");}
CDM_API void DeinitializeCdmModule(){Log("native_proxy_module_unloaded");}
CDM_API const char* GetCdmVersion(){return "4.10.3112.0";} // Actual delegated CDM ABI fixture; this DLL is the proxy.
CDM_API void* CreateCdmInstance(int version,const char* key,uint32_t size,GetCdmHostFunc get_host,void* user){
  if(version!=10||std::string_view(key,size)!="com.widevine.alpha")return nullptr;
  auto* host=static_cast<cdm::Host_10*>(get_host(10,user));if(!host)return nullptr;return new Proxy(host);
}
}
