// Genuine Chrome-hosted CDM initialization; no content session or provider I/O.
#pragma once
#include <psapi.h>
#include <map>

void ChildSecurity(HANDLE process,const char* stage) {
  Event(stage,"observing");
  HANDLE raw=nullptr;
  if (!OpenProcessToken(process,TOKEN_QUERY,&raw)) {Event("child_token_query","failure",GetLastError());return;}
  base::win::ScopedHandle token(raw);
  DWORD app=0,returned=0;
  Event("child_restricted_token","observed",IsTokenRestricted(raw)?1:0);
  if (GetTokenInformation(raw,TokenIsAppContainer,&app,sizeof(app),&returned))
    Event("child_appcontainer","observed",app);
  else Event("child_appcontainer","query_failure",GetLastError());
  HMODULE modules[256];DWORD bytes=0;bool found=false;
  if (K32EnumProcessModules(process,modules,sizeof(modules),&bytes) && bytes<=sizeof(modules)) {
    for (size_t i=0;i<bytes/sizeof(HMODULE);++i) {
      wchar_t name[MAX_PATH]={};
      if (K32GetModuleBaseNameW(process,modules[i],name,MAX_PATH) &&
          _wcsicmp(name,L"widevinecdm.dll")==0) found=true;
    }
    Event("actual_child_widevine_module",found?"loaded":"not_loaded");
  } else Event("actual_child_module_query","failure",GetLastError());
}

struct WireMessage {
  std::vector<uint8_t> bytes;
  std::vector<MojoHandle> handles;
  ~WireMessage(){for(auto handle:handles)if(handle!=MOJO_HANDLE_INVALID)MojoClose(handle);}
  MojoHandle Take(uint32_t index) {
    if(index>=handles.size())return MOJO_HANDLE_INVALID;
    auto result=handles[index];handles[index]=MOJO_HANDLE_INVALID;return result;
  }
};
MojoResult ReadWire(MojoHandle pipe,WireMessage& out) {
  MojoMessageHandle message=MOJO_MESSAGE_HANDLE_INVALID;
  MojoResult rc=MojoReadMessage(pipe,nullptr,&message);
  if(rc!=MOJO_RESULT_OK)return rc;
  void* data=nullptr;uint32_t size=0;uint32_t handle_count=32;
  out.handles.resize(handle_count);
  rc=MojoGetMessageData(message,nullptr,&data,&size,out.handles.data(),&handle_count);
  if(rc==MOJO_RESULT_OK) {
    out.handles.resize(handle_count);
    if(size>1024*1024)rc=MOJO_RESULT_RESOURCE_EXHAUSTED;
    else out.bytes.assign(static_cast<uint8_t*>(data),static_cast<uint8_t*>(data)+size);
  } else out.handles.clear();
  MojoDestroyMessage(message);
  return rc;
}
uint32_t AddString(std::vector<uint8_t>& bytes,std::string_view value) {
  uint32_t pos=static_cast<uint32_t>(bytes.size());
  bytes.resize(pos+((8+value.size()+7)&~size_t(7)),0);
  Put32(bytes.data()+pos,static_cast<uint32_t>(8+value.size()));
  Put32(bytes.data()+pos+4,static_cast<uint32_t>(value.size()));
  std::memcpy(bytes.data()+pos+8,value.data(),value.size());
  return pos;
}
bool OriginReply(MojoHandle pipe,const WireMessage& request) {
  // Generated synchronous responders preserve kFlagIsSync, otherwise Chrome's
  // blocking call cannot dispatch the reply while waiting for the result.
  auto bytes=Header(kFrameGetOrigin,2|4,U64(request.bytes.data()+24),88);
  Put32(bytes.data()+32,16);Put64(bytes.data()+40,8);
  Put32(bytes.data()+48,40);
  uint32_t scheme=AddString(bytes,"https");
  uint32_t host=AddString(bytes,"www.primevideo.com");
  Put64(bytes.data()+56,scheme-56);Put64(bytes.data()+64,host-64);
  uint16_t port=443;std::memcpy(bytes.data()+72,&port,2);
  // Explicit diagnostic origin; no nonce, borrowed frame identity or account ID.
  return SendBytes(pipe,bytes);
}

class RemoteCdmProbe {
 public:
  bool requested=false,initialized=false,failed=false;
  mojo::ScopedMessagePipeHandle remote_service,factory,frame,cdm,decryptor,output;
  std::map<uint32_t,std::pair<uint32_t,uint64_t>> output_pending;
  uint32_t next_output_rpc=1;
  int state=0;
  static MojoHandle Pair(mojo::ScopedMessagePipeHandle& local) {
    MojoHandle a=MOJO_HANDLE_INVALID,b=MOJO_HANDLE_INVALID;
    auto rc=MojoCreateMessagePipe(nullptr,&a,&b);
    if(rc!=MOJO_RESULT_OK){Event("cdm_diagnostic_pipe","failure",rc);return MOJO_HANDLE_INVALID;}
    local.reset(mojo::MessagePipeHandle(a));return b;
  }
  void Begin(MojoHandle broker,const base::FilePath& path) {
    requested=true;
    auto receiver=Pair(remote_service);
    if(receiver==MOJO_HANDLE_INVALID){failed=true;return;}
    const auto& text=path.value();
    auto bytes=Header(kGetCdmService,0,0,80+static_cast<uint32_t>((text.size()*2+7)&~size_t(7)));
    Put32(bytes.data()+32,24);Put64(bytes.data()+40,16);Put32(bytes.data()+48,0);
    Put32(bytes.data()+56,16);Put64(bytes.data()+64,8);
    Put32(bytes.data()+72,static_cast<uint32_t>(8+text.size()*2));
    Put32(bytes.data()+76,static_cast<uint32_t>(text.size()));
    std::memcpy(bytes.data()+80,text.data(),text.size()*2);
    if(!SendBytes(broker,bytes,{receiver})||!QueryVersion(remote_service.get().value(),43)){failed=true;return;}
    Event("actual_cdm_service_get","sent");state=1;
  }
  void OutputReply(uint32_t id,const std::vector<uint8_t>& payload) {
    auto found=output_pending.find(id);
    if(found==output_pending.end()||payload.size()!=12){Event("actual_output_rpc_reply","invalid");failed=true;return;}
    auto [ordinal,request_id]=found->second;
    auto reply=Header(ordinal,2,request_id,ordinal==kOutputQuery?56:48);
    Put32(reply.data()+32,ordinal==kOutputQuery?24:16);
    reply[40]=U32(payload.data())?1:0;
    if(ordinal==kOutputQuery) {
      Put32(reply.data()+44,U32(payload.data()+4));Put32(reply.data()+48,U32(payload.data()+8));
    }
    bool ok=SendBytes(output.get().value(),reply);
    Event("actual_output_rpc_reply",ok?"sent":"failure",id);
    output_pending.erase(found);if(!ok)failed=true;
  }
  void OutputTick() {
    if(!output.is_valid())return;
    WireMessage message;auto rc=ReadWire(output.get().value(),message);
    if(rc==MOJO_RESULT_SHOULD_WAIT)return;
    if(rc!=MOJO_RESULT_OK){Event("actual_output_service_pipe","failure",rc);failed=true;return;}
    uint32_t body=Payload(message.bytes);
    if(body==UINT32_MAX||message.bytes.size()<32){failed=true;return;}
    uint32_t ordinal=U32(message.bytes.data()+12);
    if(ordinal!=kOutputQuery&&ordinal!=kOutputEnable){Event("actual_output_request","unsupported",ordinal);failed=true;return;}
    if(ordinal==kOutputEnable&&message.bytes.size()-body<16){failed=true;return;}
    uint32_t id=next_output_rpc++;
    output_pending[id]={ordinal,U64(message.bytes.data()+24)};
    std::cout<<"{\"host_rpc\":\""<<(ordinal==kOutputQuery?"query_output":"enable_output")
             <<"\",\"rpc_id\":"<<id<<",\"desired_mask\":"
             <<(ordinal==kOutputEnable?U32(message.bytes.data()+body+8):0)<<"}\n";
    std::cout.flush();if(!std::cout.good())logging_failed=true;
  }
  void Tick(HANDLE child) {
    if(failed||!requested)return;
    if(frame.is_valid()) {
      WireMessage message;
      auto rc=ReadWire(frame.get().value(),message);
      if(rc==MOJO_RESULT_OK && message.bytes.size()>=32) {
        uint32_t ordinal=U32(message.bytes.data()+12);
        Event("actual_cdm_frame_callback_ordinal","observed",ordinal);
        if(ordinal==kFrameGetOrigin) {
          bool ok=OriginReply(frame.get().value(),message);
          Event("actual_cdm_origin_callback",ok?"responded":"failure");
          if(!ok)failed=true;
        } else if(ordinal==kFrameBindEmbedder) {
          uint32_t body=Payload(message.bytes);
          bool kept=false;
          if(body!=UINT32_MAX&&message.bytes.size()-body>=16) {
            uint64_t offset=U64(message.bytes.data()+body+8),receiver=body+8+offset;
            if(offset&&receiver<=message.bytes.size()&&message.bytes.size()-receiver>=24) {
              uint64_t name_offset=U64(message.bytes.data()+receiver+8),name=receiver+8+name_offset;
              if(name_offset&&name<=message.bytes.size()&&message.bytes.size()-name>=8) {
                uint32_t size=U32(message.bytes.data()+name+4);
                if(size<=message.bytes.size()-name-8&&size<=128) {
                  std::string_view type(reinterpret_cast<const char*>(message.bytes.data()+name+8),size);
                  if(type=="media.mojom.OutputProtection") {
                    auto handle=message.Take(U32(message.bytes.data()+receiver+16));
                    if(handle!=MOJO_HANDLE_INVALID){output.reset(mojo::MessagePipeHandle(handle));kept=true;}
                    Event("actual_output_service_bind",kept?"connected":"failure");
                  }
                }
              }
            }
          }
          if(!kept)Event("actual_optional_embedder_receiver","closed_unimplemented",message.handles.size());
        } else {Event("actual_unimplemented_frame_request","failure",ordinal);failed=true;}
      } else if(rc!=MOJO_RESULT_OK&&rc!=MOJO_RESULT_SHOULD_WAIT) {
        Event("actual_cdm_frame_pipe","failure",rc);failed=true;
      }
    }
    OutputTick();
    MojoHandle pipe=state==1?remote_service.get().value():state==2||state==3?factory.get().value():cdm.is_valid()?cdm.get().value():MOJO_HANDLE_INVALID;
    if(pipe==MOJO_HANDLE_INVALID||state>=5)return;
    WireMessage message;auto rc=ReadWire(pipe,message);
    if(rc==MOJO_RESULT_SHOULD_WAIT)return;
    if(rc!=MOJO_RESULT_OK){Event("actual_cdm_stage_pipe","failure",rc);failed=true;return;}
    if(state==1||state==2||state==4) {
      if(!IsVersionResponse(message.bytes,state==1?43:state==2?44:45)) {
        Event("actual_cdm_stage_version","unexpected_response",state);failed=true;return;
      }
      if(state==1) {
        ChildSecurity(child,"actual_cdm_service_security");
        auto factory_receiver=Pair(factory),frame_remote=Pair(frame);
        if(factory_receiver==MOJO_HANDLE_INVALID||frame_remote==MOJO_HANDLE_INVALID){failed=true;return;}
        auto bytes=Header(kCreateCdmFactory,0,0,56);
        Put32(bytes.data()+32,24);Put32(bytes.data()+40,0);Put32(bytes.data()+44,1);
        bool ok=SendBytes(remote_service.get().value(),bytes,{factory_receiver,frame_remote});
        Event("actual_cdm_factory_bind",ok?"sent":"failure");
        if(!ok||!QueryVersion(factory.get().value(),44)){failed=true;return;}state=2;
      } else if(state==2) {
        auto bytes=Header(kCreateCdm,1,46,72);
        Put32(bytes.data()+32,16);Put64(bytes.data()+40,8);Put32(bytes.data()+48,24);
        uint32_t name=AddString(bytes,"com.widevine.alpha");Put64(bytes.data()+56,name-56);
        bool ok=SendBytes(factory.get().value(),bytes);
        Event("actual_widevine_create_cdm",ok?"sent":"failure");
        if(!ok){failed=true;return;}state=3;
      } else {
        initialized=true;state=5;
        Event("actual_chrome_hosted_widevine","initialized");
        ChildSecurity(child,"actual_initialized_cdm_security");
      }
    } else if(state==3) {
      uint32_t body=Payload(message.bytes);
      if(body==UINT32_MAX||message.bytes.size()-body<32||U32(message.bytes.data()+12)!=kCreateCdm||
          U32(message.bytes.data()+16)!=2||U64(message.bytes.data()+24)!=46) {
        Event("actual_widevine_create_cdm","invalid_response");failed=true;return;
      }
      uint32_t status=U32(message.bytes.data()+body+24);
      Event("actual_widevine_create_cdm_status","observed",status);
      if(status!=0){failed=true;return;}
      auto handle=message.Take(U32(message.bytes.data()+body+8));
      if(handle==MOJO_HANDLE_INVALID){Event("actual_cdm_handle","missing");failed=true;return;}
      cdm.reset(mojo::MessagePipeHandle(handle));
      uint64_t context_delta=U64(message.bytes.data()+body+16);
      if(context_delta&&context_delta<=message.bytes.size()-body-16) {
        uint64_t context=body+16+context_delta;
        if(message.bytes.size()-context>=32) {
          auto decryptor_handle=message.Take(U32(message.bytes.data()+context+16));
          if(decryptor_handle!=MOJO_HANDLE_INVALID)decryptor.reset(mojo::MessagePipeHandle(decryptor_handle));
          Event("actual_cdm_decryptor_interface",decryptor.is_valid()?"available":"unavailable");
        }
      }
      if(!QueryVersion(cdm.get().value(),45)){failed=true;return;}state=4;
    }
  }
};
