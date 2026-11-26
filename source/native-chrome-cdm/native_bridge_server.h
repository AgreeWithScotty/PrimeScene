// Private CDM transport for the normal Kodi ABI adapter. Google CDM remains
// only inside the genuine unchanged Chrome utility. No key extraction.
#pragma once
#include "mojo/public/c/system/buffer.h"

void PrivatePacket(const char* kind,const std::vector<uint8_t>& bytes) {
  std::cout<<"{\""<<kind<<"\":\""<<base::Base64Encode(bytes)<<"\"}\n";
  std::cout.flush();if(!std::cout.good())logging_failed=true;
}
void Append32(std::vector<uint8_t>& data,uint32_t value) {
  size_t at=data.size();data.resize(at+4);Put32(data.data()+at,value);
}
void Append64(std::vector<uint8_t>& data,uint64_t value) {
  size_t at=data.size();data.resize(at+8);Put64(data.data()+at,value);
}
bool PointerAt(const std::vector<uint8_t>& data,uint64_t at,uint32_t minimum,uint64_t& target) {
  if(at>data.size()||data.size()-at<8)return false;
  uint64_t delta=U64(data.data()+at);
  if(!delta||delta>data.size()-at)return false;
  target=at+delta;return data.size()-target>=minimum;
}
class NativeBridgeServer {
 public:
  bool ready=false,failed=false;
  std::vector<MojoHandle> pipes;
  void* frame_mapping=nullptr;
  static constexpr uint32_t kFrameCapacity=16*1024*1024;
  uint64_t pending_decrypt_id=0,pending_decrypt_time=0;
  uint32_t pending_decrypt_size=0;
  uint64_t frame_count=0;
  ~NativeBridgeServer() {
    for(auto handle:pipes)MojoClose(handle);
    if(frame_mapping)UnmapViewOfFile(frame_mapping);
  }
  void Begin(MojoHandle cdm,MojoHandle decryptor,HANDLE mapping) {
    frame_mapping=MapViewOfFile(mapping,FILE_MAP_WRITE,0,0,kFrameCapacity);
    if(!frame_mapping){Event("native_frame_mapping","failure",GetLastError());failed=true;return;}
    std::vector<uint8_t> client(80,0);
    Put32(client.data(),48);Put32(client.data()+4,2);Put32(client.data()+12,kCdmSetClient);
    Put64(client.data()+32,16);Put64(client.data()+40,24);
    Put32(client.data()+48,16);Put32(client.data()+64,12);Put32(client.data()+68,1);Put32(client.data()+72,0x80000001u);
    if(!SendBytes(cdm,client)){failed=true;return;}
    std::vector<MojoHandle> remote;
    for(unsigned i=0;i<4;++i) {
      MojoHandle producer=MOJO_HANDLE_INVALID,consumer=MOJO_HANDLE_INVALID;
      MojoCreateDataPipeOptions options={sizeof(options),0,1,4*1024*1024};
      auto rc=MojoCreateDataPipe(&options,&producer,&consumer);
      if(rc!=MOJO_RESULT_OK){for(auto h:remote)MojoClose(h);Event("native_data_pipe","failure",rc);failed=true;return;}
      pipes.push_back(i==3?consumer:producer);remote.push_back(i==3?producer:consumer);
    }
    auto setup=Header(kDecryptorInitialize,0,0,56);Put32(setup.data()+32,24);
    for(unsigned i=0;i<4;++i)Put32(setup.data()+40+i*4,i);
    if(!SendBytes(decryptor,setup,remote)){failed=true;return;}
    ready=true;PrivatePacket("private_cdm_ready",{1});Event("native_cdm_adapter_transport","ready");
  }
  void Command(uint32_t op,const std::vector<uint8_t>& payload,MojoHandle cdm,MojoHandle decryptor) {
    if(!ready||failed)return;
    if(op==10||op==11) {
      if(payload.size()<24||payload.size()>1024*1024){failed=true;return;}
      if(!SendBytes(op==10?cdm:decryptor,payload))failed=true;
    } else if(op==12||op==13||op==14) {
      uint32_t size=static_cast<uint32_t>(payload.size());
      MojoWriteDataOptions options={sizeof(options),MOJO_WRITE_DATA_FLAG_ALL_OR_NONE};
      auto rc=MojoWriteData(pipes[op==12?1:op==13?2:0],payload.data(),&size,&options);
      if(rc!=MOJO_RESULT_OK||size!=payload.size()){Event("native_encrypted_buffer_pipe","failure",rc);failed=true;}
    }
  }
  void Frame(WireMessage& message,uint32_t body) {
    const auto& data=message.bytes;
    std::vector<uint8_t> result;
    Append64(result,U64(data.data()+24));Append32(result,U32(data.data()+body+8));
    uint64_t frame=0,coded=0,storage=0,region=0,strides=0,offsets=0,time=0;
    bool valid=PointerAt(data,body+16,88,frame)&&PointerAt(data,frame+16,16,coded)&&
        U32(data.data()+frame+48)==16&&U32(data.data()+frame+52)==1&&
        PointerAt(data,frame+56,32,storage)&&PointerAt(data,storage+8,16,region)&&
        PointerAt(data,storage+16,20,strides)&&PointerAt(data,storage+24,20,offsets)&&
        PointerAt(data,frame+40,16,time);
    uint32_t length=0;
    if(valid) {
      uint32_t index=U32(data.data()+region+8);
      if(index>=message.handles.size())valid=false;
      else {
        MojoSharedBufferInfo info={sizeof(info),0};
        auto rc=MojoGetBufferInfo(message.handles[index],nullptr,&info);
        if(rc!=MOJO_RESULT_OK||!info.size||info.size>kFrameCapacity)valid=false;
        else {
          void* pixels=nullptr;rc=MojoMapBuffer(message.handles[index],0,info.size,nullptr,&pixels);
          if(rc!=MOJO_RESULT_OK)valid=false;
          else {length=static_cast<uint32_t>(info.size);std::memcpy(frame_mapping,pixels,length);MojoUnmapBuffer(pixels);}
        }
      }
    }
    Append32(result,length);
    Append32(result,valid?U32(data.data()+coded+8):0);Append32(result,valid?U32(data.data()+coded+12):0);
    Append32(result,valid?U32(data.data()+frame+8):0);
    for(unsigned i=0;i<3;++i)Append32(result,valid?U32(data.data()+offsets+8+i*4):0);
    for(unsigned i=0;i<3;++i)Append32(result,valid?U32(data.data()+strides+8+i*4):0);
    Append64(result,valid?U64(data.data()+time+8):0);
    PrivatePacket("private_video_frame",result);
    if(length&&++frame_count%240==1)Event("native_cdm_decoded_frame","copied",U32(data.data()+coded+8));
  }
  void Decrypted(WireMessage& message,uint32_t body) {
    const auto& data=message.bytes;uint32_t status=U32(data.data()+body+8);
    uint64_t buffer=0,time=0;
    if(status==0&&data.size()-body>=32&&U32(data.data()+body+16)==16&&U32(data.data()+body+20)==1&&
        PointerAt(data,body+24,48,buffer)&&PointerAt(data,buffer+8,16,time)) {
      pending_decrypt_id=U64(data.data()+24);pending_decrypt_time=U64(data.data()+time+8);
      pending_decrypt_size=U32(data.data()+buffer+24);
      if(pending_decrypt_size>4*1024*1024){failed=true;return;}
      if(pending_decrypt_size)return;
    }
    std::vector<uint8_t> response;Append64(response,U64(data.data()+24));Append32(response,status);
    Append64(response,pending_decrypt_time);Append32(response,0);PrivatePacket("private_decrypted_buffer",response);
    pending_decrypt_id=0;
  }
  void Tick(MojoHandle cdm,MojoHandle decryptor) {
    if(!ready||failed)return;
    for(unsigned pipe=0;pipe<2;++pipe) {
      WireMessage message;auto rc=ReadWire(pipe?decryptor:cdm,message);
      if(rc==MOJO_RESULT_SHOULD_WAIT)continue;
      if(rc!=MOJO_RESULT_OK){Event("native_cdm_response_pipe","failure",rc);failed=true;return;}
      uint32_t body=Payload(message.bytes);
      if(body==UINT32_MAX){failed=true;return;}
      if(!pipe)PrivatePacket("private_cdm_raw",message.bytes);
      else if(message.bytes.size()-body>=32&&U32(message.bytes.data()+12)==kDecryptorDecodeVideo)Frame(message,body);
      else if(message.bytes.size()-body>=16&&U32(message.bytes.data()+12)==kDecryptorDecrypt)Decrypted(message,body);
      else PrivatePacket("private_decryptor_raw",message.bytes);
    }
    if(pending_decrypt_id&&pending_decrypt_size) {
      std::vector<uint8_t> clear(pending_decrypt_size);uint32_t size=pending_decrypt_size;
      MojoReadDataOptions options={sizeof(options),MOJO_READ_DATA_FLAG_ALL_OR_NONE};
      auto rc=MojoReadData(pipes[3],&options,clear.data(),&size);
      if(rc==MOJO_RESULT_SHOULD_WAIT)return;
      if(rc!=MOJO_RESULT_OK||size!=pending_decrypt_size){Event("native_decrypted_buffer_pipe","failure",rc);failed=true;return;}
      std::vector<uint8_t> response;Append64(response,pending_decrypt_id);Append32(response,0);
      Append64(response,pending_decrypt_time);Append32(response,size);response.insert(response.end(),clear.begin(),clear.end());
      PrivatePacket("private_decrypted_buffer",response);pending_decrypt_id=0;pending_decrypt_size=0;
    }
  }
};
