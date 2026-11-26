// Real temporary EME session. The sensitive challenge leaves only via the
// private parent pipe; the Python driver filters it before saving diagnostics.
#pragma once
#include "base/base64.h"

bool ReadInput(void* data,uint32_t size) {
  auto* next=static_cast<uint8_t*>(data);
  while(size) {
    DWORD count=0;
    if(!ReadFile(GetStdHandle(STD_INPUT_HANDLE),next,size,&count,nullptr)||!count)return false;
    next+=count;size-=count;
  }
  return true;
}
bool ReadBlob(std::vector<uint8_t>& bytes) {
  uint32_t size=0;
  if(!ReadInput(&size,4)||!size||size>1024*1024)return false;
  bytes.resize(size);return ReadInput(bytes.data(),size);
}
bool IndirectBytes(const std::vector<uint8_t>& bytes,uint64_t at,std::vector<uint8_t>& out) {
  if(at>bytes.size()||bytes.size()-at<8)return false;
  uint64_t delta=U64(bytes.data()+at);
  if(!delta||delta>bytes.size()-at)return false;
  uint64_t pos=at+delta;
  if(bytes.size()-pos<8)return false;
  uint32_t count=U32(bytes.data()+pos+4),size=U32(bytes.data()+pos);
  if(size<8||size>bytes.size()-pos||count>size-8)return false;
  out.assign(bytes.begin()+pos+8,bytes.begin()+pos+8+count);return true;
}
class ContentSessionProbe {
 public:
  std::vector<uint8_t> certificate,pssh;
  std::vector<uint8_t> session_id;
  bool requested=false,challenge_sent=false,failed=false,license_received=false,license_accepted=false;
  uint32_t usable_keys=0;
  bool license_available=false;
  std::vector<uint8_t> pending_license;
  int state=0;
  bool Begin(MojoHandle pipe) {
    requested=true;
    // Remote<> owns the high interface-ID namespace; the actual Chrome
    // Receiver<> owns the low namespace. Associate exactly one callback ID.
    std::vector<uint8_t> bytes(80,0);
    Put32(bytes.data(),48);Put32(bytes.data()+4,2);Put32(bytes.data()+12,kCdmSetClient);
    Put64(bytes.data()+32,16);Put64(bytes.data()+40,24);
    Put32(bytes.data()+48,16);Put32(bytes.data()+56,0);
    Put32(bytes.data()+64,12);Put32(bytes.data()+68,1);Put32(bytes.data()+72,0x80000001u);
    if(!SendBytes(pipe,bytes)){failed=true;return false;}
    bytes=Header(kCdmSetCertificate,1,51,56+static_cast<uint32_t>((certificate.size()+7)&~size_t(7)));
    Put32(bytes.data()+32,16);Put64(bytes.data()+40,8);
    Put32(bytes.data()+48,static_cast<uint32_t>(8+certificate.size()));
    Put32(bytes.data()+52,static_cast<uint32_t>(certificate.size()));
    std::memcpy(bytes.data()+56,certificate.data(),certificate.size());
    bool ok=SendBytes(pipe,bytes);Event("actual_chrome_cdm_certificate",ok?"sent":"failure");
    state=1;if(!ok)failed=true;return ok;
  }
  bool Promise(const std::vector<uint8_t>& bytes,uint32_t body,const char* stage) {
    if(body==UINT32_MAX||bytes.size()-body<16)return false;
    uint64_t delta=U64(bytes.data()+body+8);
    if(!delta||delta>bytes.size()-body-8)return false;
    uint64_t at=body+8+delta;
    if(bytes.size()-at<32||U32(bytes.data()+at)<32)return false;
    bool success=bytes[at+8]!=0;
    Event(stage,success?"accepted":"rejected",U32(bytes.data()+at+12));
    if(!success)Event("actual_cdm_promise_system_code","observed",U32(bytes.data()+at+16));
    return success;
  }
  void Tick(MojoHandle pipe) {
    if(failed)return;
    if(challenge_sent&&!license_received) {
      if(license_available) {
        auto license=std::move(pending_license);
        license_received=true;
        if(license.empty()||session_id.empty()){Event("actual_cdm_license_input","unavailable");failed=true;return;}
        auto request=Header(kCdmUpdateSession,1,53,56);
        Put32(request.data()+32,24);
        uint32_t id=AddString(request,std::string_view(reinterpret_cast<const char*>(session_id.data()),session_id.size()));
        uint32_t data=static_cast<uint32_t>(request.size());
        request.resize(data+((8+license.size()+7)&~size_t(7)),0);
        Put64(request.data()+40,id-40);Put64(request.data()+48,data-48);
        Put32(request.data()+data,static_cast<uint32_t>(8+license.size()));
        Put32(request.data()+data+4,static_cast<uint32_t>(license.size()));
        std::memcpy(request.data()+data+8,license.data(),license.size());
        bool ok=SendBytes(pipe,request);Event("actual_chrome_cdm_license_update",ok?"sent":"failure");
        if(!ok){failed=true;return;}
      }
    }
    WireMessage message;auto rc=ReadWire(pipe,message);
    if(rc==MOJO_RESULT_SHOULD_WAIT)return;
    if(rc!=MOJO_RESULT_OK){Event("actual_eme_message_pipe","failure",rc);failed=true;return;}
    auto& bytes=message.bytes;uint32_t body=Payload(bytes);
    if(body==UINT32_MAX||bytes.size()<32){failed=true;return;}
    uint32_t ordinal=U32(bytes.data()+12),flags=U32(bytes.data()+16),interface_id=U32(bytes.data()+8);
    if(ordinal==kCdmSetCertificate&&flags==2&&U64(bytes.data()+24)==51) {
      if(!Promise(bytes,body,"actual_chrome_cdm_certificate")){failed=true;return;}
      auto request=Header(kCdmGenerateRequest,1,52,64+static_cast<uint32_t>((pssh.size()+7)&~size_t(7)));
      Put32(request.data()+32,24);Put32(request.data()+40,0);Put32(request.data()+44,2);
      Put64(request.data()+48,8);Put32(request.data()+56,static_cast<uint32_t>(8+pssh.size()));
      Put32(request.data()+60,static_cast<uint32_t>(pssh.size()));
      std::memcpy(request.data()+64,pssh.data(),pssh.size());
      bool ok=SendBytes(pipe,request);Event("actual_chrome_cdm_generate_request",ok?"sent":"failure");
      state=2;if(!ok)failed=true;
    } else if(ordinal==kCdmGenerateRequest&&flags==2&&U64(bytes.data()+24)==52) {
      if(!Promise(bytes,body,"actual_chrome_cdm_session"))failed=true;
    } else if(ordinal==kCdmUpdateSession&&flags==2&&U64(bytes.data()+24)==53) {
      license_accepted=Promise(bytes,body,"actual_chrome_cdm_license_update");
      if(!license_accepted)failed=true;
    } else if(interface_id==0x80000001u&&ordinal==kCdmOnMessage&&bytes.size()-body>=32) {
      std::vector<uint8_t> challenge;
      if(!IndirectBytes(bytes,body+8,session_id)){failed=true;return;}
      if(!IndirectBytes(bytes,body+24,challenge)||challenge.empty()||challenge.size()>1024*1024) {failed=true;return;}
      Event("actual_chrome_cdm_license_message","generated",challenge.size());
      // This record is never persisted, displayed, or included in a report by
      // the driver. It is a pipe-only CDM message, unchanged, for Amazon.
      std::cout<<"{\"private_cdm_challenge\":\""<<base::Base64Encode(challenge)<<"\"}\n";
      std::cout.flush();challenge_sent=true;
      if(!std::cout.good())logging_failed=true;
    } else if(interface_id==0x80000001u&&ordinal==kCdmOnKeysChange&&bytes.size()-body>=32) {
      uint64_t delta=U64(bytes.data()+body+24);
      if(!delta||delta>bytes.size()-body-24){failed=true;return;}
      uint64_t array=body+24+delta;
      if(bytes.size()-array<8){failed=true;return;}
      uint32_t count=U32(bytes.data()+array+4);
      if(count>64||bytes.size()-array-8<count*8){failed=true;return;}
      uint32_t statuses[8]={};
      for(uint32_t i=0;i<count;++i) {
        uint64_t at=array+8+8*i,offset=U64(bytes.data()+at);
        if(!offset||offset>bytes.size()-at||bytes.size()-at-offset<24){failed=true;return;}
        uint32_t status=U32(bytes.data()+at+offset+16);
        if(status<8)++statuses[status];
      }
      usable_keys=statuses[0];
      Event("actual_chrome_cdm_usable_keys","count",usable_keys);
      Event("actual_chrome_cdm_output_restricted_keys","count",statuses[3]);
      Event("actual_chrome_cdm_output_downscaled_keys","count",statuses[4]);
    } else Event("actual_eme_unhandled_message_ordinal","observed",ordinal);
  }
};
