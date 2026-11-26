// Standard CDM10 to exact-fixture Mojo wire adapter. No content cryptography.
#pragma once
#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>
#include <vector>
#include "media/cdm/api/content_decryption_module.h"
#include "protocol_fixture.h"
namespace prime_wire {
using Bytes=std::vector<uint8_t>;
inline uint32_t U32(const uint8_t* p){uint32_t v;std::memcpy(&v,p,4);return v;}
inline uint64_t U64(const uint8_t* p){uint64_t v;std::memcpy(&v,p,8);return v;}
inline void Put32(Bytes& b,size_t p,uint32_t v){std::memcpy(b.data()+p,&v,4);}
inline void Put64(Bytes& b,size_t p,uint64_t v){std::memcpy(b.data()+p,&v,8);}
inline size_t Add(Bytes& b,uint32_t size){size_t p=b.size();b.resize(p+((size+7)&~size_t(7)),0);Put32(b,p,size);return p;}
inline void Ptr(Bytes& b,size_t p,size_t target){Put64(b,p,target-p);}
inline size_t Array(Bytes& b,const void* raw,uint32_t size){size_t p=Add(b,8+size);Put32(b,p+4,size);if(size)std::memcpy(b.data()+p+8,raw,size);return p;}
inline size_t String(Bytes& b,std::string_view s){return Array(b,s.data(),static_cast<uint32_t>(s.size()));}
inline Bytes Header(uint32_t method,uint32_t flags,uint64_t id,uint32_t size){
  Bytes b(size,0);Put32(b,0,32);Put32(b,4,1);Put32(b,12,method);Put32(b,16,flags);Put64(b,24,id);return b;
}
inline uint32_t Body(const Bytes& b){
  if(b.size()<24)return UINT32_MAX;uint32_t header=U32(b.data()),version=U32(b.data()+4);
  if(header<24||header>b.size())return UINT32_MAX;
  if(version<2)return header;
  if(header<48||U64(b.data()+32)>b.size()-32)return UINT32_MAX;
  return static_cast<uint32_t>(32+U64(b.data()+32));
}
inline bool Target(const Bytes& b,size_t at,uint32_t size,size_t& target){
  if(at>b.size()||b.size()-at<8)return false;uint64_t delta=U64(b.data()+at);
  if(!delta||delta>b.size()-at)return false;target=at+delta;return b.size()-target>=size;
}
inline Bytes BlobAt(const Bytes& b,size_t at){
  size_t p;if(!Target(b,at,8,p))return {};uint32_t n=U32(b.data()+p+4);
  if(n>b.size()-p-8)return {};return Bytes(b.begin()+p+8,b.begin()+p+8+n);
}
inline Bytes Certificate(uint64_t id,const uint8_t* data,uint32_t size){
  auto b=Header(kCdmSetCertificate,1,id,48);Put32(b,32,16);Ptr(b,40,Array(b,data,size));return b;
}
inline Bytes Generate(uint64_t id,cdm::SessionType session,cdm::InitDataType type,const uint8_t* data,uint32_t size){
  auto b=Header(kCdmGenerateRequest,1,id,56);Put32(b,32,24);Put32(b,40,static_cast<uint32_t>(session));
  Put32(b,44,type==cdm::kCenc?2:type==cdm::kWebM?1:3);Ptr(b,48,Array(b,data,size));return b;
}
inline Bytes Session(uint32_t method,uint64_t id,std::string_view session,const uint8_t* data=nullptr,uint32_t size=0){
  auto b=Header(method,1,id,method==kCdmUpdateSession?56:48);Put32(b,32,method==kCdmUpdateSession?24:16);
  Ptr(b,40,String(b,session));if(method==kCdmUpdateSession)Ptr(b,48,Array(b,data,size));return b;
}
inline Bytes VideoConfig(uint64_t id,const cdm::VideoDecoderConfig_2& config){
  auto b=Header(kDecryptorInitializeVideo,1,id,48);Put32(b,32,16);size_t root=Add(b,104);Ptr(b,40,root);
  Put32(b,root+8,config.codec==cdm::kCodecH264?1:config.codec==cdm::kCodecVp9?7:config.codec==cdm::kCodecAv1?10:0);
  int profile=static_cast<int>(config.profile);
  // Public CDM H264 baseline is2; Mojo media baseline is0.
  if(config.profile==cdm::kUnknownVideoCodecProfile)profile=-1;
  else if(config.codec==cdm::kCodecH264)profile-=static_cast<int>(cdm::kH264ProfileBaseline);
  else if(config.codec==cdm::kCodecVp9)profile=12+(profile-static_cast<int>(cdm::kVP9Profile0));
  else if(config.codec==cdm::kCodecAv1)profile=24+(profile-static_cast<int>(cdm::kAv1ProfileMain));
  Put32(b,root+12,static_cast<uint32_t>(profile));
  size_t transform=Add(b,16);Ptr(b,root+24,transform);
  size_t coded=Add(b,16);Ptr(b,root+32,coded);Put32(b,coded+8,config.coded_size.width);Put32(b,coded+12,config.coded_size.height);
  size_t rect=Add(b,24);Ptr(b,root+40,rect);Put32(b,rect+16,config.coded_size.width);Put32(b,rect+20,config.coded_size.height);
  size_t natural=Add(b,16);Ptr(b,root+48,natural);Put32(b,natural+8,config.coded_size.width);Put32(b,natural+12,config.coded_size.height);
  size_t aspect=Add(b,24);Ptr(b,root+56,aspect);Put32(b,aspect+8,1);double ratio=1.0;std::memcpy(b.data()+aspect+16,&ratio,8);
  Ptr(b,root+64,Array(b,config.extra_data,config.extra_data_size));Put32(b,root+72,static_cast<uint32_t>(config.encryption_scheme));
  size_t color=Add(b,24);Ptr(b,root+80,color);for(size_t f:{size_t(8),size_t(12),size_t(16),size_t(20)})Put32(b,color+f,1);
  Ptr(b,root+88,Add(b,32));Ptr(b,root+96,Add(b,16));return b;
}
inline Bytes Buffer(uint32_t method,uint64_t id,const cdm::InputBuffer_2& input){
  bool decode=method==kDecryptorDecodeVideo;
  auto b=Header(method,1,id,decode?56:64);Put32(b,32,decode?24:32);
  size_t union_at=decode?40:48;
  if(!decode)Put32(b,40,1);Put32(b,union_at,16);
  if(!input.data){size_t eos=Add(b,24);Ptr(b,union_at+8,eos);return b;}
  Put32(b,union_at+4,1);size_t body=Add(b,48);Ptr(b,union_at+8,body);
  size_t time=Add(b,16);Put64(b,time+8,input.timestamp);Ptr(b,body+8,time);
  size_t duration=Add(b,16);Ptr(b,body+16,duration);Put32(b,body+24,input.data_size);b[body+28]=1;
  if(input.encryption_scheme==cdm::EncryptionScheme::kUnencrypted)return b;
  size_t crypto=Add(b,48);Ptr(b,body+32,crypto);Put32(b,crypto+8,static_cast<uint32_t>(input.encryption_scheme));
  auto bytestring=[&](const uint8_t* data,uint32_t n){size_t at=Add(b,16);Ptr(b,at+8,Array(b,data,n));return at;};
  Ptr(b,crypto+16,bytestring(input.key_id,input.key_id_size));Ptr(b,crypto+24,bytestring(input.iv,input.iv_size));
  size_t entries=Add(b,8+input.num_subsamples*8);Put32(b,entries+4,input.num_subsamples);Ptr(b,crypto+32,entries);
  for(uint32_t i=0;i<input.num_subsamples;++i){size_t native=Add(b,24);Ptr(b,entries+8+i*8,native);uint32_t pair[2]={input.subsamples[i].clear_bytes,input.subsamples[i].cipher_bytes};Ptr(b,native+8,Array(b,pair,8));}
  if(input.encryption_scheme==cdm::EncryptionScheme::kCbcs){size_t pattern=Add(b,16);Ptr(b,crypto+40,pattern);Put32(b,pattern+8,input.pattern.crypt_byte_block);Put32(b,pattern+12,input.pattern.skip_byte_block);}
  return b;
}
} // namespace prime_wire
