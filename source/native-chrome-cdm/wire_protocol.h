// Small, checked transport adapter for the exact public Mojo IDL fixture.
// No CDM implementation, signature or provider response is substituted here.
#pragma once
#include <limits>
#include <string_view>
#include "protocol_fixture.h"

uint64_t U64(const uint8_t* p) { uint64_t v; std::memcpy(&v,p,8); return v; }
void Put64(uint8_t* p,uint64_t v) { std::memcpy(p,&v,8); }
uint32_t Payload(const std::vector<uint8_t>& bytes) {
  if (bytes.size()<24) return UINT32_MAX;
  uint32_t header=U32(bytes.data()),version=U32(bytes.data()+4);
  if (header>bytes.size() || header<24) return UINT32_MAX;
  if (version<2) return header;
  if (header<48) return UINT32_MAX;
  uint64_t offset=U64(bytes.data()+32);
  if (offset>bytes.size()-32) return UINT32_MAX;
  return static_cast<uint32_t>(32+offset);
}
bool SendBytes(MojoHandle pipe,const std::vector<uint8_t>& bytes,
               std::vector<MojoHandle> handles={}) {
  MojoMessageHandle message=MOJO_MESSAGE_HANDLE_INVALID;
  MojoResult rc=MojoCreateMessage(nullptr,&message);
  bool transferred=false;
  if (rc==MOJO_RESULT_OK) {
    MojoAppendMessageDataOptions options={sizeof(options),MOJO_APPEND_MESSAGE_DATA_FLAG_COMMIT_SIZE};
    void* buffer=nullptr;uint32_t capacity=0;
    rc=MojoAppendMessageData(message,static_cast<uint32_t>(bytes.size()),
        handles.empty()?nullptr:handles.data(),static_cast<uint32_t>(handles.size()),
        &options,&buffer,&capacity);
    if (rc==MOJO_RESULT_OK) {
      transferred=true;
      std::memcpy(buffer,bytes.data(),bytes.size());
      rc=MojoWriteMessage(pipe,message,nullptr);
      if (rc==MOJO_RESULT_OK) message=MOJO_MESSAGE_HANDLE_INVALID;
    }
  }
  if (message!=MOJO_MESSAGE_HANDLE_INVALID) MojoDestroyMessage(message);
  if (!transferred) for (auto handle:handles) MojoClose(handle);
  if (rc!=MOJO_RESULT_OK) Event("protocol_message_send","failure",rc);
  return rc==MOJO_RESULT_OK;
}
std::vector<uint8_t> Header(uint32_t ordinal,uint32_t flags,uint64_t request_id,
                            uint32_t size) {
  std::vector<uint8_t> bytes(size,0);
  Put32(bytes.data(),32);Put32(bytes.data()+4,1);
  Put32(bytes.data()+12,ordinal);Put32(bytes.data()+16,flags);
  Put64(bytes.data()+24,request_id);
  return bytes;
}
bool QueryVersion(MojoHandle pipe,uint64_t request_id) {
  // RunMessageParams: header8 + inline RunInput union16 + QueryVersion8.
  auto bytes=Header(0xffffffffu,1,request_id,64);
  Put32(bytes.data()+32,24);Put32(bytes.data()+40,16);
  Put64(bytes.data()+48,8);Put32(bytes.data()+56,8);
  return SendBytes(pipe,bytes);
}
bool IsVersionResponse(const std::vector<uint8_t>& bytes,uint64_t request_id) {
  auto body=Payload(bytes);
  if (body==UINT32_MAX || bytes.size()-body<24 || bytes.size()<32 ||
      U32(bytes.data()+12)!=0xffffffffu || U32(bytes.data()+16)!=2 ||
      U64(bytes.data()+24)!=request_id || U32(bytes.data()+body)<24) return false;
  // Mojo unions, including nullable unions, are inline16-byte values. A null
  // union is encoded with its size field zero, not an outer nullable pointer.
  uint64_t union_pos=body+8;
  if (bytes.size()-union_pos<16 || U32(bytes.data()+union_pos)!=16 ||
      U32(bytes.data()+union_pos+4)!=0) return false;
  uint64_t result_relative=U64(bytes.data()+union_pos+8);
  if (!result_relative || result_relative>bytes.size()-union_pos-8) return false;
  uint64_t result=union_pos+8+result_relative;
  if (bytes.size()-result<16 || U32(bytes.data()+result)<16) return false;
  Event("actual_interface_version","observed",U32(bytes.data()+result+8));
  return true;
}
bool BindService(MojoHandle control,MojoHandle receiver,std::string_view name) {
  // BindServiceInterface params16 -> GenericPendingReceiver24 -> string.
  uint32_t string_bytes=static_cast<uint32_t>((8+name.size()+7)&~size_t(7));
  auto bytes=Header(kBindCdmService,0,0,72+string_bytes);
  Put32(bytes.data()+32,16);Put64(bytes.data()+40,8);
  Put32(bytes.data()+48,24);Put64(bytes.data()+56,16);
  Put32(bytes.data()+64,0); // Transferred message pipe index.
  Put32(bytes.data()+72,static_cast<uint32_t>(8+name.size()));
  Put32(bytes.data()+76,static_cast<uint32_t>(name.size()));
  std::memcpy(bytes.data()+80,name.data(),name.size());
  return SendBytes(control,bytes,{receiver});
}
