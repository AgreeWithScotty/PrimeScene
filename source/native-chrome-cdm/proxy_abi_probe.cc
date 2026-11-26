// Exercise the real CDM10 ABI DLL with real Amazon inputs. No mocked license,
// media or provider result; no Kodi/Chrome binary changes or on-screen playback.
#include <windows.h>
#include <wincrypt.h>
#include <atomic>
#include <chrono>
#include <cstring>
#include <iostream>
#include <mutex>
#include <thread>
#include "media/cdm/api/content_decryption_module.h"
#include "native_proxy_wire.h"
using prime_wire::Bytes;
namespace {
bool Input(void* data,uint32_t size){auto* next=static_cast<uint8_t*>(data);while(size){DWORD n=0;if(!ReadFile(GetStdHandle(STD_INPUT_HANDLE),next,size,&n,nullptr)||!n)return false;next+=n;size-=n;}return true;}
bool Blob(Bytes& data){uint32_t n=0;if(!Input(&n,4)||n>4*1024*1024)return false;data.resize(n);return !n||Input(data.data(),n);}
void Event(const char* stage,int64_t value){std::cout<<"{\"stage\":\""<<stage<<"\",\"value\":"<<value<<"}\n";std::cout.flush();}
struct Buffer:cdm::Buffer{
  Bytes data;uint32_t used=0;explicit Buffer(uint32_t size):data(size){}
  void Destroy()override{delete this;}uint32_t Capacity()const override{return static_cast<uint32_t>(data.size());}
  uint8_t* Data()override{return data.data();}void SetSize(uint32_t size)override{used=size;}
  uint32_t Size()const override{return used;}
};
struct Frame:cdm::VideoFrame{
  cdm::VideoFormat format=cdm::kUnknownVideoFormat;cdm::Size size={};cdm::Buffer* buffer=nullptr;
  uint32_t offsets[3]={},strides[3]={};int64_t time=0;
  ~Frame(){if(buffer)buffer->Destroy();}
  void SetFormat(cdm::VideoFormat value)override{format=value;}cdm::VideoFormat Format()const override{return format;}
  void SetSize(cdm::Size value)override{size=value;}cdm::Size Size()const override{return size;}
  void SetFrameBuffer(cdm::Buffer* value)override{buffer=value;}cdm::Buffer* FrameBuffer()override{return buffer;}
  void SetPlaneOffset(cdm::VideoPlane plane,uint32_t value)override{offsets[plane]=value;}
  uint32_t PlaneOffset(cdm::VideoPlane plane)override{return offsets[plane];}
  void SetStride(cdm::VideoPlane plane,uint32_t value)override{strides[plane]=value;}
  uint32_t Stride(cdm::VideoPlane plane)override{return strides[plane];}
  void SetTimestamp(int64_t value)override{time=value;}int64_t Timestamp()const override{return time;}
};
struct Host:cdm::Host_10{
  cdm::ContentDecryptionModule_10* module=nullptr;
  std::atomic<int> initialized{-1},rejected{0},resolved{0},usable{0};
  std::mutex mutex;Bytes challenge;std::string session;
  cdm::Buffer* Allocate(uint32_t size)override{return size&&size<=16*1024*1024?new Buffer(size):nullptr;}
  void SetTimer(int64_t,void*)override{} // Proxy owns its actual remote timers.
  cdm::Time GetCurrentWallTime()override{return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();}
  void OnInitialized(bool success)override{initialized=success?1:0;}
  void OnResolveKeyStatusPromise(uint32_t,cdm::KeyStatus)override{}
  void OnResolveNewSessionPromise(uint32_t,const char* id,uint32_t size)override{std::lock_guard<std::mutex> lock(mutex);session.assign(id,size);}
  void OnResolvePromise(uint32_t id)override{resolved=id;}
  void OnRejectPromise(uint32_t id,cdm::Exception exception,uint32_t code,const char*,uint32_t)override{rejected=id;Event("actual_proxy_promise_exception",exception);Event("actual_proxy_promise_system_code",code);}
  void OnSessionMessage(const char* id,uint32_t size,cdm::MessageType,const char* data,uint32_t n)override{std::lock_guard<std::mutex> lock(mutex);session.assign(id,size);challenge.assign(data,data+n);}
  void OnSessionKeysChange(const char*,uint32_t,bool,const cdm::KeyInformation* keys,uint32_t count)override{uint32_t n=0;for(uint32_t i=0;i<count;++i)if(keys[i].status==cdm::kUsable)++n;usable=n;}
  void OnExpirationChange(const char*,uint32_t,cdm::Time)override{}
  void OnSessionClosed(const char*,uint32_t)override{}
  void SendPlatformChallenge(const char*,uint32_t,const char*,uint32_t)override{}
  void EnableOutputProtection(uint32_t)override{}
  void QueryOutputProtectionStatus()override{if(module)module->OnQueryOutputProtectionStatus(cdm::kQueryFailed,0,0);}
  void OnDeferredInitializationDone(cdm::StreamType,cdm::Status)override{}
  cdm::FileIO* CreateFileIO(cdm::FileIOClient*)override{return nullptr;}
  void RequestStorageId(uint32_t version)override{if(module)module->OnStorageId(version,nullptr,0);}
  template<class Predicate>bool Wait(Predicate done){ULONGLONG limit=GetTickCount64()+15000;while(GetTickCount64()<limit){if(done())return true;if(rejected)return false;Sleep(5);}return false;}
};
void* GetHost(int version,void* data){return version==10?static_cast<cdm::Host_10*>(static_cast<Host*>(data)):nullptr;}
}
int main(){
  Bytes certificate,pssh,sample,key,iv,subsample_data;
  uint32_t config[3]={};int64_t time=0;
  if(!Blob(certificate)||!Blob(pssh)||!Input(config,sizeof(config))||!Blob(sample)||!Blob(key)||!Blob(iv)||!Blob(subsample_data)||!Input(&time,8))return 2;
  HMODULE library=LoadLibraryExW(L"widevinecdm.dll",nullptr,LOAD_LIBRARY_SEARCH_APPLICATION_DIR|LOAD_LIBRARY_SEARCH_SYSTEM32);
  if(!library){Event("proxy_dll_load_error",GetLastError());return 3;}
  using Init=void(*)();using Create=void*(*)(int,const char*,uint32_t,GetCdmHostFunc,void*);
  auto initialize=reinterpret_cast<Init>(GetProcAddress(library,"InitializeCdmModule_4"));
  auto deinitialize=reinterpret_cast<Init>(GetProcAddress(library,"DeinitializeCdmModule"));
  auto create=reinterpret_cast<Create>(GetProcAddress(library,"CreateCdmInstance"));
  if(!initialize||!deinitialize||!create)return 4;initialize();Host host;
  host.module=static_cast<cdm::ContentDecryptionModule_10*>(create(10,"com.widevine.alpha",18,GetHost,&host));
  if(!host.module)return 5;host.module->Initialize(false,false,false);
  if(!host.Wait([&]{return host.initialized.load()!=-1;})||host.initialized!=1){Event("actual_proxy_initialization",0);host.module->Destroy();return 6;}
  Event("actual_proxy_initialization",1);
  host.module->SetServerCertificate(1,certificate.data(),static_cast<uint32_t>(certificate.size()));
  if(!host.Wait([&]{return host.resolved.load()==1;})){host.module->Destroy();return 7;}
  host.module->CreateSessionAndGenerateRequest(2,cdm::kTemporary,cdm::kCenc,pssh.data(),static_cast<uint32_t>(pssh.size()));
  if(!host.Wait([&]{std::lock_guard<std::mutex> lock(host.mutex);return !host.challenge.empty()&&!host.session.empty();})){host.module->Destroy();return 8;}
  Bytes challenge;{std::lock_guard<std::mutex> lock(host.mutex);challenge=host.challenge;}
  DWORD encoded_size=0;CryptBinaryToStringA(challenge.data(),static_cast<DWORD>(challenge.size()),CRYPT_STRING_BASE64|CRYPT_STRING_NOCRLF,nullptr,&encoded_size);
  std::string encoded(encoded_size,'\0');CryptBinaryToStringA(challenge.data(),static_cast<DWORD>(challenge.size()),CRYPT_STRING_BASE64|CRYPT_STRING_NOCRLF,encoded.data(),&encoded_size);
  while(!encoded.empty()&&encoded.back()=='\0')encoded.pop_back();
  std::cout<<"{\"private_cdm_challenge\":\""<<encoded<<"\"}\n";std::cout.flush();Bytes license;
  if(!Blob(license)||license.empty()){host.module->Destroy();return 9;}
  host.module->UpdateSession(3,host.session.data(),static_cast<uint32_t>(host.session.size()),license.data(),static_cast<uint32_t>(license.size()));
  if(!host.Wait([&]{return host.resolved.load()==3&&host.usable.load()>1;})){host.module->Destroy();return 10;}
  Event("actual_proxy_usable_keys",host.usable);
  cdm::VideoDecoderConfig_2 video={};video.codec=cdm::kCodecH264;video.profile=static_cast<cdm::VideoCodecProfile>(config[2]+cdm::kH264ProfileBaseline);
  video.format=cdm::kI420;video.coded_size={static_cast<int32_t>(config[0]),static_cast<int32_t>(config[1])};video.encryption_scheme=cdm::EncryptionScheme::kCenc;
  auto status=host.module->InitializeVideoDecoder(video);Event("actual_proxy_decoder_initialize",status);
  bool success=false;
  if(status==cdm::kSuccess){
    std::vector<cdm::SubsampleEntry> subsamples(subsample_data.size()/8);
    if(subsample_data.size()%8){host.module->Destroy();return 11;}
    for(size_t i=0;i<subsamples.size();++i){subsamples[i].clear_bytes=prime_wire::U32(subsample_data.data()+i*8);subsamples[i].cipher_bytes=prime_wire::U32(subsample_data.data()+i*8+4);}
    cdm::InputBuffer_2 input={};input.data=sample.data();input.data_size=static_cast<uint32_t>(sample.size());input.encryption_scheme=cdm::EncryptionScheme::kCenc;
    input.key_id=key.data();input.key_id_size=static_cast<uint32_t>(key.size());input.iv=iv.data();input.iv_size=static_cast<uint32_t>(iv.size());input.subsamples=subsamples.data();input.num_subsamples=static_cast<uint32_t>(subsamples.size());input.timestamp=time;
    Frame frame;status=host.module->DecryptAndDecodeFrame(input,&frame);Event("actual_proxy_first_frame_status",status);
    if(status==cdm::kNeedMoreData){cdm::InputBuffer_2 eos={};status=host.module->DecryptAndDecodeFrame(eos,&frame);Event("actual_proxy_drain_status",status);}
    if(status==cdm::kSuccess&&frame.FrameBuffer()){
      Event("actual_proxy_decoded_width",frame.Size().width);Event("actual_proxy_decoded_height",frame.Size().height);Event("actual_proxy_frame_bytes",frame.FrameBuffer()->Size());
      bool nonzero=false;for(uint32_t i=0;i<frame.FrameBuffer()->Size();++i)if(frame.FrameBuffer()->Data()[i]){nonzero=true;break;}
      Event("actual_proxy_frame_data_present",nonzero?1:0);success=frame.Size().width>=1280&&nonzero;
    }
  }
  host.module->CloseSession(4,host.session.data(),static_cast<uint32_t>(host.session.size()));
  host.module->Destroy();host.module=nullptr;deinitialize();FreeLibrary(library);Event("actual_proxy_abi_hd_frame",success?1:0);return success?0:12;
}
